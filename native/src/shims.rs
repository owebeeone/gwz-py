use pyo3::PyResult;

use crate::{error, operations};

fn with_backend<T>(action: impl FnOnce(&gwz_core::git::Git2Backend) -> T) -> T {
    action(&gwz_core::git::Git2Backend::new())
}

pub(crate) fn operation_id(request_id: &str) -> String {
    format!("op_{request_id}")
}

pub(crate) fn no_backend<T>(
    request_id: &str,
    handler: impl FnOnce(String) -> gwz_core::model::ModelResult<T>,
) -> PyResult<T> {
    handler(operation_id(request_id)).map_err(error::model)
}

pub(crate) fn backend<T>(
    request_id: &str,
    handler: impl FnOnce(&gwz_core::git::Git2Backend, String) -> gwz_core::model::ModelResult<T>,
) -> PyResult<T> {
    with_backend(|backend| handler(backend, operation_id(request_id))).map_err(error::model)
}

pub(crate) fn backend_with_events<T>(
    request_id: &str,
    handler: impl FnOnce(
        &gwz_core::git::Git2Backend,
        String,
        &dyn gwz_core::operation::EventSink,
    ) -> gwz_core::model::ModelResult<T>,
) -> PyResult<(T, operations::OperationRecorder)> {
    let operation_id = operation_id(request_id);
    let recorder = operations::begin(&operation_id);
    let response =
        backend_with_recorder(&operation_id, &recorder, handler).map_err(error::model)?;
    Ok((response, recorder))
}

pub(crate) fn backend_with_recorder<T>(
    operation_id: &str,
    recorder: &operations::OperationRecorder,
    handler: impl FnOnce(
        &gwz_core::git::Git2Backend,
        String,
        &dyn gwz_core::operation::EventSink,
    ) -> gwz_core::model::ModelResult<T>,
) -> gwz_core::model::ModelResult<T> {
    with_backend(|backend| handler(backend, operation_id.to_owned(), recorder))
}
