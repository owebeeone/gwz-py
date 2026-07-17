use pyo3::PyResult;

use crate::{codec, shims};

use super::current_dir;

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "MergeRequest")?;
    codec::require_response(method, response_message, "MergeResponse")?;
    let request = codec::decode_message(request_bytes, "decode MergeRequest", |cbor| {
        gwz_core::MergeRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_merge(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode MergeResponse", || response.to_cbor())
}
