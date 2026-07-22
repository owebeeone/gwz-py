use std::path::Path;
use std::thread;

use pyo3::PyResult;

use crate::{codec, error, operations, shims};

use super::current_dir;

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    let request = decode_request(method, request_message, response_message, request_bytes)?;
    let start = current_dir()?;
    let operation_id = shims::operation_id(&request.meta.request_id);
    let recorder = operations::begin_exclusive(&operation_id)?;
    let response = run(request, &start, &operation_id, &recorder)?;
    codec::encode_message("encode MergeResponse", || response.to_cbor())
}

pub(crate) fn submit(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    let request = decode_request(method, request_message, response_message, request_bytes)?;
    let start = current_dir()?;
    let operation_id = shims::operation_id(&request.meta.request_id);
    let recorder = operations::begin_exclusive(&operation_id)?;
    let accepted = accepted_response(&request.meta, &operation_id);
    let accepted_bytes =
        codec::encode_message("encode accepted MergeResponse", || accepted.to_cbor())?;

    let thread_operation_id = operation_id.clone();
    thread::spawn(move || {
        let _ = run(request, &start, &thread_operation_id, &recorder);
    });
    Ok(accepted_bytes)
}

fn decode_request(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<gwz_core::MergeRequest> {
    codec::require_request(method, request_message, "MergeRequest")?;
    codec::require_response(method, response_message, "MergeResponse")?;
    codec::decode_message(request_bytes, "decode MergeRequest", |cbor| {
        gwz_core::MergeRequest::from_cbor(cbor)
    })
}

fn run(
    request: gwz_core::MergeRequest,
    start: &Path,
    operation_id: &str,
    recorder: &operations::OperationRecorder,
) -> PyResult<gwz_core::MergeResponse> {
    let meta = request.meta.clone();
    let result =
        shims::backend_with_recorder(operation_id, recorder, |backend, operation_id, events| {
            gwz_core::workspace_ops::handle_merge_with_events(
                backend,
                start,
                request,
                operation_id,
                events,
            )
        });

    match result {
        Ok(response) => {
            recorder.finish_merge(&response)?;
            Ok(response)
        }
        Err(model_error) => {
            recorder.finish_model_error(&meta, gwz_core::ActionKind::Merge, &model_error)?;
            Err(error::model(model_error))
        }
    }
}

fn accepted_response(meta: &gwz_core::RequestMeta, operation_id: &str) -> gwz_core::MergeResponse {
    gwz_core::MergeResponse {
        response: gwz_core::ResponseEnvelope {
            meta: gwz_core::ResponseMeta {
                request_id: meta.request_id.clone(),
                schema_version: meta.schema_version.clone(),
                action: gwz_core::ActionKind::Merge,
                aggregate_status: gwz_core::AggregateStatus::Accepted,
                operation_id: Some(operation_id.to_owned()),
                message: None,
                attribution: meta.attribution.clone(),
            },
            members: Vec::new(),
            errors: Vec::new(),
        },
        ..gwz_core::MergeResponse::default()
    }
}
