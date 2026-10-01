use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::types::{PyAnyMethods, PyBytes, PyDict, PyDictMethods, PyString};
use pyo3::{Bound, Py, PyAny, PyErr, PyErrArguments, PyResult, Python};

pub(crate) fn protocol(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

pub(crate) fn runtime(message: impl Into<String>) -> PyErr {
    PyRuntimeError::new_err(message.into())
}

/// A model error as Python sees it: a `RuntimeError` whose attributes carry
/// the error's code, member, record context and response metadata.
///
/// It is lazy, as `protocol` and `runtime` are: this keeps Rust values only,
/// and the exception and its attributes are built where Python first needs
/// them, when the error is raised or read with the GIL held. A thread that
/// makes one never attaches to the interpreter, so an operation that fails
/// after its client closed at interpreter exit touches no finalizing
/// interpreter (gwz-py `dev-docs/GwzPyPerOperationTransportDesign.md` §2.6).
pub(crate) fn model(error: gwz_core::model::ModelError) -> PyErr {
    PyErr::new::<PyRuntimeError, _>(ModelErrorValue::from(error))
}

/// What a model error's exception is built from.
struct ModelErrorValue {
    display: String,
    code: String,
    member_id: Option<String>,
    member_path: Option<String>,
    target_kind: Option<&'static str>,
    machine_message: String,
    response_meta: Option<Vec<u8>>,
    record_context: Option<RecordContext>,
}

struct RecordContext {
    merge_id: String,
    schema: Option<String>,
    record_schema_version: Option<i64>,
    required_wave: Option<String>,
    legacy_mode: Option<String>,
}

impl From<gwz_core::model::ModelError> for ModelErrorValue {
    fn from(error: gwz_core::model::ModelError) -> Self {
        let display = error.to_string();
        let response_meta = error
            .response_meta
            .as_ref()
            .map(|meta| gwz_core::encode(&meta.to_cbor()));
        let target_kind = if error.member_id.as_deref() == Some("@root")
            && error.member_path.as_deref() == Some(".")
        {
            Some("Root")
        } else {
            (error.member_id.is_some() || error.member_path.is_some()).then_some("Member")
        };
        let record_context = error.record_context.map(|context| {
            let context = *context;
            RecordContext {
                merge_id: context.merge_id,
                schema: context.schema,
                record_schema_version: context.record_schema_version,
                required_wave: context.required_wave.map(|wave| format!("{wave:?}")),
                legacy_mode: context.legacy_mode,
            }
        });
        Self {
            display,
            code: format!("{:?}", error.code),
            member_id: error.member_id,
            member_path: error.member_path,
            target_kind,
            machine_message: error.message,
            response_meta,
            record_context,
        }
    }
}

impl ModelErrorValue {
    /// The exception, with its attributes set.
    fn exception<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let value = py
            .get_type::<PyRuntimeError>()
            .call1((self.display.as_str(),))?;
        value.setattr("code", self.code.as_str())?;
        value.setattr("member_id", self.member_id.as_deref())?;
        value.setattr("member_path", self.member_path.as_deref())?;
        value.setattr("target_kind", self.target_kind)?;
        value.setattr("detail", None::<&str>)?;
        value.setattr("machine_message", self.machine_message.as_str())?;
        if let Some(bytes) = &self.response_meta {
            value.setattr("response_meta_cbor", PyBytes::new(py, bytes))?;
        }
        match &self.record_context {
            Some(context) => {
                let record = PyDict::new(py);
                record.set_item("merge_id", context.merge_id.as_str())?;
                record.set_item("schema", context.schema.as_deref())?;
                record.set_item("record_schema_version", context.record_schema_version)?;
                record.set_item("required_wave", context.required_wave.as_deref())?;
                record.set_item("legacy_mode", context.legacy_mode.as_deref())?;
                value.setattr("record_context", record)?;
            }
            None => {
                value.setattr("record_context", None::<&str>)?;
            }
        }
        Ok(value)
    }
}

impl PyErrArguments for ModelErrorValue {
    /// The exception itself, which raising takes as it is; or, when its
    /// attributes cannot be set, its message alone, from which raising makes
    /// a plain `RuntimeError`, as before.
    fn arguments(self, py: Python<'_>) -> Py<PyAny> {
        match self.exception(py) {
            Ok(value) => value.unbind(),
            Err(_) => PyString::new(py, &self.display).into_any().unbind(),
        }
    }
}

pub(crate) fn unsupported_method(method: &str) -> PyErr {
    protocol(format!("unsupported gwz-core method: {method}"))
}

pub(crate) fn unsupported<T>(method: &str) -> PyResult<T> {
    Err(unsupported_method(method))
}

