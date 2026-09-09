use pyo3::PyResult;

use crate::codec;
use crate::{error, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    match method {
        "materialize" => call_materialize(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "clone_workspace" => call_clone_workspace(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "clone_repo_member" => call_clone_repo_member(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "attach_repo_member" => call_attach_repo_member(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "snapshot" => call_snapshot(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "tag" => call_tag(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "capture" => call_capture(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        other => error::unsupported(other),
    }
}

fn call_materialize(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "MaterializeRequest")?;
    codec::require_response(method, response_message, "MaterializeResponse")?;

    let request = codec::decode_message(request_bytes, "decode MaterializeRequest", |cbor| {
        gwz_core::MaterializeRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_materialize(
                backend,
                start,
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
    caller_cwd: &std::path::Path,
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
                caller_cwd,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode CloneWorkspaceResponse", || response.to_cbor())
}

fn call_clone_repo_member(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CloneRepoMemberRequest")?;
    codec::require_response(method, response_message, "CloneRepoMemberResponse")?;

    let request = codec::decode_message(request_bytes, "decode CloneRepoMemberRequest", |cbor| {
        gwz_core::CloneRepoMemberRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_clone_repo_member(
                backend,
                start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode CloneRepoMemberResponse", || response.to_cbor())
}

fn call_attach_repo_member(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "AttachRepoMemberRequest")?;
    codec::require_response(method, response_message, "AttachRepoMemberResponse")?;

    let request = codec::decode_message(request_bytes, "decode AttachRepoMemberRequest", |cbor| {
        gwz_core::AttachRepoMemberRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_attach_repo_member(
                backend,
                start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode AttachRepoMemberResponse", || response.to_cbor())
}

fn call_snapshot(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "SnapshotRequest")?;
    codec::require_response(method, response_message, "SnapshotResponse")?;

    let request = codec::decode_message(request_bytes, "decode SnapshotRequest", |cbor| {
        gwz_core::SnapshotRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_snapshot(backend, start, request, operation_id)
    })?;
    codec::encode_message("encode SnapshotResponse", || response.to_cbor())
}

fn call_tag(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "TagRequest")?;
    codec::require_response(method, response_message, "TagResponse")?;

    let request = codec::decode_message(request_bytes, "decode TagRequest", |cbor| {
        gwz_core::TagRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        let services = backend.operation_services();
        gwz_core::workspace_ops::handle_tag_with_services(
            &services,
            backend,
            start,
            request,
            operation_id,
        )
    })?;
    codec::encode_message("encode TagResponse", || response.to_cbor())
}

fn call_capture(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CaptureRequest")?;
    codec::require_response(method, response_message, "CaptureResponse")?;

    let request = codec::decode_message(request_bytes, "decode CaptureRequest", |cbor| {
        gwz_core::CaptureRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_capture(backend, start, request, operation_id)
    })?;
    codec::encode_message("encode CaptureResponse", || response.to_cbor())
}
