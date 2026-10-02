//! `ClientHost`, one `Client`'s host for its network operations (1.1.0 S6.2;
//! gwz-py `dev-docs/GwzPyPerOperationTransportDesign.md` §2.3–§2.7). The
//! bridge creates one for each `Client`; it is never a static, and gwz keeps
//! no registry of hosts.
//!
//! | Behaviour | Clause |
//! | --- | --- |
//! | `call` and `submit` are the native entries. gwz-core's transport scope decides which requests are network operations; only those take a snapshot, register with the host and count against its limit | §2.1, §2.3, §2.4 |
//! | The route and the snapshot are captured while the caller holds the GIL; the operation then runs with it released: a `call` on the caller's thread, a `submit` on its own `gwz-py-operation` thread, which is where a waiting operation waits | §2.3, §2.4 |
//! | `cancel_operation` and `close` wait with the GIL released, bounded by the cleanup bound, and so does the exit hook. An operation needs no GIL to end: its Python error is built only where it is raised or read (`error.rs`) | §2.5, §2.6 "No wait holds the GIL" |
//! | Each host registers its own exit callback with `atexit` when it is created, holding only a weak reference to the host's operations; `close` unregisters it once no operation is left running, and until then the callback marks the host as exiting and waits for them again | §2.6 "Interpreter exit" |
//! | An operation that ends after a close at exit records its outcome in the host and then attaches to no interpreter | §2.6 |

mod operations;

use std::sync::{Arc, Mutex, PoisonError, Weak};

use gwz_core::RequestMeta;
use gwz_core::git::Git2Backend;
use gwz_core::model::ModelError;
use gwz_core::session_host::CLEANUP_BOUND;
use gwz_core::transport_scope::{Operation, in_scope};
use pyo3::prelude::*;
use pyo3::types::{PyCFunction, PyDict, PyTuple};

pub(crate) use operations::{Canceller, Cleanup};
use operations::{Operations, Ticket};

use crate::route::{self, Route};
use crate::{codec, dispatch, error, shims};

#[pyclass(module = "gwz._gwz_core", frozen)]
pub(crate) struct ClientHost {
    operations: Arc<Operations>,
    /// Repository configuration files already reported for this Client.
    ignored_transport_files: route::Notices,
    /// The `atexit` callback, until `close` unregisters it.
    exit_hook: Mutex<Option<Py<PyAny>>>,
}

#[pymethods]
impl ClientHost {
    #[new]
    fn new(py: Python<'_>) -> PyResult<Self> {
        let operations = Arc::new(Operations::default());
        let exit_hook = register_exit_hook(py, Arc::downgrade(&operations))?;
        Ok(Self {
            operations,
            ignored_transport_files: route::Notices::default(),
            exit_hook: Mutex::new(Some(exit_hook)),
        })
    }

    /// Runs one request and returns its encoded response. A network
    /// operation waits for a slot on this thread.
    fn call(
        &self,
        py: Python<'_>,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
    ) -> PyResult<Vec<u8>> {
        let mut request_bytes = request_bytes.to_vec();
        let network = self.network(py, method, &mut request_bytes)?;
        let (method, request_message, response_message) = (
            method.to_owned(),
            request_message.to_owned(),
            response_message.to_owned(),
        );
        let run = move |backend: &shims::Backend<'_>| {
            // Dispatch takes the caller's directory from the request itself.
            dispatch::call(
                &method,
                &request_message,
                &response_message,
                &request_bytes,
                None,
                backend,
            )
        };
        let ran: Result<PyResult<Vec<u8>>, ModelError> = py.detach(move || match network {
            None => Ok(run(&shims::Backend::own())),
            Some(network) => {
                network
                    .run(|backend| run(&shims::Backend::given(backend)))
                    .result
            }
        });
        ran.unwrap_or_else(|refusal| Err(error::model(refusal)))
    }

    /// Accepts one request and returns its accepted response at once; the
    /// operation runs, or first waits for a slot, on its own thread.
    fn submit(
        &self,
        py: Python<'_>,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
    ) -> PyResult<Vec<u8>> {
        let mut request_bytes = request_bytes.to_vec();
        let network = self.network(py, method, &mut request_bytes)?;
        let (method, request_message, response_message) = (
            method.to_owned(),
            request_message.to_owned(),
            response_message.to_owned(),
        );
        py.detach(move || {
            dispatch::submit(
                &method,
                &request_message,
                &response_message,
                &request_bytes,
                network,
            )
        })
    }

    /// Cancels one of this host's network operations (§2.5) and returns its
    /// cleanup report as `(pending_local_work, peer_cleanup_confirmed)`.
    fn cancel_operation(&self, py: Python<'_>, operation_id: &str) -> PyResult<(usize, bool)> {
        let operations = Arc::clone(&self.operations);
        let operation_id = operation_id.to_owned();
        let cleanup = py
            .detach(move || operations.cancel(&operation_id, CLEANUP_BOUND))
            .map_err(error::model)?;
        Ok((cleanup.pending_local_work, cleanup.peer_cleanup_confirmed))
    }

    /// Closes the host (§2.6): cancels its waiting and running network
    /// operations, waits for them up to the cleanup bound and returns the
    /// host's report, the first close's on every later call.
    fn close(&self, py: Python<'_>) -> PyResult<(usize, bool)> {
        let operations = Arc::clone(&self.operations);
        let cleanup = py.detach(move || operations.close(false, CLEANUP_BOUND));
        if self.operations.is_idle() {
            let hook = self
                .exit_hook
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .take();
            if let Some(hook) = hook {
                py.import("atexit")?.call_method1("unregister", (hook,))?;
            }
        }
        Ok((cleanup.pending_local_work, cleanup.peer_cleanup_confirmed))
    }
}

