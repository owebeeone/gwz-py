use pyo3::PyResult;

use crate::{codec, error, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    match method {
        "configure_transport_runtime" => {
            codec::require_request(method, request_message, "TransportRuntimeRequest")?;
            codec::require_response(method, response_message, "TransportRuntimeResponse")?;
            let request =
                codec::decode_message(request_bytes, "decode TransportRuntimeRequest", |cbor| {
                    gwz_core::TransportRuntimeRequest::from_cbor(cbor)
                })?;
            let response = gwz_core::protocol::transport_capabilities::configure_runtime(request)
                .map_err(error::model)?;
            codec::encode_message("encode TransportRuntimeResponse", || response.to_cbor())
        }
        "remote_identity" => {
            codec::require_request(method, request_message, "RemoteIdentityRequest")?;
            codec::require_response(method, response_message, "RemoteIdentityResponse")?;
            let request =
                codec::decode_message(request_bytes, "decode RemoteIdentityRequest", |cbor| {
                    gwz_core::RemoteIdentityRequest::from_cbor(cbor)
                })?;
            let request_id = request.meta.request_id.clone();
            let start = caller_cwd;
            let response = shims::no_backend(&request_id, |operation_id| {
                gwz_core::workspace_ops::handle_remote_identity(
                    &gwz_core::git::Git2Backend::new(),
                    start,
                    request,
                    operation_id,
                )
            })?;
            codec::encode_message("encode RemoteIdentityResponse", || response.to_cbor())
        }
        "transport_capabilities" => {
            codec::require_request(method, request_message, "TransportCapabilitiesRequest")?;
            codec::require_response(method, response_message, "TransportCapabilitiesResponse")?;
            let request = codec::decode_message(
                request_bytes,
                "decode TransportCapabilitiesRequest",
                |cbor| gwz_core::TransportCapabilitiesRequest::from_cbor(cbor),
            )?;
            let response = gwz_core::protocol::transport_capabilities::handle(request)
                .map_err(error::model)?;
            codec::encode_message("encode TransportCapabilitiesResponse", || {
                response.to_cbor()
            })
        }
        "create_workspace" => {
            call_create_workspace(method, request_message, response_message, request_bytes)
        }
        "init_from_sources" => call_init_from_sources(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "add_existing_repo" => call_add_existing_repo(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "create_repo" => call_create_repo(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "repo_sync" => call_repo_sync(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "detach_repo_member" => call_detach_repo_member(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "status" => call_status(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "ls" | "resolve_forall_targets" => call_ls(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        "list_snapshots" => call_list_snapshots(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        ),
        other => error::unsupported(other),
    }
}

fn call_create_workspace(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CreateWorkspaceRequest")?;
    codec::require_response(method, response_message, "CreateWorkspaceResponse")?;

    let request = codec::decode_message(request_bytes, "decode CreateWorkspaceRequest", |cbor| {
        gwz_core::CreateWorkspaceRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let response = shims::no_backend(&request_id, |operation_id| {
        gwz_core::workspace_ops::handle_create_workspace(request, operation_id)
    })?;
    codec::encode_message("encode CreateWorkspaceResponse", || response.to_cbor())
}

fn call_init_from_sources(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "InitFromSourcesRequest")?;
    codec::require_response(method, response_message, "InitFromSourcesResponse")?;

    let request = codec::decode_message(request_bytes, "decode InitFromSourcesRequest", |cbor| {
        gwz_core::InitFromSourcesRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_init_from_sources(
                backend,
                start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode InitFromSourcesResponse", || response.to_cbor())
}

fn call_add_existing_repo(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "AddExistingRepoRequest")?;
    codec::require_response(method, response_message, "AddExistingRepoResponse")?;

    let request = codec::decode_message(request_bytes, "decode AddExistingRepoRequest", |cbor| {
        gwz_core::AddExistingRepoRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_add_existing_repo(backend, start, request, operation_id)
    })?;
    codec::encode_message("encode AddExistingRepoResponse", || response.to_cbor())
}

fn call_create_repo(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CreateRepoRequest")?;
    codec::require_response(method, response_message, "CreateRepoResponse")?;

    let request = codec::decode_message(request_bytes, "decode CreateRepoRequest", |cbor| {
        gwz_core::CreateRepoRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_create_repo(backend, start, request, operation_id)
    })?;
    codec::encode_message("encode CreateRepoResponse", || response.to_cbor())
}

fn call_repo_sync(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "RepoSyncRequest")?;
    codec::require_response(method, response_message, "RepoSyncResponse")?;

    let request = codec::decode_message(request_bytes, "decode RepoSyncRequest", |cbor| {
        gwz_core::RepoSyncRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_repo_sync(backend, start, request, operation_id)
    })?;
    codec::encode_message("encode RepoSyncResponse", || response.to_cbor())
}

fn call_detach_repo_member(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "DetachRepoMemberRequest")?;
    codec::require_response(method, response_message, "DetachRepoMemberResponse")?;

    let request = codec::decode_message(request_bytes, "decode DetachRepoMemberRequest", |cbor| {
        gwz_core::DetachRepoMemberRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_detach_repo_member(backend, start, request, operation_id)
    })?;
    codec::encode_message("encode DetachRepoMemberResponse", || response.to_cbor())
}

fn call_status(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "StatusRequest")?;
    codec::require_response(method, response_message, "StatusResponse")?;

    let request = codec::decode_message(request_bytes, "decode StatusRequest", |cbor| {
        gwz_core::StatusRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::status::handle_status(backend, start, request, operation_id)
    })?;
    codec::encode_message("encode StatusResponse", || response.to_cbor())
}

fn call_ls(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "LsRequest")?;
    codec::require_response(method, response_message, "LsResponse")?;

    let request = codec::decode_message(request_bytes, "decode LsRequest", |cbor| {
        gwz_core::LsRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::no_backend(&request_id, |operation_id| {
        if method == "resolve_forall_targets" {
            gwz_core::workspace_ops::resolve_forall_targets(start, request, operation_id)
        } else {
            gwz_core::workspace_ops::handle_ls(start, request, operation_id)
        }
    })?;
    codec::encode_message("encode LsResponse", || response.to_cbor())
}

fn call_list_snapshots(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "ListSnapshotsRequest")?;
    codec::require_response(method, response_message, "ListSnapshotsResponse")?;

    let request = codec::decode_message(request_bytes, "decode ListSnapshotsRequest", |cbor| {
        gwz_core::ListSnapshotsRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = caller_cwd;
    let response = shims::no_backend(&request_id, |operation_id| {
        gwz_core::workspace_ops::handle_list_snapshots(start, request, operation_id)
    })?;
    codec::encode_message("encode ListSnapshotsResponse", || response.to_cbor())
}
