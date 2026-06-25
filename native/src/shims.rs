use pyo3::PyResult;

use crate::error;

pub(crate) fn operation_id(request_id: &str) -> String {
    format!("op_{request_id}")
}

pub(crate) fn no_backend<T>(
    request_id: &str,
    handler: impl FnOnce(String) -> gwz_core::model::ModelResult<T>,
) -> PyResult<T> {
    handler(operation_id(request_id)).map_err(|err| error::runtime(err.to_string()))
}

pub(crate) fn backend<T>(method: &str) -> PyResult<T> {
    Err(error::not_implemented(method))
}

pub(crate) fn backend_with_events<T>(method: &str) -> PyResult<T> {
    Err(error::not_implemented(method))
}
