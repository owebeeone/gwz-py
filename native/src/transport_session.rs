//! Python's one persistent, lazily constructed core transport host.
//! This module is candidate-only until the ordinary core graph activates it.
use std::cell::RefCell;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};

use gwz_core::transport_host::{CleanupReport, TransportCancellation, TransportRuntime};
use pyo3::prelude::*;

use crate::{codec, dispatch, error, shims};

thread_local! {
    static CURRENT_SESSION: RefCell<Option<TransportSession>> = const { RefCell::new(None) };
}

pub(crate) fn current_session() -> Option<TransportSession> {
    CURRENT_SESSION.with(|slot| slot.borrow().clone())
}

fn with_current_session<T>(session: TransportSession, action: impl FnOnce() -> T) -> T {
    struct Restore(Option<TransportSession>);
    impl Drop for Restore {
        fn drop(&mut self) {
            CURRENT_SESSION.with(|slot| *slot.borrow_mut() = self.0.take());
        }
    }
    let previous = CURRENT_SESSION.with(|slot| slot.borrow_mut().replace(session));
    let restore = Restore(previous);
    let result = action();
    drop(restore);
    result
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Status {
    Open,
    Closing,
    Closed,
}

struct Active {
    operation_id: String,
    cancellation: TransportCancellation,
    done: Arc<Completion>,
    cancel_requested: bool,
}

struct Admitting {
    operation_id: String,
    done: Arc<Completion>,
    cancel_requested: bool,
    started: bool,
}

struct State {
    status: Status,
    constructing: bool,
    admitting: Option<Admitting>,
    runtime: Option<Arc<TransportRuntime>>,
    active: Option<Active>,
    constructor_cleanup: Option<CleanupReport>,
    close_report: Option<CleanupReport>,
    last_cancel: Option<(String, CleanupReport)>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            status: Status::Open,
            constructing: false,
            admitting: None,
            runtime: None,
            active: None,
            constructor_cleanup: None,
            close_report: None,
            last_cancel: None,
        }
    }
}

#[derive(Default)]
struct Completion {
    report: Mutex<Option<CleanupReport>>,
    changed: Condvar,
}

impl Completion {
    fn finish(&self, report: CleanupReport) {
        *self.report.lock().unwrap_or_else(|e| e.into_inner()) = Some(report);
        self.changed.notify_all();
    }

    fn wait(&self) -> CleanupReport {
        let mut report = self.report.lock().unwrap_or_else(|e| e.into_inner());
        loop {
            if let Some(report) = &*report {
                return report.clone();
            }
            report = self.changed.wait(report).unwrap_or_else(|e| e.into_inner());
        }
    }
}

struct Inner {
    state: Mutex<State>,
    changed: Condvar,
    executor: tokio::runtime::Runtime,
}

#[pyclass(module = "gwz._gwz_core", skip_from_py_object)]
#[derive(Clone)]
pub(crate) struct TransportSession {
    inner: Arc<Inner>,
}

impl Drop for TransportSession {
    fn drop(&mut self) {
        if Arc::strong_count(&self.inner) != 1 {
            return;
        }
        let open = self
            .inner
            .state
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .status
            == Status::Open;
        if open {
            let session = self.clone();
            let _ = std::thread::Builder::new()
                .name("gwz-py-transport-close".into())
                .spawn(move || {
                    let _ = session.close_inner();
                });
        }
    }
}

impl TransportSession {
    fn new() -> PyResult<Self> {
        let executor = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .map_err(|_| error::runtime("transport executor unavailable"))?;
        Ok(Self {
            inner: Arc::new(Inner {
                state: Mutex::new(State::default()),
                changed: Condvar::new(),
                executor,
            }),
        })
    }

