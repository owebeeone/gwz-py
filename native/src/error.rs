use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::types::{PyAnyMethods, PyDict};
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
    let record_context = error.record_context.map(|context| {
        let context = *context;
        (
            context.merge_id,
            context.schema,
            context.record_schema_version,
            context.required_wave.map(|wave| format!("{wave:?}")),
            context.legacy_mode,
        )
    });
    let target_kind =
        if member_id.as_deref() == Some("@root") && member_path.as_deref() == Some(".") {
            Some("Root".to_owned())
        } else {
            (member_id.is_some() || member_path.is_some()).then(|| "Member".to_owned())
        };
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
        if let Some((merge_id, schema, version, required_wave, legacy_mode)) = record_context {
            let context = PyDict::new(py);
            context.set_item("merge_id", merge_id)?;
            context.set_item("schema", schema)?;
            context.set_item("record_schema_version", version)?;
            context.set_item("required_wave", required_wave)?;
            context.set_item("legacy_mode", legacy_mode)?;
            value.setattr("record_context", context)?;
        } else {
            value.setattr("record_context", None::<String>)?;
        }
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
