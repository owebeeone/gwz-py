//! Loaded-image based installed descriptor. No Python attributes or runtime env.
use gwz_sspi::{ErrorKind, WorkerExecutable};
use std::path::{Path, PathBuf};
fn unavailable() -> ErrorKind {
    ErrorKind::WorkerUnavailable
}
fn absolute_image(path: PathBuf) -> Result<PathBuf, ErrorKind> {
    if !path.is_absolute() {
        return Err(unavailable());
    }
    Ok(path)
}
fn anchor() -> &'static str {
    option_env!("GWZ_SSPI_BUILD_FINGERPRINT").unwrap_or("")
}
pub(crate) fn descriptor() -> Result<WorkerExecutable, ErrorKind> {
    descriptor_for(&image::path()?, option_env!("GWZ_SSPI_BUILD_FINGERPRINT"))
}
/// The worker beside the loaded extension `image`, for the build whose compiled fingerprint is `compiled`. The
/// image must be an absolute path to an extension module; the worker is the fixed adjacent file and nothing a
/// search finds.
fn descriptor_for(image: &Path, compiled: Option<&str>) -> Result<WorkerExecutable, ErrorKind> {
    if !image.is_absolute()
        || !image
            .extension()
            .is_some_and(|extension| extension == "so" || extension == "pyd")
    {
        return Err(unavailable());
    }
    gwz_sspi::packaging::installed_worker(image, compiled).map_err(|error| error.kind())
}
cfg_if::cfg_if! {
    if #[cfg(windows)] {
        mod image {
            use super::*;
            use std::os::windows::ffi::OsStringExt;
            #[link(name="kernel32")]
            unsafe extern "system"{
                fn GetModuleHandleExW(flags:u32,address:*const u16,module:*mut *mut std::ffi::c_void)->i32;
                fn GetModuleFileNameW(module:*mut std::ffi::c_void,path:*mut u16,size:u32)->u32;
            }
            pub(super) fn path()->Result<PathBuf,ErrorKind>{
                let mut module=std::ptr::null_mut();
                let mut words=vec![0u16;32768];
                // SAFETY: FROM_ADDRESS uses the extension-local anchor address.
                // Executing this loaded extension retains its image throughout
                // lookup; UNCHANGED_REFCOUNT borrows it, never FreeLibrary.
                if unsafe {GetModuleHandleExW(4|2,anchor as *const () as *const u16,&mut module)}==0{return Err(unavailable());}
                // SAFETY: borrowed live image and fixed initialized UTF16 output.
                let length=unsafe{GetModuleFileNameW(module,words.as_mut_ptr(),words.len() as u32)} as usize;
                if length==0 || length>=words.len(){return Err(unavailable());}
                absolute_image(PathBuf::from(std::ffi::OsString::from_wide(&words[..length])))
            }
        }
    } else if #[cfg(unix)] {
        mod image {
            use super::*;
            use std::ffi::{c_char,c_void,CStr};
            use std::os::unix::ffi::OsStringExt;
            #[repr(C)]
            struct Info{filename:*const c_char,base:*mut c_void,name:*const c_char,address:*mut c_void}
            cfg_if::cfg_if! {
                if #[cfg(target_os="linux")] {
                    #[link(name="dl")]
                    unsafe extern "C"{fn dladdr(address:*const c_void,info:*mut Info)->i32;}
                } else {
                    unsafe extern "C"{fn dladdr(address:*const c_void,info:*mut Info)->i32;}
                }
            }
            pub(super) fn path()->Result<PathBuf,ErrorKind>{
                let mut info=Info{filename:std::ptr::null(),base:std::ptr::null_mut(),name:std::ptr::null(),address:std::ptr::null_mut()};
                // SAFETY: actual extension-local function address, initialized
                // four-pointer Dl_info, image remains loaded while executing.
                if unsafe{dladdr(anchor as *const () as *const c_void,&mut info)}==0 || info.filename.is_null(){return Err(unavailable());}
                // SAFETY: successful dladdr reports image-owned NUL-terminated
                // filename; copy while the executing image is retained.
                let bytes=unsafe{CStr::from_ptr(info.filename)}.to_bytes();
                let path=absolute_image(PathBuf::from(std::ffi::OsString::from_vec(bytes.to_vec())))?;
                std::fs::canonicalize(path).map_err(|_|unavailable())
            }
        }
    } else {
        mod image {use super::*;pub(super) fn path()->Result<PathBuf,ErrorKind>{Err(ErrorKind::UnsupportedPlatform)}}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn relative_loader_path_refuses_before_any_cwd_resolution() {
        assert_eq!(
            absolute_image(PathBuf::from("Cargo.toml")),
            Err(ErrorKind::WorkerUnavailable)
        );
        let absolute = std::env::current_exe().unwrap();
        assert_eq!(absolute_image(absolute.clone()).unwrap(), absolute);
        assert_eq!(descriptor().err(), Some(ErrorKind::WorkerUnavailable));
    }

    /// An install directory under the system temporary directory, removed when dropped.
    struct Scratch(PathBuf);
    impl Scratch {
        fn new(label: &str) -> Self {
            static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "gwz-py-worker-host-{}-{}-{label}",
                std::process::id(),
                NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    const WORKER: &str = if cfg!(windows) {
        "gwz-sspi-worker.exe"
    } else {
        "gwz-sspi-worker"
    };

    #[test]
    fn the_wheels_worker_is_the_file_beside_the_extension_wherever_the_wheel_is_installed() {
        // WH3 installed paths (step 4.10): site-packages under a directory with spaces or non-ASCII characters.
        let build = "42".repeat(32);
        for directory in [
            vec!["Python 3.13", "Lib", "site-packages", "gwz"],
            vec!["\u{fc}ml\u{e4}ut \u{65e5}\u{672c}\u{8a9e}", "venv", "gwz"],
            vec!["with spaces", "\u{43f}\u{430}\u{43f}\u{43a}\u{430}", "gwz"],
        ] {
            let scratch = Scratch::new("installed");
            let dir = directory
                .iter()
                .fold(scratch.0.clone(), |path, part| path.join(part));
            std::fs::create_dir_all(&dir).unwrap();
            let image = dir.join("_gwz_core.pyd");
            std::fs::write(&image, b"image").unwrap();
            let worker = dir.join(WORKER);
            std::fs::write(&worker, b"worker").unwrap();
            let descriptor = descriptor_for(&image, Some(&build)).unwrap();
            assert_eq!(descriptor.path(), worker.as_path(), "{directory:?}");
        }
    }

    #[test]
    fn a_missing_worker_an_unprovisioned_build_and_a_foreign_image_have_no_descriptor() {
        let build = "42".repeat(32);
        let scratch = Scratch::new("missing");
        let dir = scratch.0.join("with spaces").join("gwz");
        std::fs::create_dir_all(&dir).unwrap();
        let image = dir.join("_gwz_core.pyd");
        std::fs::write(&image, b"image").unwrap();
        // The worker is not beside the extension: nothing else stands in for it.
        assert_eq!(
            descriptor_for(&image, Some(&build)).err(),
            Some(ErrorKind::WorkerUnavailable)
        );
        std::fs::write(dir.join(WORKER), b"worker").unwrap();
        assert!(descriptor_for(&image, Some(&build)).is_ok());
        // A build with no compiled fingerprint is unprovisioned; a malformed one is a mismatch.
        assert_eq!(
            descriptor_for(&image, None).err(),
            Some(ErrorKind::WorkerUnavailable)
        );
        assert_eq!(
            descriptor_for(&image, Some("not hex")).err(),
            Some(ErrorKind::WorkerMismatch)
        );
        // Only an extension module names a worker: a script or an executable beside one does not.
        for foreign in ["gwz.py", "python.exe", "gwz"] {
            let path = dir.join(foreign);
            std::fs::write(&path, b"x").unwrap();
            assert_eq!(
                descriptor_for(&path, Some(&build)).err(),
                Some(ErrorKind::WorkerUnavailable),
                "{foreign}"
            );
        }
    }
}

#[cfg(test)]
mod filesystem_conversion_tests {
    use pyo3::prelude::*;
    cfg_if::cfg_if! {
        if #[cfg(unix)] {
            mod unix {
                use super::*;
                use std::os::unix::ffi::OsStringExt;
                #[test]
                fn public_descriptor_conversion_preserves_non_utf8_filesystem_bytes() {
                    pyo3::Python::initialize();
                    Python::attach(|py| {
                        let bytes=b"/owned/invalid-\xff/worker".to_vec();
                        let path=std::path::PathBuf::from(std::ffi::OsString::from_vec(bytes.clone()));
                        let worker=gwz_sspi::WorkerExecutable::new(path,[0x42;32]).unwrap();
                        let result=crate::worker_descriptor_path(Ok(worker)).unwrap().into_pyobject(py).unwrap();
                        let actual:Vec<u8>=py.import("os").unwrap().call_method1("fsencode",(result,)).unwrap().extract().unwrap();
                        assert_eq!(actual,bytes);
                    });
                }
            }
        } else if #[cfg(windows)] {
            mod windows {
                use super::*;
                use std::os::windows::ffi::{OsStringExt,OsStrExt};
                #[test]
                fn public_descriptor_conversion_preserves_unpaired_surrogate() {
                    pyo3::Python::initialize();
                    Python::attach(|py| {
                        let words=[b'C' as u16,b':' as u16,b'\\' as u16,0xd800,b'\\' as u16,b'w' as u16];
                        let path=std::path::PathBuf::from(std::ffi::OsString::from_wide(&words));
                        let worker=gwz_sspi::WorkerExecutable::new(path,[0x42;32]).unwrap();
                        let result=crate::worker_descriptor_path(Ok(worker)).unwrap().into_pyobject(py).unwrap();
                        let actual:std::ffi::OsString=result.extract().unwrap();
                        assert_eq!(actual.encode_wide().collect::<Vec<_>>(),words);
                    });
                }
            }
        }
    }
}
