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
        "ls" => call_ls(method, request_message, response_message, request_bytes),
        "create_workspace" | "init_from_sources" | "add_existing_repo" | "create_repo"
        | "repo_sync" | "status" => Err(error::not_implemented(method)),
        other => error::unsupported(other),
    }
}

fn call_ls(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "LsRequest")?;
    codec::require_response(method, response_message, "LsResponse")?;

    let request_cbor = codec::decode_cbor(request_bytes)?;
    let request = codec::catch_protocol("decode LsRequest", || {
        gwz_core::LsRequest::from_cbor(&request_cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start =
        env::current_dir().map_err(|err| error::runtime(format!("current_dir failed: {err}")))?;
    let response = shims::no_backend(&request_id, |operation_id| {
        gwz_core::workspace_ops::handle_ls(&start, request, operation_id)
    })?;
    let response_cbor = codec::catch_protocol("encode LsResponse", || response.to_cbor())?;
    Ok(codec::encode_cbor(&response_cbor))
}
