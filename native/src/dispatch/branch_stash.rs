use std::env;

use pyo3::PyResult;

use crate::{codec, error, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    match method {
        "branch" => call_branch(method, request_message, response_message, request_bytes),
        "stash" => call_stash(method, request_message, response_message, request_bytes),
        other => error::unsupported(other),
    }
}

fn call_branch(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "BranchRequest")?;
    codec::require_response(method, response_message, "BranchResponse")?;

    let request = codec::decode_message(request_bytes, "decode BranchRequest", |cbor| {
        gwz_core::BranchRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_branch(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode BranchResponse", || response.to_cbor())
}

fn call_stash(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "StashRequest")?;
    codec::require_response(method, response_message, "StashResponse")?;

    let request = codec::decode_message(request_bytes, "decode StashRequest", |cbor| {
        gwz_core::StashRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_stash(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode StashResponse", || response.to_cbor())
}

fn current_dir() -> PyResult<std::path::PathBuf> {
    env::current_dir().map_err(|err| error::runtime(format!("current_dir failed: {err}")))
}
