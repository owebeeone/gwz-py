use std::time::Duration;

use pyo3::prelude::*;

mod codec;
mod dispatch;
mod error;
mod operations;
mod shims;

#[pyfunction]
fn health() -> &'static str {
    "ok"
}

#[pyfunction]
fn version() -> &'static str {
    gwz_core::version()
}

#[pyfunction]
fn call(
    py: Python<'_>,
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    let method = method.to_owned();
    let request_message = request_message.to_owned();
    let response_message = response_message.to_owned();
    let request_bytes = request_bytes.to_vec();

    py.detach(move || dispatch::call(&method, &request_message, &response_message, &request_bytes))
}

#[pyfunction]
fn submit(
    py: Python<'_>,
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    let method = method.to_owned();
    let request_message = request_message.to_owned();
    let response_message = response_message.to_owned();
    let request_bytes = request_bytes.to_vec();

    py.detach(move || {
        dispatch::submit(&method, &request_message, &response_message, &request_bytes)
    })
}

#[pyfunction]
fn subscribe_events(operation_id: &str) -> PyResult<Vec<Vec<u8>>> {
    operations::events(operation_id)?
        .into_iter()
        .map(|event| codec::encode_message("encode OperationEvent", || event.to_cbor()))
        .collect()
}

#[pyfunction]
fn wait_events(
    py: Python<'_>,
    operation_id: &str,
    after_sequence: i64,
    timeout_ms: u64,
) -> PyResult<(Vec<Vec<u8>>, bool)> {
    let operation_id = operation_id.to_owned();
    py.detach(move || {
        let (events, complete) = operations::wait_events(
            &operation_id,
            after_sequence,
            Duration::from_millis(timeout_ms),
        )?;
        let event_bytes = events
            .into_iter()
            .map(|event| codec::encode_message("encode OperationEvent", || event.to_cbor()))
            .collect::<PyResult<Vec<_>>>()?;
        Ok((event_bytes, complete))
    })
}

#[pyfunction]
fn operation_result(operation_id: &str) -> PyResult<Vec<u8>> {
    let result = operations::result(operation_id)?;
    codec::encode_message("encode OperationResult", || result.to_cbor())
}

#[pyfunction]
fn try_operation_result(operation_id: &str) -> PyResult<Option<Vec<u8>>> {
    operations::try_result(operation_id)?
        .map(|result| codec::encode_message("encode OperationResult", || result.to_cbor()))
        .transpose()
}

#[pymodule]
fn _gwz_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(health, module)?)?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_function(wrap_pyfunction!(call, module)?)?;
    module.add_function(wrap_pyfunction!(submit, module)?)?;
    module.add_function(wrap_pyfunction!(subscribe_events, module)?)?;
    module.add_function(wrap_pyfunction!(wait_events, module)?)?;
    module.add_function(wrap_pyfunction!(operation_result, module)?)?;
    module.add_function(wrap_pyfunction!(try_operation_result, module)?)?;
    Ok(())
}