    fn closed() -> PyErr {
        error::model(gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::InvalidRequest,
            "client is closed",
        ))
    }

    fn busy() -> PyErr {
        error::model(gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::InvalidRequest,
            "another network operation is active",
        ))
    }

    fn wrong_operation() -> PyErr {
        error::model(gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::InvalidRequest,
            "operation is not owned by this session",
        ))
    }

    fn ensure_open(&self) -> PyResult<()> {
        if self
            .inner
            .state
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .status
            != Status::Open
        {
            return Err(Self::closed());
        }
        Ok(())
    }

    fn reserve_inner(&self, operation_id: String) -> PyResult<()> {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.status != Status::Open {
            return Err(Self::closed());
        }
        if state.admitting.is_some() || state.active.is_some() {
            return Err(Self::busy());
        }
        state.admitting = Some(Admitting {
            operation_id,
            done: Arc::new(Completion::default()),
            cancel_requested: false,
            started: false,
        });
        Ok(())
    }

    fn abandon_unstarted(&self, operation_id: Option<&str>) {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        let matching = state.admitting.as_ref().is_some_and(|admitting| {
            !admitting.started
                && operation_id.is_none_or(|operation_id| operation_id == admitting.operation_id)
        });
        if matching {
            let admitting = state.admitting.take().expect("matching admission");
            let report = CleanupReport::default();
            if admitting.cancel_requested {
                state.last_cancel = Some((admitting.operation_id, report.clone()));
            }
            admitting.done.finish(report);
            self.inner.changed.notify_all();
        }
    }

    fn runtime(&self) -> PyResult<Arc<TransportRuntime>> {
        loop {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.status != Status::Open {
                return Err(Self::closed());
            }
            if let Some(runtime) = &state.runtime {
                return Ok(runtime.clone());
            }
            if state.constructing {
                state = self
                    .inner
                    .changed
                    .wait(state)
                    .unwrap_or_else(|e| e.into_inner());
                drop(state);
                continue;
            }
            state.constructing = true;
            drop(state);
            let built = self
                .inner
                .executor
                .block_on(async { TransportRuntime::from_environment() });
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.status != Status::Open {
                drop(state);
                let cleanup = built
                    .ok()
                    .map(|runtime| self.inner.executor.block_on(runtime.shutdown()));
                let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
                state.constructor_cleanup = cleanup;
                state.constructing = false;
                self.inner.changed.notify_all();
                return Err(Self::closed());
            }
            state.constructing = false;
            match built {
                Ok(runtime) => {
                    let runtime = Arc::new(runtime);
                    state.runtime = Some(runtime.clone());
                    self.inner.changed.notify_all();
                    return Ok(runtime);
                }
                Err(err) => {
                    self.inner.changed.notify_all();
                    return Err(error::model(err));
                }
            }
        }
    }

    fn call_inner(
        &self,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
        caller_cwd: PathBuf,
    ) -> PyResult<Vec<u8>> {
        if method == "transport_capabilities" {
            self.ensure_open()?;
            let request = codec::decode_message(
                request_bytes,
                "decode TransportCapabilitiesRequest",
                |cbor| gwz_core::TransportCapabilitiesRequest::from_cbor(cbor),
            )?;
            let response = self
                .runtime()?
                .capabilities(request)
                .map_err(error::model)?;
            return codec::encode_message("encode TransportCapabilitiesResponse", || {
                response.to_cbor()
            });
        }
        let meta = match network_meta(method, request_bytes) {
            Ok(meta) => meta,
            Err(err) => {
                self.abandon_unstarted(None);
                return Err(err);
            }
        };
        let Some(meta) = meta else {
            self.ensure_open()?;
            return dispatch::call(
                method,
                request_message,
                response_message,
                request_bytes,
                caller_cwd,
            );
        };
        let operation_id = shims::operation_id(&meta.request_id);
        {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.status != Status::Open {
                drop(state);
                self.abandon_unstarted(Some(&operation_id));
                return Err(Self::closed());
            }
            if state.active.is_some() {
                return Err(Self::busy());
            }
            if let Some(admitting) = &mut state.admitting {
                if admitting.operation_id != operation_id || admitting.started {
                    return Err(Self::busy());
                }
                admitting.started = true;
                if admitting.cancel_requested {
                    drop(state);
                    self.end_admission(CleanupReport::default());
                    return Err(error::runtime("operation cancelled"));
                }
            } else {
                state.admitting = Some(Admitting {
                    operation_id: operation_id.clone(),
                    done: Arc::new(Completion::default()),
                    cancel_requested: false,
                    started: true,
                });
            }
        }
        let runtime = match self.runtime() {
            Ok(runtime) => runtime,
            Err(err) => {
                self.end_admission(CleanupReport::default());
                return Err(err);
            }
        };
        let request = match self
            .inner
            .executor
            .block_on(runtime.request(meta, operation_id.clone()))
        {
            Ok(request) => request,
            Err(err) => {
                self.end_admission(CleanupReport::default());
                return Err(error::model(err));
            }
        };
        let done;
        {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            let admitting = state.admitting.take().expect("reserved admission");
            if state.status != Status::Open || admitting.cancel_requested {
                let cancelled = admitting.cancel_requested;
                let done = admitting.done.clone();
                state.admitting = Some(admitting);
                drop(state);
                request.cancel();
                let report = self.inner.executor.block_on(request.finish());
                let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
                state.admitting = None;
                if cancelled {
                    state.last_cancel = Some((operation_id, report.clone()));
                }
                done.finish(report);
                self.inner.changed.notify_all();
                return Err(Self::closed());
            }
            done = admitting.done;
            state.active = Some(Active {
                operation_id: operation_id.clone(),
                cancellation: request.cancellation_handle(),
                done: done.clone(),
                cancel_requested: false,
            });
            self.inner.changed.notify_all();
        }
        let result = catch_unwind(AssertUnwindSafe(|| {
            shims::with_scoped_backend(request.backend().clone(), || {
                dispatch::call(
                    method,
                    request_message,
                    response_message,
                    request_bytes,
                    caller_cwd,
                )
            })
        }))
        .unwrap_or_else(|_| Err(error::runtime("native operation failed")));
        let report = self.inner.executor.block_on(request.finish());
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(active) = state.active.take() {
            if active.cancel_requested {
                state.last_cancel = Some((operation_id, report.clone()));
            }
        }
        done.finish(report);
        self.inner.changed.notify_all();
        result
    }

    fn end_admission(&self, report: CleanupReport) {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(admitting) = state.admitting.take() {
            if admitting.cancel_requested {
                state.last_cancel = Some((admitting.operation_id, report.clone()));
            }
            admitting.done.finish(report);
        }
        self.inner.changed.notify_all();
    }

    pub(crate) fn spawned_call(
        &self,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
        caller_cwd: PathBuf,
    ) -> PyResult<Vec<u8>> {
        self.call_inner(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
        )
    }

    fn close_inner(&self) -> CleanupReport {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        match state.status {
            Status::Closed => return state.close_report.clone().unwrap_or_default(),
            Status::Closing => {
                while state.status != Status::Closed {
                    state = self
                        .inner
                        .changed
                        .wait(state)
                        .unwrap_or_else(|e| e.into_inner());
                }
                return state.close_report.clone().unwrap_or_default();
            }
            Status::Open => state.status = Status::Closing,
        }
        loop {
            if let Some(admitting) = &mut state.admitting {
                admitting.cancel_requested = true;
            }
            if let Some(active) = &mut state.active {
                active.cancellation.cancel();
                active.cancel_requested = true;
            }
            if !state.constructing
                && state.admitting.is_none()
                && state.active.is_none()
            {
                break;
            }
            state = self
                .inner
                .changed
                .wait(state)
                .unwrap_or_else(|e| e.into_inner());
        }
        let runtime = state.runtime.take();
        let constructor_cleanup = state.constructor_cleanup.take();
        drop(state);
        let report = if let Some(runtime) = runtime {
            self.inner.executor.block_on(runtime.shutdown())
        } else {
            constructor_cleanup.unwrap_or_default()
        };
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        state.close_report = Some(report.clone());
        state.status = Status::Closed;
        self.inner.changed.notify_all();
        report
    }

    fn cancel_inner(&self, operation_id: &str) -> PyResult<CleanupReport> {
        let (cancel, done) = {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(admitting) = &mut state.admitting {
                if admitting.operation_id == operation_id {
                    admitting.cancel_requested = true;
                    let done = admitting.done.clone();
                    drop(state);
                    return Ok(done.wait());
                }
                return Err(Self::wrong_operation());
            }
            if let Some(active) = &mut state.active {
                if active.operation_id == operation_id {
                    active.cancel_requested = true;
                    (active.cancellation.clone(), active.done.clone())
                } else {
                    if let Some((last, report)) = &state.last_cancel {
                        if last == operation_id {
                            return Ok(report.clone());
                        }
                    }
                    return Err(Self::wrong_operation());
                }
            } else if let Some((last, report)) = &state.last_cancel {
                if last == operation_id {
                    return Ok(report.clone());
                }
                return Err(Self::wrong_operation());
            } else {
                return Err(Self::wrong_operation());
            }
        };
        cancel.cancel();
        Ok(done.wait())
    }
}

