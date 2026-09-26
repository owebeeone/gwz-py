//! Python's one persistent, lazily constructed core transport host.
//! This module is candidate-only until the ordinary core graph activates it.
use std::cell::RefCell;
use std::collections::HashMap;
use std::fs::File;
use std::io::Read;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::PathBuf;
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Condvar, Mutex};

use gwz_core::transport_host::{CleanupReport, TransportCancellation, TransportRuntime};
use pyo3::prelude::*;

use crate::{codec, dispatch, error, operations, shims};

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
    cancellation: TransportCancellation,
    done: Arc<Completion>,
    cancel_requested: bool,
}

struct Admitting {
    operation_id: String,
    done: Arc<Completion>,
    cancel_requested: bool,
    started: bool,
    signal: Option<Sender<Result<(), AdmissionFailure>>>,
    failure: Option<AdmissionFailure>,
    failure_report: Option<CleanupReport>,
}

enum AdmissionFailure {
    Model(gwz_core::model::ModelError),
    Runtime(String),
}

struct State {
    status: Status,
    faulted: bool,
    constructing: bool,
    admitting: HashMap<String, Admitting>,
    runtime: Option<Arc<TransportRuntime>>,
    active: HashMap<String, Active>,
    constructor_cleanup: Option<CleanupReport>,
    close_report: Option<CleanupReport>,
    last_cancel: HashMap<String, CleanupReport>,
    request_operations: HashMap<String, String>,
    next_serial: u64,
}

impl Default for State {
    fn default() -> Self {
        Self {
            status: Status::Open,
            faulted: false,
            constructing: false,
            admitting: HashMap::new(),
            runtime: None,
            active: HashMap::new(),
            constructor_cleanup: None,
            close_report: None,
            last_cancel: HashMap::new(),
            request_operations: HashMap::new(),
            next_serial: 0,
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
    operations: Arc<operations::OperationStore>,
    nonce: [u8; 16],
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
        let mut nonce = [0_u8; 16];
        File::open("/dev/urandom")
            .and_then(|mut source| source.read_exact(&mut nonce))
            .map_err(|_| error::runtime("operation identity source unavailable"))?;
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
                operations: Arc::new(operations::OperationStore::default()),
                nonce,
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

    fn full_error() -> gwz_core::model::ModelError {
        gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::TransportSessionFull,
            "transport session has eight live operations",
        )
    }

    fn refuse_full(&self, operation_id: &str) -> PyErr {
        let refusal = Self::full_error();
        let _ = self.inner.operations.refuse(operation_id, refusal.clone());
        error::model(refusal)
    }

    fn duplicate_request() -> PyErr {
        error::model(gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::InvalidRequest,
            "request_id is already issued in this transport generation",
        ))
    }

