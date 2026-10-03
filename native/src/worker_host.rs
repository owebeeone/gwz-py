//! Loaded-image based installed descriptor. No Python attributes or runtime env.
use gwz_sspi::{ErrorKind, WorkerExecutable};
use std::path::PathBuf;
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
    let image = image::path()?;
    if !image.is_absolute()
        || !image
            .extension()
            .is_some_and(|extension| extension == "so" || extension == "pyd")
    {
        return Err(unavailable());
    }
    gwz_sspi::packaging::installed_worker(&image, option_env!("GWZ_SSPI_BUILD_FINGERPRINT"))
        .map_err(|error| error.kind())
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
}
