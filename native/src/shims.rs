use pyo3::PyResult;
use std::cell::RefCell;

use crate::{error, operations};

thread_local! {
    static SCOPED_BACKEND: RefCell<Option<gwz_core::git::Git2Backend>> = const { RefCell::new(None) };
}

/// Keep the admitted backend alive only while the matching native dispatch runs.
/// The guard restores a prior value even when a handler unwinds.
pub(crate) fn with_scoped_backend<T>(
    backend: gwz_core::git::Git2Backend,
    action: impl FnOnce() -> T,
) -> T {
    struct Restore(Option<gwz_core::git::Git2Backend>);
    impl Drop for Restore {
        fn drop(&mut self) {
            SCOPED_BACKEND.with(|slot| {
                *slot.borrow_mut() = self.0.take();
            });
        }
    }
    let previous = SCOPED_BACKEND.with(|slot| slot.borrow_mut().replace(backend));
    let restore = Restore(previous);
    let result = action();
    drop(restore);
    result
}

fn with_backend<T>(action: impl FnOnce(&gwz_core::git::Git2Backend) -> T) -> T {
    SCOPED_BACKEND.with(|slot| {
        let scoped = slot.borrow();
        if let Some(backend) = scoped.as_ref() {
            action(backend)
        } else {
            action(&gwz_core::git::Git2Backend::new())
        }
    })
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
