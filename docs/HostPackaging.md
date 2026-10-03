# Packaged SSPI worker

The PEP517 backend `build_support/sspi_backend.py` builds a dedicated worker and
the extension with identical build-only artifact-set metadata. Normal wheel,
package_smoke, candidate and release recipes use it. Maturin source distributions
include the backend and vendor Cargo path dependencies; discovery uses Cargo's
resolved manifest paths after extraction, without a sibling-checkout fallback.
The producer is carried by the resolved SSPI package. It records source/contracts,
lock, workspace configuration, compiler, target, profile, features and delegated
packaging options. It is not a finished binary hash or signature.

The worker uses a fresh build-owned target directory, so simultaneous wheels
sharing an extension cache cannot overwrite a different worker before bundling.
Subprocess environments are copied; the backend does not mutate process
os.environ. The wheel contains the worker and a nonsecret receipt beside the
extension, and regenerates RECORD hashes. The receipt never grants runtime trust.

For a local provisioned development-profile wheel, with Cargo dependencies
available and an external target directory:

```sh
CARGO_TARGET_DIR=/absolute/external/cache python build_support/sspi_backend.py \
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
