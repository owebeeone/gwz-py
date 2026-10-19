//! Unified commit-log dispatch through the public operation seam.

use pyo3::PyResult;

use crate::{codec, error, log_outputs, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    backend: &shims::Backend<'_>,
) -> PyResult<Vec<u8>> {
    match method {
        "log" => call_log(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
            backend,
        ),
        other => error::unsupported(other),
    }
}

fn call_log(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    backend: &shims::Backend<'_>,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "LogRequest")?;
    codec::require_response(method, response_message, "LogResponse")?;
    let request = codec::decode_message(request_bytes, "decode LogRequest", |cbor| {
        gwz_core::LogRequest::from_cbor(cbor)
    })?;
    let response = run_log(
        backend,
        caller_cwd,
        request,
        |backend, start, request, operation_id| {
            gwz_core::operation::handle_log(
                backend,
                start,
                request,
                operation_id,
                log_outputs::registry(),
            )
        },
    )?;
    codec::encode_message("encode LogResponse", || response.to_cbor())
}

/// Runs `handler` on the request's own backend: its Client's, so the path cursor's `git` child spawns under the
/// gate the Client's other spawns and worker launches take (contract C9; operator decision D25).
fn run_log<T>(
    backend: &shims::Backend<'_>,
    start: &std::path::Path,
    request: gwz_core::LogRequest,
    handler: impl FnOnce(
        &gwz_core::git::Git2Backend,
        &std::path::Path,
        gwz_core::LogRequest,
        String,
    ) -> gwz_core::model::ModelResult<T>,
) -> PyResult<T> {
    let request_id = request.meta.request_id.clone();
    shims::backend(backend, &request_id, |backend, operation_id| {
        handler(backend, start, request, operation_id)
    })
}

cfg_if::cfg_if! {
    if #[cfg(all(test, gwz_transport_candidate, any(unix, windows)))] {
        mod tests {
            use super::*;
            use gwz_core::transport_host::NativeHost;

            /// A log call runs on its Client's gate (operator decision D25): the backend its handler gets, which
            /// the path cursor's `git rev-list` spawns under, is the Client's native route's, and not another
            /// Client's or a plain `Git2Backend::new()`'s. (An excluding gate exists only on Windows, so this asks
            /// whom the gate belongs to rather than trying to hold it.)
            #[test]
            fn a_log_call_runs_on_its_clients_gate() {
                let host = NativeHost::new(Err(gwz_sspi::ErrorKind::UnsupportedPlatform));
                let other = NativeHost::new(Err(gwz_sspi::ErrorKind::UnsupportedPlatform));
                let backend = shims::Backend::own(host.clone());
                let ran = run_log(
                    &backend,
                    std::path::Path::new("."),
                    gwz_core::LogRequest::default(),
                    |backend, _start, _request, _operation_id| {
                        Ok((
                            host.gates(backend),
                            other.gates(backend),
                            host.gates(&gwz_core::git::Git2Backend::new()),
                        ))
                    },
                )
                .unwrap();
                assert_eq!(ran, (true, false, false));
            }
        }
    }
}
