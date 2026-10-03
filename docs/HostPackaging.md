# Packaged SSPI worker

The PEP517 backend `build_support/sspi_backend.py` builds a dedicated worker and
the extension with identical build-only artifact-set metadata. Normal wheel,
package_smoke, candidate and release recipes use it. Maturin source distributions
include the backend and vendor Cargo path dependencies; discovery uses Cargo's
resolved manifest paths after extraction, without a sibling-checkout fallback.
The producer is carried by the resolved SSPI package. It records source/contracts,
lock, workspace configuration, compiler, target, profile, features and delegated
packaging options. It is not a finished binary hash or signature.

Worker and extension use fresh build-owned target directories under the output
directory; provisioned builds do not capture artifacts from a shared mutable cache.
Subprocess environments are copied; the backend does not mutate process
os.environ. The wheel contains the worker and a nonsecret receipt beside the
extension, and regenerates RECORD hashes. The receipt never grants runtime trust.

For a local provisioned development-profile wheel, with Cargo dependencies
available and an external output directory:

```sh
python build_support/sspi_backend.py \
  --out /absolute/external/wheels --build-args='--profile dev --locked'
```

`pip install .` and `pip wheel .` use the same backend. Editable hooks remain
supported, but editable installations are explicitly unprovisioned: their source
extension receives no packaging fingerprint or bundled worker. Use a wheel for
worker provisioning. Registry-pin release reconciliation includes SSPI before
its existing registry checks; the unpublished/publish=false SSPI dependency is
currently a precise public release prerequisite, not an excuse to ship an
unprovisioned release artifact.

`_gwz_core.sspi_worker_descriptor()` returns the absolute fixed adjacent worker
path or a fixed classification (WorkerUnavailable/WorkerMismatch). Its native
implementation obtains the actual loaded extension image, independently of
mutable module.__file__, PATH, runtime fingerprint variables and current cwd.
Relative loader paths refuse; there is no cwd fallback. The extension must be a
.so or .pyd image. Missing/nonregular/symlink worker files refuse.

Unix uses [dladdr](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/dladdr.3.html)
with an extension-local function address. Windows uses
[GetModuleHandleExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulehandleexw)
FROM_ADDRESS|UNCHANGED_REFCOUNT and
[GetModuleFileNameW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulefilenamew)
with fixed initialized path storage. The executing extension keeps its image
alive; the borrowed handle is never FreeLibrary'd. Zero/truncated paths refuse.
These synchronous nonsecret image/path probes are not an async HTTP operation.

The callable descriptor is not connected to ClientHost HTTP authentication yet.
The existing worker Supervisor owns future launches and verifies compiled Hello;
this checkpoint adds no process owner or fallback. Portable selection tests do
not prove Windows execution or Hello mismatch. Digest is unavailable, and Windows
transport/release activation remains closed.

Install/upgrade the complete wheel with pip, keeping the worker beside the
loaded extension. pip uninstall removes the bundled worker and receipt through
wheel RECORD, alongside the extension. Candidate unpacking preserves this
adjacent layout and executable mode. The explicit --out names the resulting
wheel directory; worker build scratch is disposed after bundling. A portable
descriptor does not mean Windows HTTP authentication is available.

## Explicit wrapper and frontend options

| Option | Required/default | Behavior |
|---|---|---|
| `--out DIRECTORY` | Required, no default | Publishes the completed wheel here; may create the directory |
| `--build-args TEXT` | Optional, empty by default | Shell-tokenized delegated maturin build options, not a shell command |
| `--help` | Optional | Prints usage and exits |

With empty delegated arguments, the profile is release (or configured
`tool.maturin.profile`), and the interpreter is the invoking frontend Python.
The metadata and wheel hooks resolve that same interpreter and target before
fingerprinting. Build-time `MATURIN_PEP517_USE_BASE_PYTHON`/`use-base-python` retain
maturin's base-interpreter choice. Explicit `--interpreter PATH`/`-i PATH` selects
one interpreter and probes its platform/width. Target precedence is explicit
`--target`, tool.maturin target, CARGO_BUILD_TARGET, the selected 32-bit Python on
AMD64 Windows default (`i686-pc-windows-msvc`), then the Rust compiler host.
Candidates' `--python` launches this backend with that Python, so it remains the
frontend interpreter. Custom JSON target paths are refused.

Supported delegated options are `--profile dev|release`, `--release`,
`--target TRIPLE`, `--interpreter`/`-i`, `--features`/`-F`, `--locked`, `--offline`,
`--frozen`, `--strip`, `--no-default-features`, `--auditwheel`, `--compatibility`,
`--manylinux`, and verbosity `-v`/`--verbose` or `-q`/`--quiet`. Value options take
one value. Duplicate aliases, conflicting release/profile choices, missing
values and other options refuse before provisioning. Locked resolution is
always required. Frontend PEP517 config_settings use `maturin.build-args`
(the retained `build-args` alias and MATURIN_PEP517_ARGS apply to hook callers).
A release wheel using host-native defaults:

```sh
python build_support/sspi_backend.py --out /absolute/external/wheels
```

An explicit development build uses `--build-args='--profile dev --target
YOUR-RUST-TRIPLE --interpreter /absolute/python'` on that command. All target,
profile, interpreter and delegated choices are recorded; worker and extension
receive the same selected target/profile. Cross builds still need platform
prerequisites; provisioning does not qualify Windows HTTP operation.

Raw wheels and all Rust output are now owned by one staging directory under
`--out`. CARGO_TARGET_DIR no longer supplies a shared mutable extension cache for
provisioned wheel builds. Completed provisioning validates ZIP contents and the
complete RECORD before publication. Same-name outputs refuse explicitly; choose
a fresh output directory for a replacement build, then install the completed
wheel. Atomic no-replace publication uses a same-filesystem hard link: unsupported
filesystems fail without replacing a prior destination, with no lock or indefinite
stale-lock wait. No Windows/filesystem runtime qualification is implied by Darwin
coverage. Source archives likewise stage their backend adaptation before final
publication. A failure/interruption before publication exposes no raw final
artifact; a prior artifact is preserved. This is build-time ownership, not another
worker runtime owner.

The public descriptor returns a Python filesystem string through PyO3: Unix
non-UTF8 bytes and Windows unpaired UTF16 surrogates retain their path identity.
Use os.fsencode on Unix when bytes are required. It never reports a lossy changed
pathname as successful selection.
