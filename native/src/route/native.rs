//! libgit2's native route, for every network operation of a build without
//! the transport. It captures nothing, builds no runtime, cannot cancel a
//! running operation and reports no transport cleanup.

use gwz_core::RequestMeta;
use gwz_core::git::Git2Backend;
use gwz_core::model::ModelResult;
use gwz_core::transport_scope::Operation;
use pyo3::PyResult;

use crate::client_host::{Canceller, Cleanup};

pub(crate) struct Route;

#[derive(Default)]
pub(crate) struct Notices {}

/// The route of a network operation: always the native one here, which
/// leaves the request as it is.
pub(crate) fn capture(
    _py: pyo3::Python<'_>,
    _request_bytes: &mut Vec<u8>,
    _meta: &mut RequestMeta,
    _operation: Operation,
    _ignored_files: &Notices,
) -> PyResult<Route> {
    Ok(Route)
}

impl Route {
    /// None: the native route cannot cancel a running operation.
    pub(crate) fn canceller(&self) -> Option<Canceller> {
        None
    }

    /// Runs `action` on a backend of its own, which takes libgit2's native
    /// route; it builds nothing, so there is no cleanup report.
    pub(crate) fn run<T>(
        self,
        _meta: RequestMeta,
        _operation_id: String,
        action: impl FnOnce(&Git2Backend) -> T,
    ) -> (ModelResult<T>, Option<Cleanup>) {
        (Ok(action(&Git2Backend::new())), None)
    }
}
