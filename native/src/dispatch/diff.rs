//! `diff` dispatch — the planning half of `gwz diff`.
//!
//! `diff` is a direct request/response call (like `status`/`ls`), not a submitted
//! operation: `handle_diff` resolves the per-repo target set, builds the manifest,
//! and — for byte formats — mints a `diff.output` `log_id` and runs the producer
//! to completion synchronously (in-process v0), all before returning. The patch
//! bytes then live in the process-global [`diff_logs`](crate::diff_logs) registry,
//! read later through the `diff_log_read` pyfunction. `DiffManifestResponse` stays
//! metadata-only (AD5): it carries the manifest, summary, targets, excluded
//! targets, and the optional `DiffOutputLogRef.log_id`, never patch bytes.

use pyo3::PyResult;

use crate::{codec, diff_logs, error, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    match method {
        "diff" => call_diff(method, request_message, response_message, request_bytes),
        other => error::unsupported(other),
    }
}

fn call_diff(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "DiffRequest")?;
    codec::require_response(method, response_message, "DiffManifestResponse")?;

    let request = codec::decode_message(request_bytes, "decode DiffRequest", |cbor| {
        gwz_core::DiffRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = super::current_dir()?;
    let outcome = shims::no_backend(&request_id, |operation_id| {
        gwz_core::diff::handle_diff(&start, request, operation_id, diff_logs::registry())
    })?;
    codec::encode_message("encode DiffManifestResponse", || {
        outcome.response.to_cbor()
    })
}
