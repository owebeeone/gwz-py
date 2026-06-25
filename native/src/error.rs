use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::{PyErr, PyResult};

pub(crate) fn protocol(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

pub(crate) fn runtime(message: impl Into<String>) -> PyErr {
    PyRuntimeError::new_err(message.into())
}

pub(crate) fn unsupported_method(method: &str) -> PyErr {
    protocol(format!("unsupported gwz-core method: {method}"))
}

pub(crate) fn unsupported<T>(method: &str) -> PyResult<T> {
    Err(unsupported_method(method))
}
