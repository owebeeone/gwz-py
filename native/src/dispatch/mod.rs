mod branch_stash;
mod diff;
mod git_mutation;
mod local_family;
mod log;
mod materialize;
mod merge;
mod read;

use std::path::PathBuf;
use std::thread;

use pyo3::PyResult;

use crate::{codec, error, operations, shims};

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: PathBuf,
) -> PyResult<Vec<u8>> {
    match method {
        "configure_transport_runtime"
        | "remote_identity"
        | "transport_capabilities"
        | "create_workspace"
        | "init_from_sources"
        | "add_existing_repo"
        | "create_repo"
        | "repo_sync"
        | "detach_repo_member"
        | "status"
        | "ls"
        | "resolve_forall_targets"
        | "list_snapshots" => read::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "materialize" | "clone_workspace" | "clone_repo_member" | "attach_repo_member"
        | "snapshot" | "tag" | "capture" => materialize::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "commit" | "stage" | "pull_head" | "pull_snapshot" | "push" => git_mutation::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "branch" | "stash" => branch_stash::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "merge" => merge::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "diff" => diff::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "log" => log::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "clone_local_workspace" | "local_family" => local_family::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        other => Err(error::unsupported_method(other)),
    }
}

pub(crate) fn submit(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: PathBuf,
) -> PyResult<Vec<u8>> {
    match method {
        "init_from_sources" => submit_init_from_sources(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "materialize" => submit_materialize(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "clone_workspace" => submit_clone_workspace(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "clone_repo_member" => submit_clone_repo_member(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "pull_head" => submit_pull_head(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "pull_snapshot" => submit_pull_snapshot(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "push" => submit_push(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "merge" => merge::submit(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        "clone_local_workspace" => local_family::submit(
            method,
            request_message,
            response_message,
            request_bytes,
            &caller_cwd,
        ),
        other => Err(error::unsupported_method(other)),
    }
}

fn submit_init_from_sources(
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
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::InitFromSources,
        |response| gwz_core::InitFromSourcesResponse { response }.to_cbor(),
    )
}

fn submit_materialize(
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
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::Materialize,
        |response| gwz_core::MaterializeResponse { response }.to_cbor(),
    )
}

fn submit_clone_workspace(
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
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::CloneWorkspace,
        |response| gwz_core::CloneWorkspaceResponse { response }.to_cbor(),
    )
}

fn submit_clone_repo_member(
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
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::CloneRepoMember,
        |response| gwz_core::CloneRepoMemberResponse { response }.to_cbor(),
    )
}

fn submit_pull_head(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "PullHeadRequest")?;
    codec::require_response(method, response_message, "PullHeadResponse")?;
    let request = codec::decode_message(request_bytes, "decode PullHeadRequest", |cbor| {
        gwz_core::PullHeadRequest::from_cbor(cbor)
    })?;
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::PullHead,
        |response| gwz_core::PullHeadResponse { response }.to_cbor(),
    )
}

fn submit_pull_snapshot(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "PullSnapshotRequest")?;
    codec::require_response(method, response_message, "PullSnapshotResponse")?;
    let request = codec::decode_message(request_bytes, "decode PullSnapshotRequest", |cbor| {
        gwz_core::PullSnapshotRequest::from_cbor(cbor)
    })?;
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::PullSnapshot,
        |response| gwz_core::PullSnapshotResponse { response }.to_cbor(),
    )
}

fn submit_push(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "PushRequest")?;
    codec::require_response(method, response_message, "PushResponse")?;
    let request = codec::decode_message(request_bytes, "decode PushRequest", |cbor| {
        gwz_core::PushRequest::from_cbor(cbor)
    })?;
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::Push,
        |response| gwz_core::PushResponse { response }.to_cbor(),
    )
}

#[allow(clippy::too_many_arguments)]
fn submit_accepted(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    meta: &gwz_core::RequestMeta,
    action: gwz_core::ActionKind,
    encode_response: impl FnOnce(gwz_core::ResponseEnvelope) -> gwz_core::Cbor,
) -> PyResult<Vec<u8>> {
    let operation_id = shims::operation_id(&meta.request_id);
    let recorder = operations::begin(&operation_id);
    let envelope = gwz_core::ResponseEnvelope {
        meta: gwz_core::ResponseMeta {
            transport: None,
            request_id: meta.request_id.clone(),
            schema_version: meta.schema_version.clone(),
            action,
            aggregate_status: gwz_core::AggregateStatus::Accepted,
            operation_id: Some(operation_id),
            message: None,
            attribution: meta.attribution.clone(),
        },
        members: Vec::new(),
        errors: Vec::new(),
    };
    spawn_call(
        method,
        request_message,
        response_message,
        request_bytes,
        recorder,
        meta.request_id.clone(),
        meta.schema_version.clone(),
        action,
        caller_cwd.to_path_buf(),
    );
    codec::encode_message("encode accepted response", || encode_response(envelope))
}

#[allow(clippy::too_many_arguments)]
fn spawn_call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    recorder: operations::OperationRecorder,
    request_id: String,
    schema_version: String,
    action: gwz_core::ActionKind,
    caller_cwd: PathBuf,
) {
    let method = method.to_owned();
    let request_message = request_message.to_owned();
    let response_message = response_message.to_owned();
    let request_bytes = request_bytes.to_vec();
    thread::spawn(move || {
        if let Err(err) = call(
            &method,
            &request_message,
            &response_message,
            &request_bytes,
            caller_cwd,
        ) {
            let _ = recorder.finish_error(request_id, schema_version, action, err.to_string());
        }
    });
}