#[pymethods]
impl TransportSession {
    #[new]
    fn py_new(py: Python<'_>) -> PyResult<Self> {
        py.detach(Self::new)
    }

    fn reserve_operation(&self, py: Python<'_>, operation_id: &str) -> PyResult<()> {
        let session = self.clone();
        let operation_id = operation_id.to_owned();
        py.detach(move || session.reserve_inner(operation_id))
    }

    fn call(
        &self,
        py: Python<'_>,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
    ) -> PyResult<Vec<u8>> {
        let method = method.to_owned();
        let request_message = request_message.to_owned();
        let response_message = response_message.to_owned();
        let request_bytes = request_bytes.to_vec();
        let caller_cwd =
            std::env::current_dir().map_err(|_| error::runtime("current_dir failed"))?;
        let session = self.clone();
        py.detach(move || {
            session.call_inner(
                &method,
                &request_message,
                &response_message,
                &request_bytes,
                caller_cwd,
            )
        })
    }

    fn submit(
        &self,
        py: Python<'_>,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
    ) -> PyResult<Vec<u8>> {
        let method = method.to_owned();
        let request_message = request_message.to_owned();
        let response_message = response_message.to_owned();
        let request_bytes = request_bytes.to_vec();
        let caller_cwd =
            std::env::current_dir().map_err(|_| error::runtime("current_dir failed"))?;
        let session = self.clone();
        py.detach(move || {
            let meta = match network_meta(&method, &request_bytes) {
                Ok(meta) => meta,
                Err(err) => {
                    session.abandon_unstarted(None);
                    return Err(err);
                }
            };
            let network = meta.is_some();
            if !network {
                session.ensure_open()?;
            }
            if network {
                let operation_id = shims::operation_id(&meta.expect("network meta").request_id);
                let mut state = session
                    .inner
                    .state
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                if state.status != Status::Open {
                    drop(state);
                    session.abandon_unstarted(Some(&operation_id));
                    return Err(Self::closed());
                }
                if state.constructing || state.active.is_some() {
                    return Err(Self::busy());
                }
                if let Some(admitting) = &state.admitting {
                    if admitting.operation_id != operation_id || admitting.started {
                        return Err(Self::busy());
                    }
                } else {
                    state.admitting = Some(Admitting {
                        operation_id,
                        done: Arc::new(Completion::default()),
                        cancel_requested: false,
                        started: false,
                    });
                }
            }
            let result = with_current_session(session.clone(), || {
                dispatch::submit(
                    &method,
                    &request_message,
                    &response_message,
                    &request_bytes,
                    caller_cwd,
                )
            });
            if network && result.is_err() {
                let mut state = session
                    .inner
                    .state
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                if let Some(admitting) = state.admitting.take() {
                    let report = CleanupReport::default();
                    if admitting.cancel_requested {
                        state.last_cancel = Some((admitting.operation_id, report.clone()));
                    }
                    admitting.done.finish(report);
                }
                session.inner.changed.notify_all();
            }
            result
        })
    }