    fn wrong_operation() -> PyErr {
        error::model(gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::InvalidRequest,
            "operation is not owned by this session",
        ))
    }

    fn cancelled_before_admission() -> gwz_core::model::ModelError {
        gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::Cancelled,
            "operation cancelled before admission",
        )
    }

    fn expired_operation() -> PyErr {
        error::model(gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::OperationExpired,
            "operation record has expired or been released",
        ))
    }

    fn issued_serial(&self, operation_id: &str) -> Option<u64> {
        let prefix = format!("op_{:032x}_", u128::from_be_bytes(self.inner.nonce));
        let part = operation_id.strip_prefix(&prefix)?;
        if part.is_empty() || !part.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        let serial = part.parse::<u64>().ok()?;
        (serial != 0 && serial.to_string() == part).then_some(serial)
    }

    /// Reject foreign and never-issued IDs before touching this session's
    /// ledger. The high-water mark replaces a lifetime tombstone table.
    fn validate_operation_id(&self, operation_id: &str) -> PyResult<()> {
        let serial = self.issued_serial(operation_id);
        let state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if !serial.is_some_and(|serial| serial <= state.next_serial) {
            return Err(Self::wrong_operation());
        }
        if !self.inner.operations.contains(operation_id) {
            return Err(Self::expired_operation());
        }
        Ok(())
    }

    fn ensure_open(&self) -> PyResult<()> {
        let state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.status != Status::Open || state.faulted {
            return Err(Self::closed());
        }
        Ok(())
    }

    fn reserve_inner(&self, request_id: String) -> PyResult<String> {
        if request_id.is_empty()
            || request_id.len() > 128
            || request_id.chars().any(char::is_control)
        {
            return Err(error::model(gwz_core::model::ModelError::new(
                gwz_core::model::ErrorCode::InvalidRequest,
                "invalid request_id",
            )));
        }
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.status != Status::Open || state.faulted {
            return Err(Self::closed());
        }
        if state.request_operations.contains_key(&request_id) {
            return Err(Self::duplicate_request());
        }
        let serial = state
            .next_serial
            .checked_add(1)
            .ok_or_else(|| error::runtime("operation identity space exhausted"))?;
        let operation_id = format!(
            "op_{:032x}_{}",
            u128::from_be_bytes(self.inner.nonce),
            serial
        );
        self.inner.operations.issue(&operation_id)?;
        state.next_serial = serial;
        state
            .request_operations
            .insert(request_id, operation_id.clone());
        Ok(operation_id)
    }

    fn operation_for_request(&self, request_id: &str, expected: Option<&str>) -> PyResult<String> {
        if let Some(operation_id) = expected {
            self.validate_operation_id(operation_id)?;
            let state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.last_cancel.contains_key(operation_id) {
                return Err(error::model(gwz_core::model::ModelError::new(
                    gwz_core::model::ErrorCode::InvalidRequest,
                    "operation was cancelled before admission",
                )));
            }
            if state.request_operations.get(request_id).map(String::as_str) != Some(operation_id) {
                return Err(Self::wrong_operation());
            }
            return Ok(operation_id.to_owned());
        }
        {
            let state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(operation_id) = state.request_operations.get(request_id) {
                if state.last_cancel.contains_key(operation_id) {
                    return Err(error::model(gwz_core::model::ModelError::new(
                        gwz_core::model::ErrorCode::InvalidRequest,
                        "operation was cancelled before admission",
                    )));
                }
                return Ok(operation_id.clone());
            }
        }
        self.reserve_inner(request_id.to_owned())
    }

    fn release_request_mapping(&self, request_id: &str, operation_id: &str) {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if state
            .request_operations
            .get(request_id)
            .is_some_and(|id| id == operation_id)
        {
            state.request_operations.remove(request_id);
        }
    }

    fn abandon_unstarted(&self, operation_id: Option<&str>) {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        let matching = operation_id
            .and_then(|id| state.admitting.get(id))
            .is_some_and(|admitting| !admitting.started);
        if matching {
            let admitting = state
                .admitting
                .remove(operation_id.expect("matching id"))
                .expect("matching admission");
            let report = CleanupReport::default();
            if admitting.cancel_requested {
                state
                    .last_cancel
                    .insert(admitting.operation_id.clone(), report.clone());
            }
            if let Some(signal) = admitting.signal {
                let _ = signal.send(Err(AdmissionFailure::Runtime(
                    "operation cancelled before admission".into(),
                )));
            }
            let _ = self
                .inner
                .operations
                .refuse(&admitting.operation_id, Self::cancelled_before_admission());
            admitting.done.finish(report);
            self.inner.changed.notify_all();
        }
    }

    fn runtime(&self) -> PyResult<Arc<TransportRuntime>> {
        self.runtime_with(|| {
            self.inner
                .executor
                .block_on(async { TransportRuntime::from_environment() })
        })
    }

    fn runtime_with(
        &self,
        build: impl Fn() -> gwz_core::model::ModelResult<TransportRuntime>,
    ) -> PyResult<Arc<TransportRuntime>> {
        loop {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.status != Status::Open || state.faulted {
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
            let built = catch_unwind(AssertUnwindSafe(&build));
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if built.is_err() {
                state.constructing = false;
                state.faulted = true;
                state.status = Status::Closed;
                let report = CleanupReport {
                    pending_local_work: 1,
                    peer_cleanup_confirmed: false,
                };
                state.close_report = Some(report.clone());
                let refusal = gwz_core::model::ModelError::new(
                    gwz_core::model::ErrorCode::InternalError,
                    "transport endpoint construction panicked",
                );
                for operation_id in state.request_operations.values() {
                    let _ = self.inner.operations.refuse(operation_id, refusal.clone());
                }
                for (_, mut admission) in std::mem::take(&mut state.admitting) {
                    if let Some(signal) = admission.signal.take() {
                        let _ = signal.send(Err(AdmissionFailure::Model(refusal.clone())));
                    }
                    admission.done.finish(report.clone());
                }
                self.inner.changed.notify_all();
                return Err(error::model(refusal));
            }
            let built = built.expect("checked construction result");
            if state.status != Status::Open || state.faulted {
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
        caller_cwd: Option<PathBuf>,
        defer_failure: bool,
        expected_operation_id: Option<&str>,
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
        let operation_id = self.operation_for_request(&meta.request_id, expected_operation_id)?;
        if let Err(err) = gwz_core::transport_host::validate_request_context(&meta, &operation_id) {
            let _ = self.inner.operations.refuse(&operation_id, err.clone());
            self.release_request_mapping(&meta.request_id, &operation_id);
            self.end_admission(
                &operation_id,
                CleanupReport::default(),
                Some(AdmissionFailure::Model(err.clone())),
                defer_failure,
            );
            return Err(error::model(err));
        }
        if meta
            .transport
            .as_ref()
            .and_then(|transport| transport.placement)
            == Some(gwz_core::TransportPlacement::Cli)
        {
            let err = gwz_core::model::ModelError::new(
                gwz_core::model::ErrorCode::UnsupportedOperation,
                "Python transport session has no CLI endpoint placement",
            );
            let _ = self.inner.operations.refuse(&operation_id, err.clone());
            self.release_request_mapping(&meta.request_id, &operation_id);
            self.end_admission(
                &operation_id,
                CleanupReport::default(),
                Some(AdmissionFailure::Model(err.clone())),
                defer_failure,
            );
            return Err(error::model(err));
        }
        {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if state.status != Status::Open || state.faulted {
                drop(state);
                self.end_admission(
                    &operation_id,
                    CleanupReport::default(),
                    Some(AdmissionFailure::Runtime("client is closed".into())),
                    defer_failure,
                );
                return Err(Self::closed());
            }
            if state.last_cancel.contains_key(&operation_id) {
                return Err(error::model(gwz_core::model::ModelError::new(
                    gwz_core::model::ErrorCode::InvalidRequest,
                    "operation was cancelled before admission",
                )));
            }
            if state.active.contains_key(&operation_id) {
                return Err(Self::busy());
            }
            if let Some(admitting) = state.admitting.get_mut(&operation_id) {
                if admitting.started {
                    return Err(Self::busy());
                }
                admitting.started = true;
                if admitting.cancel_requested {
                    drop(state);
                    self.end_admission(
                        &operation_id,
                        CleanupReport::default(),
                        Some(AdmissionFailure::Runtime(
                            "operation cancelled before admission".into(),
                        )),
                        defer_failure,
                    );
                    return Err(error::runtime("operation cancelled"));
                }
            } else {
                if state.admitting.len() + state.active.len() >= 8 {
                    state.request_operations.retain(|_, id| id != &operation_id);
                    return Err(self.refuse_full(&operation_id));
                }
                state.admitting.insert(
                    operation_id.clone(),
                    Admitting {
                        operation_id: operation_id.clone(),
                        done: Arc::new(Completion::default()),
                        cancel_requested: false,
                        started: true,
                        signal: None,
                        failure: None,
                        failure_report: None,
                    },
                );
            }
        }
        let runtime = match self.runtime() {
            Ok(runtime) => runtime,
            Err(err) => {
                self.release_request_mapping(&meta.request_id, &operation_id);
                self.end_admission(
                    &operation_id,
                    CleanupReport::default(),
                    Some(AdmissionFailure::Runtime(err.to_string())),
                    defer_failure,
                );
                return Err(err);
            }
        };
        let request_id = meta.request_id.clone();
        let schema_version = meta.schema_version.clone();
        let request = match self
            .inner
            .executor
            .block_on(runtime.request(meta, operation_id.clone()))
        {
            Ok(request) => request,
            Err(err) => {
                if matches!(
                    err.code,
                    gwz_core::model::ErrorCode::TransportCapacityConflict
                        | gwz_core::model::ErrorCode::InvalidRequest
                        | gwz_core::model::ErrorCode::UnsupportedOperation
                ) {
                    self.release_request_mapping(&request_id, &operation_id);
                }
                self.end_admission(
                    &operation_id,
                    CleanupReport::default(),
                    Some(AdmissionFailure::Model(err.clone())),
                    defer_failure,
                );
                return Err(error::model(err));
            }
        };
        let done;
        {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            let admitting = state
                .admitting
                .remove(&operation_id)
                .expect("reserved admission");
            if state.status != Status::Open || state.faulted || admitting.cancel_requested {
                state.admitting.insert(operation_id.clone(), admitting);
                drop(state);
                request.cancel();
                let report = self.inner.executor.block_on(request.finish());
                self.end_admission(
                    &operation_id,
                    report,
                    Some(AdmissionFailure::Runtime(
                        "operation cancelled before admission".into(),
                    )),
                    defer_failure,
                );
                return Err(Self::closed());
            }
            done = admitting.done;
            if let Some(signal) = admitting.signal {
                let _ = signal.send(Ok(()));
            }
            state.active.insert(
                operation_id.clone(),
                Active {
                    cancellation: request.cancellation_handle(),
                    done: done.clone(),
                    cancel_requested: false,
                },
            );
            self.inner.changed.notify_all();
        }
        self.inner.operations.defer_terminal(&operation_id);
        let dispatched = catch_unwind(AssertUnwindSafe(|| {
            operations::with_store(self.inner.operations.clone(), || {
                shims::with_operation_id(operation_id.clone(), || {
                    shims::with_scoped_backend(request.backend().clone(), || {
                        dispatch::call(
                            method,
                            request_message,
                            response_message,
                            request_bytes,
                            caller_cwd,
                        )
                    })
                })
            })
        }));
        let panicked = dispatched.is_err();
        let result = dispatched.unwrap_or_else(|_| Err(error::runtime("native operation failed")));
        if let Err(err) = &result {
            let recorder = operations::with_store(self.inner.operations.clone(), || {
                operations::begin(&operation_id)
            });
            if panicked {
                let _ = recorder.finish_panic_error(
                    request_id.clone(),
                    action_for_method(method),
                    err.to_string(),
                );
            } else {
                let _ = recorder.finish_error(
                    request_id.clone(),
                    schema_version,
                    action_for_method(method),
                    err.to_string(),
                );
            }
        }
        let report = self.finish_after_dispatch(
            &operation_id,
            &request_id,
            action_for_method(method),
            || self.inner.executor.block_on(request.finish()),
        )?;
        let publication = self.inner.operations.publish_terminal(&operation_id);
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(active) = state.active.remove(&operation_id) {
            if active.cancel_requested {
                state.last_cancel.insert(operation_id, report.clone());
            }
        }
        done.finish(report);
        self.inner.changed.notify_all();
        publication?;
        result
    }

    fn finish_after_dispatch(
        &self,
        operation_id: &str,
        request_id: &str,
        action: gwz_core::ActionKind,
        finish: impl FnOnce() -> CleanupReport,
    ) -> PyResult<CleanupReport> {
        match catch_unwind(AssertUnwindSafe(finish)) {
            Ok(report) => Ok(report),
            Err(_) => {
                let recorder = operations::with_store(self.inner.operations.clone(), || {
                    operations::begin(operation_id)
                });
                let _ = recorder.finish_panic_error(
                    request_id.to_owned(),
                    action,
                    "transport finish panicked".into(),
                );
                self.worker_panicked(operation_id);
                Err(error::runtime("transport finish panicked"))
            }
        }
    }

    fn end_admission(
        &self,
        operation_id: &str,
        report: CleanupReport,
        reason: Option<AdmissionFailure>,
        defer_failure: bool,
    ) {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if defer_failure {
            if let Some(admitting) = state.admitting.get_mut(operation_id) {
                admitting.failure = reason;
                admitting.failure_report = Some(report);
            }
            self.inner.changed.notify_all();
            return;
        }
        if let Some(reason) = reason.as_ref() {
            let refusal = match reason {
                AdmissionFailure::Model(err) => err.clone(),
                AdmissionFailure::Runtime(message) => gwz_core::model::ModelError::new(
                    gwz_core::model::ErrorCode::IoError,
                    message.clone(),
                ),
            };
            let _ = self.inner.operations.refuse(operation_id, refusal);
        }
        if let Some(admitting) = state.admitting.remove(operation_id) {
            if let Some(signal) = admitting.signal {
                let _ =
                    signal.send(Err(reason.unwrap_or_else(|| {
                        AdmissionFailure::Runtime("admission failed".into())
                    })));
            }
            if admitting.cancel_requested {
                state
                    .last_cancel
                    .insert(admitting.operation_id, report.clone());
            }
            admitting.done.finish(report);
        }
        self.inner.changed.notify_all();
    }

    pub(crate) fn complete_failed_admission(&self, operation_id: &str) {
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(mut admitting) = state.admitting.remove(operation_id) {
            let report = admitting.failure_report.take().unwrap_or_default();
            if admitting.cancel_requested {
                state
                    .last_cancel
                    .insert(operation_id.to_owned(), report.clone());
            }
            if let Some(signal) = admitting.signal.take() {
                let _ = signal
                    .send(Err(admitting.failure.take().unwrap_or_else(|| {
                        AdmissionFailure::Runtime("admission failed".into())
                    })));
            }
            admitting.done.finish(report);
            self.inner.changed.notify_all();
        }
    }

    /// The worker's outer unwind boundary must wake every waiter even if the
    /// unwind happened outside the narrower Git-dispatch catch in call_inner.
    /// Physical cleanup is conservatively unconfirmed after such an unwind.
    pub(crate) fn worker_panicked(&self, operation_id: &str) {
        let report = CleanupReport {
            pending_local_work: 1,
            peer_cleanup_confirmed: false,
        };
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        state.faulted = true;
        for peer in state.active.values_mut() {
            peer.cancellation.cancel();
            peer.cancel_requested = true;
        }
        for peer in state.admitting.values_mut() {
            peer.cancel_requested = true;
        }
        if let Some(mut admitting) = state.admitting.remove(operation_id) {
            if let Some(signal) = admitting.signal.take() {
                let _ = signal.send(Err(AdmissionFailure::Runtime(
                    "native operation panicked before acceptance".into(),
                )));
            }
            if admitting.cancel_requested {
                state
                    .last_cancel
                    .insert(operation_id.to_owned(), report.clone());
            }
            let _ = self.inner.operations.refuse(
                operation_id,
                gwz_core::model::ModelError::new(
                    gwz_core::model::ErrorCode::InternalError,
                    "native operation panicked before acceptance",
                ),
            );
            admitting.done.finish(report.clone());
        }
        let _ = self.inner.operations.publish_terminal(operation_id);
        if let Some(active) = state.active.remove(operation_id) {
            active.cancellation.cancel();
            if active.cancel_requested {
                state
                    .last_cancel
                    .insert(operation_id.to_owned(), report.clone());
            }
            active.done.finish(report);
        }
        self.inner.changed.notify_all();
    }

    pub(crate) fn spawned_call(
        &self,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
        caller_cwd: Option<PathBuf>,
        operation_id: &str,
    ) -> PyResult<Vec<u8>> {
        self.call_inner(
            method,
            request_message,
            response_message,
            request_bytes,
            caller_cwd,
            true,
            Some(operation_id),
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
            let unstarted: Vec<String> = state
                .admitting
                .iter()
                .filter_map(|(id, admission)| (!admission.started).then_some(id.clone()))
                .collect();
            for id in unstarted {
                let admission = state.admitting.remove(&id).expect("unstarted admission");
                let report = CleanupReport::default();
                state.last_cancel.insert(id, report.clone());
                if let Some(signal) = admission.signal {
                    let _ = signal.send(Err(AdmissionFailure::Runtime("client is closed".into())));
                }
                let _ = self
                    .inner
                    .operations
                    .refuse(&admission.operation_id, Self::cancelled_before_admission());
                admission.done.finish(report);
            }
            for admitting in state.admitting.values_mut() {
                admitting.cancel_requested = true;
            }
            for active in state.active.values_mut() {
                active.cancellation.cancel();
                active.cancel_requested = true;
            }
            if !state.constructing && state.admitting.is_empty() && state.active.is_empty() {
                break;
            }
            state = self
                .inner
                .changed
                .wait(state)
                .unwrap_or_else(|e| e.into_inner());
        }
        // A caller may have reserved a handle but never entered call/submit.
        // Close must settle that handle as well as worker-backed admissions.
        for operation_id in state.request_operations.values() {
            let _ = self
                .inner
                .operations
                .refuse(operation_id, Self::cancelled_before_admission());
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
        let report = if state.faulted {
            CleanupReport {
                pending_local_work: report.pending_local_work.max(1),
                peer_cleanup_confirmed: false,
            }
        } else {
            report
        };
        state.close_report = Some(report.clone());
        state.status = Status::Closed;
        self.inner.changed.notify_all();
        report
    }

    fn cancel_inner(&self, operation_id: &str) -> PyResult<CleanupReport> {
        self.validate_operation_id(operation_id)?;
        let (cancel, done) = {
            let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(admitting) = state.admitting.get_mut(operation_id) {
                if !admitting.started {
                    let admitting = state
                        .admitting
                        .remove(operation_id)
                        .expect("unstarted admission");
                    let report = CleanupReport::default();
                    state
                        .last_cancel
                        .insert(operation_id.to_owned(), report.clone());
                    if let Some(signal) = admitting.signal {
                        let _ = signal.send(Err(AdmissionFailure::Runtime(
                            "operation cancelled before admission".into(),
                        )));
                    }
                    let _ = self
                        .inner
                        .operations
                        .refuse(operation_id, Self::cancelled_before_admission());
                    admitting.done.finish(report.clone());
                    self.inner.changed.notify_all();
                    return Ok(report);
                }
                admitting.cancel_requested = true;
                let done = admitting.done.clone();
                drop(state);
                return Ok(done.wait());
            }
            if let Some(active) = state.active.get_mut(operation_id) {
                active.cancel_requested = true;
                (active.cancellation.clone(), active.done.clone())
            } else if let Some(report) = state.last_cancel.get(operation_id) {
                return Ok(report.clone());
            } else if self.inner.operations.contains(operation_id)
                && state
                    .request_operations
                    .values()
                    .any(|id| id == operation_id)
            {
                let report = CleanupReport::default();
                state
                    .last_cancel
                    .insert(operation_id.to_owned(), report.clone());
                let _ = self
                    .inner
                    .operations
                    .refuse(operation_id, Self::cancelled_before_admission());
                return Ok(report);
            } else {
                return Err(Self::wrong_operation());
            }
        };
        cancel.cancel();
        Ok(done.wait())
    }

    fn release_inner(&self, operation_id: &str) -> PyResult<()> {
        self.validate_operation_id(operation_id)?;
        let mut state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.active.contains_key(operation_id)
            || state
                .admitting
                .get(operation_id)
                .is_some_and(|admission| admission.started)
        {
            return Err(error::model(gwz_core::model::ModelError::new(
                gwz_core::model::ErrorCode::OpenOperation,
                "operation is still active",
            )));
        }
        if let Some(admission) = state.admitting.remove(operation_id) {
            admission.done.finish(CleanupReport::default());
            self.inner.changed.notify_all();
        }
        self.inner.operations.discard(operation_id)?;
        state.request_operations.retain(|_, id| id != operation_id);
        state.last_cancel.remove(operation_id);
        Ok(())
    }
}

cfg_if::cfg_if! {
    if #[cfg(test)] {
        mod constructor_panic_tests {
            use super::*;
            use pyo3::types::PyAnyMethods;

            #[test]
            fn construction_panic_settles_reserved_and_admitting_ids() {
                Python::initialize();
                let session = TransportSession::new().unwrap();
                let reserved = session.reserve_inner("reserved".into()).unwrap();
                let admitting = session.reserve_inner("admitting".into()).unwrap();
                let (signal, wait) = mpsc::channel();
                session.inner.state.lock().unwrap().admitting.insert(
                    admitting.clone(),
                    Admitting {
                        operation_id: admitting.clone(),
                        done: Arc::new(Completion::default()),
                        cancel_requested: false,
                        started: true,
                        signal: Some(signal),
                        failure: None,
                        failure_report: None,
                    },
                );
                assert!(session.runtime_with(|| panic!("injected construction panic")).is_err());
                assert!(matches!(wait.recv_timeout(std::time::Duration::from_secs(1)),
                    Ok(Err(AdmissionFailure::Model(_)))));
                for operation_id in [&reserved, &admitting] {
                    let error = session.inner.operations.result(operation_id).unwrap_err();
                    Python::attach(|py| {
                        let code: String = error.value(py).getattr("code").unwrap().extract().unwrap();
                        assert_eq!(code, "InternalError");
                    });
                    assert!(session.inner.operations.wait_events(operation_id, 0,
                        std::time::Duration::ZERO).unwrap().1);
                }
                let report = session.close_inner();
                assert!(!report.peer_cleanup_confirmed);
                let repeated = session.close_inner();
                assert_eq!(repeated.pending_local_work, report.pending_local_work);
                assert_eq!(repeated.peer_cleanup_confirmed, report.peer_cleanup_confirmed);
                assert!(session.reserve_inner("later".into()).is_err());
            }

            #[test]
            fn eight_slot_refusals_settle_both_native_call_forms() {
                Python::initialize();
                let session = TransportSession::new().unwrap();
                {
                    let mut state = session.inner.state.lock().unwrap();
                    for index in 0..8 {
                        let operation_id = format!("held-{index}");
                        state.admitting.insert(operation_id.clone(), Admitting {
                            operation_id,
                            done: Arc::new(Completion::default()),
                            cancel_requested: false,
                            started: true,
                            signal: None,
                            failure: None,
                            failure_report: None,
                        });
                    }
                }
                for index in 0..65 {
                    let request_id = format!("full-{index}");
                    let operation_id = session.reserve_inner(request_id.clone()).unwrap();
                    let meta = gwz_core::RequestMeta {
                        request_id,
                        schema_version: "gwz.protocol/v0".into(),
                        ..Default::default()
                    };
                    let request = gwz_core::FetchRequest { meta };
                    let bytes = gwz_core::encode(&request.to_cbor());
                    let error = if index % 2 == 0 {
                        session.call_inner(
                            "fetch", "FetchRequest", "FetchResponse", &bytes,
                            Some(std::env::current_dir().unwrap()), false, Some(&operation_id),
                        ).unwrap_err()
                    } else {
                        Python::attach(|py| session.submit(
                            py, "fetch", "FetchRequest", "FetchResponse", &bytes,
                            Some(&operation_id),
                        )).unwrap_err()
                    };
                    Python::attach(|py| {
                        let code: String = error.value(py).getattr("code").unwrap().extract().unwrap();
                        assert_eq!(code, "TransportSessionFull");
                    });
                    let retained = session.inner.operations.result(&operation_id).unwrap_err();
                    Python::attach(|py| {
                        let code: String = retained.value(py).getattr("code").unwrap().extract().unwrap();
                        assert_eq!(code, "TransportSessionFull");
                    });
                    assert!(session.inner.operations.wait_events(
                        &operation_id, 0, std::time::Duration::ZERO,
                    ).unwrap().1);
                    session.release_inner(&operation_id).unwrap();
                }
                session.inner.state.lock().unwrap().admitting.clear();
                session.close_inner();
            }

            #[test]
            fn finish_panic_cannot_publish_staged_success() {
                Python::initialize();
                let session = TransportSession::new().unwrap();
                let operation_id = session.reserve_inner("request".into()).unwrap();
                session.inner.operations.defer_terminal(&operation_id);
                let recorder = operations::with_store(session.inner.operations.clone(), || {
                    operations::begin(&operation_id)
                });
                let mut response = gwz_core::ResponseEnvelope::default();
                response.meta.operation_id = Some(operation_id.clone());
                response.meta.request_id = "request".into();
                response.meta.aggregate_status = gwz_core::AggregateStatus::Ok;
                recorder.finish(&response).unwrap();
                assert!(session.finish_after_dispatch(
                    &operation_id, "request", gwz_core::ActionKind::Fetch,
                    || panic!("injected finish panic"),
                ).is_err());
                let result = session.inner.operations.result(&operation_id).unwrap();
                assert_eq!(result.aggregate_status, gwz_core::AggregateStatus::Failed);
                assert!(session.reserve_inner("later".into()).is_err());
                let report = session.close_inner();
                assert!(!report.peer_cleanup_confirmed);
            }
        }
    }
}

#[pymethods]
impl TransportSession {
    #[new]
    fn py_new(py: Python<'_>) -> PyResult<Self> {
        py.detach(Self::new)
    }

    fn reserve_operation(&self, py: Python<'_>, request_id: &str) -> PyResult<String> {
        let session = self.clone();
        let request_id = request_id.to_owned();
        py.detach(move || session.reserve_inner(request_id))
    }

    fn issued_operation(&self, operation_id: &str) -> bool {
        let serial = self.issued_serial(operation_id);
        let state = self.inner.state.lock().unwrap_or_else(|e| e.into_inner());
        serial.is_some_and(|serial| serial <= state.next_serial)
    }

    #[pyo3(signature = (method, request_message, response_message, request_bytes, operation_id=None))]
    fn call(
        &self,
        py: Python<'_>,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
        operation_id: Option<&str>,
    ) -> PyResult<Vec<u8>> {
        let method = method.to_owned();
        let request_message = request_message.to_owned();
        let response_message = response_message.to_owned();
        let request_bytes = request_bytes.to_vec();
        let operation_id = operation_id.map(str::to_owned);
        let session = self.clone();
        py.detach(move || {
            session.call_inner(
                &method,
                &request_message,
                &response_message,
                &request_bytes,
                // Dispatch takes the caller's directory from the request itself.
                None,
                false,
                operation_id.as_deref(),
            )
        })
    }

    #[pyo3(signature = (method, request_message, response_message, request_bytes, issued_operation_id=None))]
    fn submit(
        &self,
        py: Python<'_>,
        method: &str,
        request_message: &str,
        response_message: &str,
        request_bytes: &[u8],
        issued_operation_id: Option<&str>,
    ) -> PyResult<Vec<u8>> {
        let method = method.to_owned();
        let request_message = request_message.to_owned();
        let response_message = response_message.to_owned();
        let request_bytes = request_bytes.to_vec();
        let issued_operation_id = issued_operation_id.map(str::to_owned);
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
            let operation_id = meta
                .as_ref()
                .map(|meta| {
                    session.operation_for_request(&meta.request_id, issued_operation_id.as_deref())
                })
                .transpose()?;
            let mut admission_wait = None;
            if !network {
                session.ensure_open()?;
            }
            if network {
                let operation_id = operation_id.as_ref().expect("network operation id");
                let mut state = session
                    .inner
                    .state
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                if state.status != Status::Open || state.faulted {
                    drop(state);
                    session.abandon_unstarted(Some(&operation_id));
                    return Err(Self::closed());
                }
                if state.last_cancel.contains_key(operation_id) {
                    return Err(error::model(gwz_core::model::ModelError::new(
                        gwz_core::model::ErrorCode::InvalidRequest,
                        "operation was cancelled before admission",
                    )));
                }
                if state.admitting.len() + state.active.len() >= 8
                    && !state.admitting.contains_key(operation_id)
                {
                    state.request_operations.retain(|_, id| id != operation_id);
                    return Err(session.refuse_full(operation_id));
                }
                if let Some(admitting) = state.admitting.get(operation_id) {
                    if admitting.started {
                        return Err(Self::busy());
                    }
                } else {
                    state.admitting.insert(
                        operation_id.clone(),
                        Admitting {
                            operation_id: operation_id.clone(),
                            done: Arc::new(Completion::default()),
                            cancel_requested: false,
                            started: false,
                            signal: None,
                            failure: None,
                            failure_report: None,
                        },
                    );
                }
                let (signal, wait) = mpsc::channel();
                state
                    .admitting
                    .get_mut(operation_id)
                    .expect("reserved admission")
                    .signal = Some(signal);
                admission_wait = Some(wait);
            }
            let result = if let Some(operation_id) = operation_id.as_ref() {
                operations::with_store(session.inner.operations.clone(), || {
                    with_current_session(session.clone(), || {
                        shims::with_operation_id(operation_id.clone(), || {
                            dispatch::submit(
                                &method,
                                &request_message,
                                &response_message,
                                &request_bytes,
                            )
                        })
                    })
                })
            } else {
                dispatch::submit(
                    &method,
                    &request_message,
                    &response_message,
                    &request_bytes,
                )
            };
            if network && result.is_err() {
                if let (Some(meta), Some(operation_id)) = (meta.as_ref(), operation_id.as_ref()) {
                    session.release_request_mapping(&meta.request_id, operation_id);
                }
                let mut state = session
                    .inner
                    .state
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                if let Some(admitting) = state
                    .admitting
                    .remove(operation_id.as_ref().expect("network operation id"))
                {
                    let report = CleanupReport::default();
                    if admitting.cancel_requested {
                        state
                            .last_cancel
                            .insert(admitting.operation_id, report.clone());
                    }
                    admitting.done.finish(report);
                }
                session.inner.changed.notify_all();
            }
            if result.is_ok() {
                if let Some(wait) = admission_wait {
                    match wait.recv() {
                        Ok(Ok(())) => {}
                        Ok(Err(AdmissionFailure::Model(err))) => return Err(error::model(err)),
                        Ok(Err(AdmissionFailure::Runtime(message))) => {
                            return Err(error::runtime(message));
                        }
                        Err(_) => {
                            return Err(error::runtime(
                                "admission worker ended before registration",
                            ));
                        }
                    }
                }
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

    fn release_operation(&self, py: Python<'_>, operation_id: &str) -> PyResult<()> {
        let operation_id = operation_id.to_owned();
        let session = self.clone();
        py.detach(move || session.release_inner(&operation_id))
    }

    fn subscribe_events(&self, operation_id: &str) -> PyResult<Vec<Vec<u8>>> {
        self.validate_operation_id(operation_id)?;
        self.inner
            .operations
            .events(operation_id)?
            .into_iter()
            .map(|event| codec::encode_message("encode OperationEvent", || event.to_cbor()))
            .collect()
    }

    fn wait_events(
        &self,
        py: Python<'_>,
        operation_id: &str,
        after_sequence: i64,
        timeout_ms: u64,
    ) -> PyResult<(Vec<Vec<u8>>, bool)> {
        let session = self.clone();
        let operation_id = operation_id.to_owned();
        py.detach(move || {
            session.validate_operation_id(&operation_id)?;
            let (events, complete) = session.inner.operations.wait_events(
                &operation_id,
                after_sequence,
                std::time::Duration::from_millis(timeout_ms),
            )?;
            let encoded = events
                .into_iter()
                .map(|event| codec::encode_message("encode OperationEvent", || event.to_cbor()))
                .collect::<PyResult<Vec<_>>>()?;
            Ok((encoded, complete))
        })
    }

    fn operation_result(&self, py: Python<'_>, operation_id: &str) -> PyResult<Vec<u8>> {
        let session = self.clone();
        let operation_id = operation_id.to_owned();
        py.detach(move || {
            session.validate_operation_id(&operation_id)?;
            let result = session.inner.operations.result(&operation_id)?;
            codec::encode_message("encode OperationResult", || result.to_cbor())
        })
    }

    fn try_operation_result(&self, operation_id: &str) -> PyResult<Option<Vec<u8>>> {
        self.validate_operation_id(operation_id)?;
        self.inner
            .operations
            .try_result(operation_id)?
            .map(|result| codec::encode_message("encode OperationResult", || result.to_cbor()))
            .transpose()
    }

    fn merge_operation_response(&self, py: Python<'_>, operation_id: &str) -> PyResult<Vec<u8>> {
        let session = self.clone();
        let operation_id = operation_id.to_owned();
        py.detach(move || {
            session.validate_operation_id(&operation_id)?;
            let response = session.inner.operations.merge_response(&operation_id)?;
            codec::encode_message("encode MergeResponse", || response.to_cbor())
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

fn action_for_method(method: &str) -> gwz_core::ActionKind {
    match method {
        "init_from_sources" => gwz_core::ActionKind::InitFromSources,
        "materialize" => gwz_core::ActionKind::Materialize,
        "clone_workspace" => gwz_core::ActionKind::CloneWorkspace,
        "clone_repo_member" => gwz_core::ActionKind::CloneRepoMember,
        "attach_repo_member" => gwz_core::ActionKind::AttachRepoMember,
        "pull_head" => gwz_core::ActionKind::PullHead,
        "pull_snapshot" => gwz_core::ActionKind::PullSnapshot,
        "push" => gwz_core::ActionKind::Push,
        "fetch" => gwz_core::ActionKind::Fetch,
        "tag" => gwz_core::ActionKind::Tag,
        _ => gwz_core::ActionKind::Fetch,
    }
}
