mod branch_stash;
mod diff;
mod git_mutation;
mod local_family;
mod log;
mod materialize;
mod merge;
mod read;

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::PathBuf;
use std::thread;

use gwz_core::model::{ErrorCode, ModelError};
use pyo3::{PyErr, PyResult};

use crate::client_host::{Network, Ran};
use crate::{codec, error, operations, shims};

/// The caller's directory for a request, taken only from its metadata, never
/// from this process's working directory. It is read only after a method has
/// been routed, so an unknown method is reported as unsupported first.
fn request_directory(request_bytes: &[u8]) -> PyResult<PathBuf> {
    let cbor = codec::decode_cbor(request_bytes)?;
    let meta = codec::catch_protocol("decode request metadata", || {
        gwz_core::RequestMeta::from_cbor(cbor.try_get(1)?)
    })?
    .map_err(|_| error::protocol("decode request metadata failed"))?;
    gwz_core::workspace_ops::caller_directory(&meta).map_err(error::model)
}

/// The directory a routed call runs against: the one a submit already derived
/// and validated, or else the request's own.
fn directory(caller_cwd: Option<PathBuf>, request_bytes: &[u8]) -> PyResult<PathBuf> {
    match caller_cwd {
        Some(caller_cwd) => Ok(caller_cwd),
        None => request_directory(request_bytes),
    }
}

fn uses_no_directory(method: &str) -> bool {
    matches!(
        method,
        "configure_transport_runtime" | "transport_capabilities"
    )
}

/// Routes one request to its handler, which runs on `backend`: a network
/// operation's route's backend, or a backend of its own (`shims`).
pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: Option<PathBuf>,
    backend: &shims::Backend<'_>,
) -> PyResult<Vec<u8>> {
    if uses_no_directory(method) {
        return read::call_transport(method, request_message, response_message, request_bytes);
    }
    match method {
        "remote_identity"
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
            &directory(caller_cwd, request_bytes)?,
            backend,
        ),
        "materialize" | "clone_workspace" | "clone_repo_member" | "attach_repo_member"
        | "snapshot" | "tag" | "capture" => materialize::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &directory(caller_cwd, request_bytes)?,
            backend,
        ),
        "commit" | "stage" | "pull_head" | "pull_snapshot" | "push" | "fetch" => {
            git_mutation::call(
                method,
                request_message,
                response_message,
                request_bytes,
                &directory(caller_cwd, request_bytes)?,
                backend,
            )
        }
        "branch" | "stash" => branch_stash::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &directory(caller_cwd, request_bytes)?,
            backend,
        ),
        "merge" => merge::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &directory(caller_cwd, request_bytes)?,
            backend,
        ),
        "diff" => diff::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &directory(caller_cwd, request_bytes)?,
        ),
        "log" => log::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &directory(caller_cwd, request_bytes)?,
        ),
        "clone_local_workspace" | "local_family" => local_family::call(
            method,
            request_message,
            response_message,
            request_bytes,
            &directory(caller_cwd, request_bytes)?,
            backend,
        ),
        other => Err(error::unsupported_method(other)),
    }
}

