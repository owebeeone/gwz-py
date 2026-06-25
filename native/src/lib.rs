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
fn subscribe_events(operation_id: &str) -> PyResult<Vec<Vec<u8>>> {
    operations::events(operation_id)?
        .into_iter()
        .map(|event| codec::encode_message("encode OperationEvent", || event.to_cbor()))
        .collect()
}

#[pyfunction]
fn operation_result(operation_id: &str) -> PyResult<Vec<u8>> {
    let result = operations::result(operation_id)?;
    codec::encode_message("encode OperationResult", || result.to_cbor())
}

#[pymodule]
fn _gwz_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(health, module)?)?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_function(wrap_pyfunction!(call, module)?)?;
    module.add_function(wrap_pyfunction!(subscribe_events, module)?)?;
    module.add_function(wrap_pyfunction!(operation_result, module)?)?;
    Ok(())
}
