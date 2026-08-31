//! Unified commit-log dispatch through the public operation seam.

use pyo3::PyResult;

use crate::{codec, error, log_outputs, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    match method {
        "log" => call_log(method, request_message, response_message, request_bytes),
        other => error::unsupported(other),
    }
}

fn call_log(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "LogRequest")?;
    codec::require_response(method, response_message, "LogResponse")?;
    let request = codec::decode_message(request_bytes, "decode LogRequest", |cbor| {
        gwz_core::LogRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = super::current_dir()?;
    let response = shims::no_backend(&request_id, |operation_id| {
        gwz_core::operation::handle_log(&start, request, operation_id, log_outputs::registry())
    })?;
    codec::encode_message("encode LogResponse", || response.to_cbor())
}