/// Accepts one request and runs it on its own `gwz-py-operation` thread. A
/// network operation arrives with its `network`, registered with its host,
/// and waits on that thread for a slot.
pub(crate) fn submit(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    network: Option<Network>,
) -> PyResult<Vec<u8>> {
    match method {
        "init_from_sources" => submit_init_from_sources(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "materialize" => submit_materialize(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "clone_workspace" => submit_clone_workspace(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "clone_repo_member" => submit_clone_repo_member(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "pull_head" => submit_pull_head(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "pull_snapshot" => submit_pull_snapshot(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "push" => submit_push(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "fetch" => submit_fetch(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
            network,
        ),
        "merge" => merge::submit(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
        ),
        "clone_local_workspace" => local_family::submit(
            method,
            request_message,
            response_message,
            request_bytes,
            &request_directory(request_bytes)?,
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
    network: Option<Network>,
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
        network,
    )
}

fn submit_materialize(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    network: Option<Network>,
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
        network,
    )
}

fn submit_clone_workspace(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    network: Option<Network>,
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
        network,
    )
}

fn submit_clone_repo_member(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    network: Option<Network>,
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
        network,
    )
}

fn submit_pull_head(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    network: Option<Network>,
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
        network,
    )
}

fn submit_pull_snapshot(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    network: Option<Network>,
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
        network,
    )
}

fn submit_push(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    network: Option<Network>,
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
        network,
    )
}

/// `gwz fetch` accepted asynchronously. The accepted envelope carries no
/// `repos` rows: they are the finished operation's answer, read back through
/// `operation.result`, exactly as push's member rows are.
fn submit_fetch(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    caller_cwd: &std::path::Path,
    network: Option<Network>,
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "FetchRequest")?;
    codec::require_response(method, response_message, "FetchResponse")?;
    let request = codec::decode_message(request_bytes, "decode FetchRequest", |cbor| {
        gwz_core::FetchRequest::from_cbor(cbor)
    })?;
    submit_accepted(
        method,
        request_message,
        response_message,
        request_bytes,
        caller_cwd,
        &request.meta,
        gwz_core::ActionKind::Fetch,
        |response| {
            gwz_core::FetchResponse {
                response,
                repos: None,
            }
            .to_cbor()
        },
        network,
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
    network: Option<Network>,
) -> PyResult<Vec<u8>> {
    let operation_id = shims::operation_id(&meta.request_id);
    // Refused before any effect while another operation with this request
    // ID is live (the core session contract, §4.3).
    let recorder = operations::begin(&operation_id).map_err(error::model)?;
    cfg_if::cfg_if! {
        if #[cfg(any(all(unix, gwz_transport_candidate), all(windows, gwz_transport_candidate, gwz_windows_https_qualification)))] {
            let response_meta = gwz_core::ResponseMeta {
                transport_message: None,
                transport: None,
                request_id: meta.request_id.clone(),
                schema_version: meta.schema_version.clone(),
                action,
                aggregate_status: gwz_core::AggregateStatus::Accepted,
                operation_id: Some(operation_id.clone()),
                message: None,
                attribution: meta.attribution.clone(),
            };
        } else {
            let response_meta = gwz_core::ResponseMeta {
                transport: None,
                request_id: meta.request_id.clone(),
                schema_version: meta.schema_version.clone(),
                action,
                aggregate_status: gwz_core::AggregateStatus::Accepted,
                operation_id: Some(operation_id.clone()),
                message: None,
                attribution: meta.attribution.clone(),
            };
        }
    }
    let envelope = gwz_core::ResponseEnvelope {
        meta: response_meta,
        members: Vec::new(),
        errors: Vec::new(),
    };
    let accepted = codec::encode_message("encode accepted response", || encode_response(envelope))?;
    spawn_call(
        method,
        request_message,
        response_message,
        request_bytes,
        recorder,
        meta.clone(),
        action,
        caller_cwd.to_path_buf(),
        network,
    )?;
    Ok(accepted)
}

#[allow(clippy::too_many_arguments)]
fn spawn_call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
    recorder: operations::OperationRecorder,
    meta: gwz_core::RequestMeta,
    action: gwz_core::ActionKind,
    caller_cwd: PathBuf,
    network: Option<Network>,
) -> PyResult<()> {
    let method = method.to_owned();
    let request_message = request_message.to_owned();
    let response_message = response_message.to_owned();
    let request_bytes = request_bytes.to_vec();
    let failure_recorder = recorder.clone();
    thread::Builder::new()
        .name("gwz-py-operation".into())
        .spawn(move || {
            let run = |backend: &shims::Backend<'_>| {
                call(
                    &method,
                    &request_message,
                    &response_message,
                    &request_bytes,
                    Some(caller_cwd),
                    backend,
                )
            };
            // The handler reports to the record this submit began.
            let outcome =
                catch_unwind(AssertUnwindSafe(|| match network {
                    None => Submitted::Ran(run(&shims::Backend::own().reporting_to(&recorder))),
                    Some(network) => Submitted::from(network.run(|backend| {
                        run(&shims::Backend::given(backend).reporting_to(&recorder))
                    })),
                }));
            record(&recorder, &meta, action, outcome);
        })
        .map_err(|err| worker_spawn_failure(failure_recorder, err))?;
    Ok(())
}

