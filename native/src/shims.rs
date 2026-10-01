//! The extension's backend scope: every handler runs on the backend its
//! dispatch hands it. For a network operation that is the backend of the
//! operation's own transport runtime, from the cancellable entry; for any
//! other request, and on libgit2's native route, it is `Git2Backend::new()`
//! (gwz-py `dev-docs/GwzPyPerOperationTransportDesign.md` §2.2, §2.4).

use std::cell::OnceCell;

use gwz_core::git::Git2Backend;
use pyo3::PyResult;

use crate::{error, operations};

/// The backend a request's handler runs on. A request's own backend is made
/// only when its handler first asks, as before: making one fixes the
/// process-wide transport timeout, which `configure_transport_runtime` must
/// still be able to choose first.
pub(crate) struct Backend<'a> {
    given: Option<&'a Git2Backend>,
    own: OnceCell<Git2Backend>,
}

impl<'a> Backend<'a> {
    /// A backend of the request's own, on libgit2's native route.
    pub(crate) fn own() -> Self {
        Self {
            given: None,
            own: OnceCell::new(),
        }
    }

    /// A network operation's route's backend.
    pub(crate) fn given(backend: &'a Git2Backend) -> Self {
        Self {
            given: Some(backend),
            own: OnceCell::new(),
        }
    }

    fn get(&self) -> &Git2Backend {
        match self.given {
            Some(backend) => backend,
            None => self.own.get_or_init(Git2Backend::new),
        }
    }
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
    backend: &Backend<'_>,
    request_id: &str,
    handler: impl FnOnce(&Git2Backend, String) -> gwz_core::model::ModelResult<T>,
) -> PyResult<T> {
    handler(backend.get(), operation_id(request_id)).map_err(error::model)
}

pub(crate) fn backend_with_events<T>(
    backend: &Backend<'_>,
    request_id: &str,
    handler: impl FnOnce(
        &Git2Backend,
        String,
        &dyn gwz_core::operation::EventSink,
    ) -> gwz_core::model::ModelResult<T>,
) -> PyResult<(T, operations::OperationRecorder)> {
    let operation_id = operation_id(request_id);
    let recorder = operations::begin(&operation_id);
    let response = handler(backend.get(), operation_id.clone(), &recorder).map_err(error::model)?;
    Ok((response, recorder))
}

pub(crate) fn backend_with_recorder<T>(
    backend: &Backend<'_>,
    operation_id: &str,
    recorder: &operations::OperationRecorder,
    handler: impl FnOnce(
        &Git2Backend,
        String,
        &dyn gwz_core::operation::EventSink,
    ) -> gwz_core::model::ModelResult<T>,
) -> gwz_core::model::ModelResult<T> {
    handler(backend.get(), operation_id.to_owned(), recorder)
}
