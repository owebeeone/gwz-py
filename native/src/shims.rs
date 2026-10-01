//! The extension's backend scope: every handler runs on the backend its
//! dispatch hands it. For a network operation that is the backend of the
//! operation's own transport runtime, from the cancellable entry; for any
//! other request, and on libgit2's native route, it is `Git2Backend::new()`
//! (gwz-py `dev-docs/GwzPyPerOperationTransportDesign.md` §2.2, §2.4). A
//! submitted operation's dispatch also hands its handler the record its
//! submit began, which the handler reports to.

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
    /// A submitted operation's record, which its submit began.
    recorder: Option<&'a operations::OperationRecorder>,
}

impl<'a> Backend<'a> {
    /// A backend of the request's own, on libgit2's native route.
    pub(crate) fn own() -> Self {
        Self {
            given: None,
            own: OnceCell::new(),
            recorder: None,
        }
    }

    /// A network operation's route's backend.
    pub(crate) fn given(backend: &'a Git2Backend) -> Self {
        Self {
            given: Some(backend),
            own: OnceCell::new(),
            recorder: None,
        }
    }

    /// The same backend, for a submitted operation whose handler reports to
    /// `recorder`, the record its submit began, rather than beginning one.
    pub(crate) fn reporting_to(self, recorder: &'a operations::OperationRecorder) -> Self {
        Self {
            recorder: Some(recorder),
            ..self
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

/// Runs a handler that reports events on its operation's record: for a
/// submitted operation the record its submit began, and for a call one begun
/// here, which a request ID that matches a live operation's refuses before
/// the handler runs. A call whose handler fails ends that record with the
/// failure, so its request ID is free again once the call returns.
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
    let (recorder, begun_here) = match backend.recorder {
        Some(submitted) => (submitted.clone(), false),
        None => (
            operations::begin(&operation_id).map_err(error::model)?,
            true,
        ),
    };
    match handler(backend.get(), operation_id, &recorder) {
        Ok(response) => Ok((response, recorder)),
        Err(failure) => {
            if begun_here {
                recorder.refuse(failure.clone());
            }
            Err(error::model(failure))
        }
    }
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
