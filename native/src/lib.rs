use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

mod codec;
mod dispatch;
mod error;
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
fn subscribe_events(_operation_id: &str) -> PyResult<Vec<Vec<u8>>> {
    Err(PyRuntimeError::new_err(
        "gwz._gwz_core event subscription is not implemented yet",
    ))
}

#[pyfunction]
fn operation_result(_operation_id: &str) -> PyResult<Vec<u8>> {
    Err(PyRuntimeError::new_err(
        "gwz._gwz_core operation result lookup is not implemented yet",
    ))
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
