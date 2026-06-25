use std::env;

use pyo3::PyResult;

use crate::codec;
use crate::{error, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    match method {
        "materialize" => call_materialize(method, request_message, response_message, request_bytes),
        "clone_workspace" => {
            call_clone_workspace(method, request_message, response_message, request_bytes)
        }
        "snapshot" => call_snapshot(method, request_message, response_message, request_bytes),
        "tag" => call_tag(method, request_message, response_message, request_bytes),
        "capture" => call_capture(method, request_message, response_message, request_bytes),
        other => error::unsupported(other),
    }
}

fn call_materialize(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "MaterializeRequest")?;
    codec::require_response(method, response_message, "MaterializeResponse")?;

    let request = codec::decode_message(request_bytes, "decode MaterializeRequest", |cbor| {
        gwz_core::MaterializeRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_materialize(
                backend,
                &start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode MaterializeResponse", || response.to_cbor())
}

fn call_clone_workspace(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CloneWorkspaceRequest")?;
    codec::require_response(method, response_message, "CloneWorkspaceResponse")?;

    let request = codec::decode_message(request_bytes, "decode CloneWorkspaceRequest", |cbor| {
        gwz_core::CloneWorkspaceRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_clone_workspace_request(
                backend,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode CloneWorkspaceResponse", || response.to_cbor())
}

fn call_snapshot(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "SnapshotRequest")?;
    codec::require_response(method, response_message, "SnapshotResponse")?;

    let request = codec::decode_message(request_bytes, "decode SnapshotRequest", |cbor| {
        gwz_core::SnapshotRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_snapshot(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode SnapshotResponse", || response.to_cbor())
}

fn call_tag(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "TagRequest")?;
    codec::require_response(method, response_message, "TagResponse")?;

    let request = codec::decode_message(request_bytes, "decode TagRequest", |cbor| {
        gwz_core::TagRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_tag(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode TagResponse", || response.to_cbor())
}

fn call_capture(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CaptureRequest")?;
    codec::require_response(method, response_message, "CaptureResponse")?;

    let request = codec::decode_message(request_bytes, "decode CaptureRequest", |cbor| {
        gwz_core::CaptureRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_capture(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode CaptureResponse", || response.to_cbor())
}

fn current_dir() -> PyResult<std::path::PathBuf> {
    env::current_dir().map_err(|err| error::runtime(format!("current_dir failed: {err}")))
}
