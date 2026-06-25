use std::env;
use std::panic::{catch_unwind, AssertUnwindSafe};

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

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

    py.detach(move || match method.as_str() {
        "ls" => call_ls(&request_message, &response_message, &request_bytes),
        other => Err(PyValueError::new_err(format!(
            "unsupported gwz-core method: {other}"
        ))),
    })
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

fn call_ls(
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    if request_message != "LsRequest" {
        return Err(PyValueError::new_err(format!(
            "ls expects LsRequest, got {request_message}"
        )));
    }
    if response_message != "LsResponse" {
        return Err(PyValueError::new_err(format!(
            "ls returns LsResponse, got {response_message}"
        )));
    }

    let request_cbor = decode_cbor(request_bytes)?;
    let request = catch_protocol("decode LsRequest", || {
        gwz_core::LsRequest::from_cbor(&request_cbor)
    })?;
    let start = env::current_dir()
        .map_err(|err| PyRuntimeError::new_err(format!("current_dir failed: {err}")))?;
    let operation_id = format!("op_{}", request.meta.request_id);
    let response = gwz_core::workspace_ops::handle_ls(&start, request, operation_id)
        .map_err(|err| PyRuntimeError::new_err(err.to_string()))?;
    let response_cbor = catch_protocol("encode LsResponse", || response.to_cbor())?;
    Ok(gwz_core::encode(&response_cbor))
}

fn decode_cbor(data: &[u8]) -> PyResult<gwz_core::Cbor> {
    catch_protocol("decode CBOR", || gwz_core::decode(data))
}

fn catch_protocol<T>(
    context: &'static str,
    f: impl FnOnce() -> T,
) -> PyResult<T> {
    catch_unwind(AssertUnwindSafe(f))
        .map_err(|_| PyValueError::new_err(format!("{context} failed")))
}