    fn close(&self, py: Python<'_>) -> PyResult<(usize, bool)> {
        let session = self.clone();
        py.detach(move || {
            let report = session.close_inner();
            Ok((report.pending_local_work, report.peer_cleanup_confirmed))
        })
    }

    fn cancel_operation(&self, py: Python<'_>, operation_id: &str) -> PyResult<(usize, bool)> {
        let operation_id = operation_id.to_owned();
        let session = self.clone();
        py.detach(move || {
            let report = session.cancel_inner(&operation_id)?;
            Ok((report.pending_local_work, report.peer_cleanup_confirmed))
        })
    }
}

fn network_meta(method: &str, bytes: &[u8]) -> PyResult<Option<gwz_core::RequestMeta>> {
    let network = matches!(
        method,
        "init_from_sources"
            | "materialize"
            | "clone_workspace"
            | "clone_repo_member"
            | "attach_repo_member"
            | "pull_head"
            | "pull_snapshot"
            | "push"
            | "fetch"
    );
    if method == "tag" {
        let tag =
            codec::decode_message(bytes, "decode TagRequest", gwz_core::TagRequest::from_cbor)?;
        let uses_remote = matches!(tag.op, gwz_core::TagOp::Push | gwz_core::TagOp::Fetch)
            || (matches!(tag.op, gwz_core::TagOp::List | gwz_core::TagOp::Delete)
                && tag.remote.is_some());
        return Ok(uses_remote.then_some(tag.meta));
    }
    if !network {
        return Ok(None);
    }
    let cbor = codec::decode_cbor(bytes)?;
    let meta = codec::catch_protocol("decode request metadata", || {
        gwz_core::RequestMeta::from_cbor(cbor.try_get(1)?)
    })?
    .map_err(|_| error::protocol("decode request metadata failed"))?;
    Ok(Some(meta))
}
