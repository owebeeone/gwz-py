use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::types::PyAnyMethods;
use pyo3::{PyErr, PyResult, Python};

pub(crate) fn protocol(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

pub(crate) fn runtime(message: impl Into<String>) -> PyErr {
    PyRuntimeError::new_err(message.into())
}

pub(crate) fn model(error: gwz_core::model::ModelError) -> PyErr {
    let display = error.to_string();
    let code = format!("{:?}", error.code);
    let member_id = error.member_id;
    let member_path = error.member_path;
    let target_kind = (member_id.is_some() || member_path.is_some()).then(|| "Member".to_owned());
    let machine_message = error.message;
    let exception = PyRuntimeError::new_err(display.clone());
    let attached = Python::attach(|py| -> PyResult<()> {
        let value = exception.value(py);
        value.setattr("code", code)?;
        value.setattr("member_id", member_id)?;
        value.setattr("member_path", member_path)?;
        value.setattr("target_kind", target_kind)?;
        value.setattr("detail", None::<String>)?;
        value.setattr("machine_message", machine_message)?;
        Ok(())
    });
    if attached.is_err() {
        return PyRuntimeError::new_err(display);
    }
    exception
}

pub(crate) fn unsupported_method(method: &str) -> PyErr {
    protocol(format!("unsupported gwz-core method: {method}"))
}

pub(crate) fn unsupported<T>(method: &str) -> PyResult<T> {
    Err(unsupported_method(method))
}