impl ClientHost {
    /// The request as a network operation, if gwz-core's transport scope
    /// says it is one: its route captured and its registration made, here at
    /// the native entry, while the caller holds the GIL.
    fn network(
        &self,
        py: Python<'_>,
        method: &str,
        request_bytes: &mut Vec<u8>,
    ) -> PyResult<Option<Network>> {
        let Some(mut meta) = transport_meta(method, request_bytes) else {
            return Ok(None);
        };
        let route = route::capture(
            py,
            request_bytes,
            &mut meta,
            Operation::from_method(method).expect("transport scope has a method"),
            &self.ignored_transport_files,
        )?;
        let operation_id = shims::operation_id(&meta.request_id);
        let ticket = self
            .operations
            .register(operation_id.clone(), route.canceller())
            .map_err(error::model)?;
        Ok(Some(Network {
            ticket,
            route,
            meta,
            operation_id,
        }))
    }
}

/// The request's metadata when gwz-core's transport scope puts it inside a
/// transport runtime: the predicate gwz-cli's dispatch calls too (§2.1). A
/// request this cannot decode is not taken for one, and its dispatch refuses
/// it as before.
fn transport_meta(method: &str, request_bytes: &[u8]) -> Option<RequestMeta> {
    let operation = Operation::from_method(method)?;
    let request = codec::decode_cbor(request_bytes).ok()?;
    let meta = codec::catch_protocol("decode request metadata", || {
        RequestMeta::from_cbor(request.try_get(1)?)
    })
    .ok()?
    .ok()?;
    let tag = match operation {
        Operation::Tag => Some(
            codec::catch_protocol("decode TagRequest", || {
                gwz_core::TagRequest::from_cbor(&request)
            })
            .ok()?
            .ok()?,
        ),
        _ => None,
    };
    in_scope(operation, tag.as_ref()).then_some(meta)
}

/// One network operation, registered with its host and its route captured.
pub(crate) struct Network {
    ticket: Ticket,
    route: Route,
    meta: RequestMeta,
    operation_id: String,
}

/// What became of a network operation.
pub(crate) struct Ran<T> {
    /// The action's value, or the refusal that kept it from running or
    /// ended it: cancelled while waiting, or the entry's own failure.
    pub(crate) result: Result<T, ModelError>,
    /// The host closed at interpreter exit before the operation ended: its
    /// outcome is recorded in the host, and it must attach to no
    /// interpreter.
    pub(crate) exiting: bool,
}

impl Network {
    /// Waits for a slot, holding no GIL, then runs `action` on the
    /// operation's route, and records its outcome in the host.
    pub(crate) fn run<T>(self, action: impl FnOnce(&Git2Backend) -> T) -> Ran<T> {
        let Self {
            mut ticket,
            route,
            meta,
            operation_id,
        } = self;
        if let Err(refusal) = ticket.admit() {
            return Ran {
                result: Err(refusal),
                exiting: ticket.finish(None),
            };
        }
        let (result, cleanup) = route.run(meta, operation_id, action);
        Ran {
            result,
            exiting: ticket.finish(cleanup),
        }
    }
}

/// Registers the host's own `atexit` callback, holding only a weak reference
/// to its operations, so a dropped host leaks nothing but the callback.
fn register_exit_hook(py: Python<'_>, operations: Weak<Operations>) -> PyResult<Py<PyAny>> {
    let hook = PyCFunction::new_closure(
        py,
        Some(c"close_client_host_at_exit"),
        Some(c"Closes one gwz ClientHost at interpreter exit."),
        move |args: &Bound<'_, PyTuple>, _kwargs: Option<&Bound<'_, PyDict>>| {
            if let Some(operations) = operations.upgrade() {
                args.py()
                    .detach(move || operations.close(true, CLEANUP_BOUND));
            }
        },
    )?;
    py.import("atexit")?.call_method1("register", (&hook,))?;
    Ok(hook.into_any().unbind())
}
