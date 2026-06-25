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
        "commit" => call_commit(method, request_message, response_message, request_bytes),
        "stage" => call_stage(method, request_message, response_message, request_bytes),
        "pull_head" => call_pull_head(method, request_message, response_message, request_bytes),
        "pull_snapshot" => {
            call_pull_snapshot(method, request_message, response_message, request_bytes)
        }
        "push" => call_push(method, request_message, response_message, request_bytes),
        other => error::unsupported(other),
    }
}

fn call_commit(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "CommitRequest")?;
    codec::require_response(method, response_message, "CommitResponse")?;

    let request = codec::decode_message(request_bytes, "decode CommitRequest", |cbor| {
        gwz_core::CommitRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_commit(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode CommitResponse", || response.to_cbor())
}

fn call_stage(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "StageRequest")?;
    codec::require_response(method, response_message, "StageResponse")?;

    let request = codec::decode_message(request_bytes, "decode StageRequest", |cbor| {
        gwz_core::StageRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let response = shims::backend(&request_id, |backend, operation_id| {
        gwz_core::workspace_ops::handle_stage(backend, &start, request, operation_id)
    })?;
    codec::encode_message("encode StageResponse", || response.to_cbor())
}

fn call_pull_head(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "PullHeadRequest")?;
    codec::require_response(method, response_message, "PullHeadResponse")?;

    let request = codec::decode_message(request_bytes, "decode PullHeadRequest", |cbor| {
        gwz_core::PullHeadRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_pull_head_with_events(
                backend,
                &start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode PullHeadResponse", || response.to_cbor())
}

fn call_pull_snapshot(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "PullSnapshotRequest")?;
    codec::require_response(method, response_message, "PullSnapshotResponse")?;

    let request = codec::decode_message(request_bytes, "decode PullSnapshotRequest", |cbor| {
        gwz_core::PullSnapshotRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_pull_snapshot(
                backend,
                &start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode PullSnapshotResponse", || response.to_cbor())
}

fn call_push(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    codec::require_request(method, request_message, "PushRequest")?;
    codec::require_response(method, response_message, "PushResponse")?;

    let request = codec::decode_message(request_bytes, "decode PushRequest", |cbor| {
        gwz_core::PushRequest::from_cbor(cbor)
    })?;
    let request_id = request.meta.request_id.clone();
    let start = current_dir()?;
    let (response, recorder) =
        shims::backend_with_events(&request_id, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_push_with_events(
                backend,
                &start,
                request,
                operation_id,
                events,
            )
        })?;
    recorder.finish(&response.response)?;
    codec::encode_message("encode PushResponse", || response.to_cbor())
}

fn current_dir() -> PyResult<std::path::PathBuf> {
    env::current_dir().map_err(|err| error::runtime(format!("current_dir failed: {err}")))
}