cfg_if::cfg_if! {
    if #[cfg(test)] {
        mod tests {
            use std::sync::mpsc;
            use std::thread;
            use std::time::Duration;

            use gwz_core::model::{ErrorCode, ModelError};
            use pyo3::Python;
            use pyo3::exceptions::PyRuntimeError;
            use pyo3::types::{PyAnyMethods, PyDictMethods};

            /// A model error is made without the interpreter: while this
            /// test's thread holds the GIL, another thread makes one at once.
            /// An error that attached to build itself would wait for the GIL
            /// here, and after interpreter exit would attach to a finalizing
            /// interpreter (design §2.6). Raised or read, it is the
            /// `RuntimeError` the bridge reads, with every attribute.
            #[test]
            fn a_model_error_is_made_without_the_interpreter_and_read_with_its_attributes() {
                Python::initialize();
                let context = gwz_core::MergeRecordCompatibilityContext {
                    merge_id: "merge_1".to_owned(),
                    schema: Some("gwz.merge/v1".to_owned()),
                    record_schema_version: Some(3),
                    required_wave: Some(gwz_core::MergeRecordRequiredWave::A2),
                    legacy_mode: None,
                };
                let mut error = ModelError::new(ErrorCode::GitCommandFailed, "fetch failed")
                    .with_member("mem_app", "repos/app")
                    .with_record_context(context);
                let meta = gwz_core::ResponseMeta {
                    request_id: "req_lazy".to_owned(),
                    ..gwz_core::ResponseMeta::default()
                };
                let meta_bytes = gwz_core::encode(&meta.to_cbor());
                error.response_meta = Some(Box::new(meta));
                Python::attach(|py| {
                    let (sender, receiver) = mpsc::channel();
                    thread::spawn(move || {
                        let _ = sender.send(super::model(error));
                    });
                    let made = receiver
                        .recv_timeout(Duration::from_secs(2))
                        .expect("the error is made while another thread holds the GIL");
                    let value = made.value(py);
                    assert!(value.is_instance_of::<PyRuntimeError>());
                    let text = |name: &str| -> Option<String> {
                        value.getattr(name).unwrap().extract().unwrap()
                    };
                    assert_eq!(
                        value.str().unwrap().to_string(),
                        "GitCommandFailed: member 'mem_app' at 'repos/app': fetch failed"
                    );
                    assert_eq!(text("code").as_deref(), Some("GitCommandFailed"));
                    assert_eq!(text("member_id").as_deref(), Some("mem_app"));
                    assert_eq!(text("member_path").as_deref(), Some("repos/app"));
                    assert_eq!(text("target_kind").as_deref(), Some("Member"));
                    assert_eq!(text("detail"), None);
                    assert_eq!(
                        text("machine_message").as_deref(),
                        Some("member 'mem_app' at 'repos/app': fetch failed")
                    );
                    let cbor: Vec<u8> = value.getattr("response_meta_cbor").unwrap().extract().unwrap();
                    assert_eq!(cbor, meta_bytes);
                    let record = value.getattr("record_context").unwrap();
                    let record = record.cast::<pyo3::types::PyDict>().unwrap();
                    let item = |name: &str| record.get_item(name).unwrap().unwrap();
                    assert_eq!(item("merge_id").extract::<String>().unwrap(), "merge_1");
                    assert_eq!(item("schema").extract::<String>().unwrap(), "gwz.merge/v1");
                    assert_eq!(item("record_schema_version").extract::<i64>().unwrap(), 3);
                    assert_eq!(item("required_wave").extract::<String>().unwrap(), "A2");
                    assert!(item("legacy_mode").is_none());
                });
            }

            /// A model error at the root is the root's, and one that names
            /// no member carries neither a target nor response metadata.
            #[test]
            fn a_model_error_names_the_root_or_no_target() {
                Python::initialize();
                let root = super::model(
                    ModelError::new(ErrorCode::InvalidRequest, "refused").with_member("@root", "."),
                );
                let plain = super::model(ModelError::new(ErrorCode::IoError, "disk full"));
                Python::attach(|py| {
                    let root = root.value(py);
                    let kind: Option<String> = root.getattr("target_kind").unwrap().extract().unwrap();
                    assert_eq!(kind.as_deref(), Some("Root"));
                    let plain = plain.value(py);
                    assert!(plain.getattr("target_kind").unwrap().is_none());
                    assert!(plain.getattr("member_id").unwrap().is_none());
                    assert!(plain.getattr("record_context").unwrap().is_none());
                    assert!(!plain.hasattr("response_meta_cbor").unwrap());
                });
            }
        }
    }
}