/// What became of a submitted operation, for its record.
enum Submitted {
    /// It ran; a handler that succeeded recorded its result itself.
    Ran(PyResult<Vec<u8>>),
    /// It was refused before its handler ran, or its entry failed.
    Refused(ModelError),
}

impl From<Ran<PyResult<Vec<u8>>>> for Submitted {
    fn from(ran: Ran<PyResult<Vec<u8>>>) -> Self {
        match ran.result {
            // After a close at interpreter exit the operation must not
            // attach to the interpreter, so a Python error is recorded
            // without its text, which only the interpreter can render.
            Ok(Err(_)) if ran.exiting => Self::Refused(ModelError::new(
                ErrorCode::InternalError,
                "the operation failed after its client closed at interpreter exit",
            )),
            Ok(result) => Self::Ran(result),
            Err(refusal) => Self::Refused(refusal),
        }
    }
}

/// Records a submitted operation's failure; a success its handler recorded.
fn record(
    recorder: &operations::OperationRecorder,
    meta: &gwz_core::RequestMeta,
    action: gwz_core::ActionKind,
    outcome: thread::Result<Submitted>,
) {
    match outcome {
        // The text that rendering `native operation panicked` as a Python
        // error gave, without attaching to the interpreter.
        Err(_) => {
            let _ = recorder.finish_panic_error(
                meta.request_id.clone(),
                action,
                "RuntimeError: native operation panicked".into(),
            );
        }
        Ok(Submitted::Refused(refusal)) => {
            let _ = recorder.finish_model_error(meta, action, &refusal);
        }
        Ok(Submitted::Ran(Err(err))) => {
            let _ = recorder.finish_error(
                meta.request_id.clone(),
                meta.schema_version.clone(),
                action,
                err.to_string(),
            );
        }
        Ok(Submitted::Ran(Ok(_))) => {}
    }
}

fn worker_spawn_failure(recorder: operations::OperationRecorder, source: std::io::Error) -> PyErr {
    let message = format!("native worker unavailable: {source}");
    let refusal = gwz_core::model::ModelError::new(gwz_core::model::ErrorCode::IoError, message);
    recorder.refuse(refusal.clone());
    error::model(refusal)
}

cfg_if::cfg_if! {
    if #[cfg(test)] {
        mod worker_spawn_failure_tests {
            use super::*;

            #[test]
            fn spawn_failure_writes_terminal_before_returning() {
                use pyo3::types::PyAnyMethods;
                pyo3::Python::initialize();
                let operation_id = "spawn-failure-test";
                let recorder = operations::begin(operation_id)
                    .expect("a new operation ID begins a record");
                let error = worker_spawn_failure(
                    recorder,
                    std::io::Error::other("injected spawn failure"),
                );
                pyo3::Python::attach(|py| {
                    let code: String = error.value(py).getattr("code").unwrap().extract().unwrap();
                    assert_eq!(code, "IoError");
                });
                let retained = operations::result(operation_id).expect_err("failed launch is a refusal");
                pyo3::Python::attach(|py| {
                    let code: String = retained.value(py).getattr("code").unwrap().extract().unwrap();
                    assert_eq!(code, "IoError");
                });
                assert!(operations::wait_events(operation_id, 0, std::time::Duration::ZERO).unwrap().1);
            }
        }
    }
}
