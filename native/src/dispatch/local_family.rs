//! Native dispatch for the local clone family (LCM1.0c, 2026-09-05):
//! `clone_local_workspace` (ActionKind 27) and `local_family` (ActionKind
//! 28). Both go through gwz-core's dispatch slots, which validate request
//! shape and refuse unsupported family dry-run before any effect; at this
//! checkpoint every mode refuses as `unsupported_operation`. Parsing and
//! presentation live in the Python driver lane's `cli_local_family` module;
//! the existing `cli_local.py` (forall/clone rendering) is unrelated.

use pyo3::PyResult;

use crate::{codec, error, operations, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    match method {
        "clone_local_workspace" => call_clone_local_workspace(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "local_family" => call_local_family(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        other => error::unsupported(other),
    }
}

pub(crate) fn submit(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    match method {
        "clone_local_workspace" => {
            codec::require_request(method, request_message, "CloneLocalWorkspaceRequest")?;
            codec::require_response(method, response_message, "CloneLocalWorkspaceResponse")?;
            let request = codec::decode_message(
                request_bytes,
                "decode CloneLocalWorkspaceRequest",
                gwz_core::CloneLocalWorkspaceRequest::from_cbor,
            )?;
            super::submit_accepted(
                method,
                request_message,
                response_message,
                request_bytes,
                caller_cwd,
                &request.meta,
                gwz_core::ActionKind::CloneLocalWorkspace,
                |response| gwz_core::CloneLocalWorkspaceResponse { response }.to_cbor(),
            )
        }
        other => Err(error::unsupported_method(other)),
    }
}

fn call_clone_local_workspace(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CloneLocalWorkspaceRequest")?;
    codec::require_response(method, response_message, "CloneLocalWorkspaceResponse")?;
    let request = codec::decode_message(
        request_bytes,
        "decode CloneLocalWorkspaceRequest",
        gwz_core::CloneLocalWorkspaceRequest::from_cbor,
    )?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let (response, recorder): (
        gwz_core::CloneLocalWorkspaceResponse,
        operations::OperationRecorder,
    ) = shims::backend_with_events(&request_id, |backend, operation_id, events| {
        gwz_core::workspace_ops::handle_clone_local_workspace(
            backend,
            start,
            request,
            operation_id,
            events,
        )
    })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode CloneLocalWorkspaceResponse", || response.to_cbor())
}

fn call_local_family(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "LocalFamilyRequest")?;
    codec::require_response(method, response_message, "LocalFamilyResponse")?;
    let request = codec::decode_message(request_bytes, "decode LocalFamilyRequest", |cbor| {
        gwz_core::LocalFamilyRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let (response, recorder): (gwz_core::LocalFamilyResponse, operations::OperationRecorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_local_family(
                backend,
                start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode LocalFamilyResponse", || response.to_cbor())
}
