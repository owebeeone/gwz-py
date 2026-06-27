use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pyo3::PyResult;

use crate::error;

static STORE: OnceLock<OperationStore> = OnceLock::new();

pub(crate) fn begin(operation_id: &str) -> OperationRecorder {
    store().begin(operation_id)
}

pub(crate) fn events(operation_id: &str) -> PyResult<Vec<gwz_core::OperationEvent>> {
    store().events(operation_id)
}

pub(crate) fn wait_events(
    operation_id: &str,
    after_sequence: i64,
    timeout: Duration,
) -> PyResult<(Vec<gwz_core::OperationEvent>, bool)> {
    store().wait_events(operation_id, after_sequence, timeout)
}

pub(crate) fn result(operation_id: &str) -> PyResult<gwz_core::OperationResult> {
    store().result(operation_id)
}

pub(crate) fn try_result(operation_id: &str) -> PyResult<Option<gwz_core::OperationResult>> {
    store().try_result(operation_id)
}

fn store() -> &'static OperationStore {
    STORE.get_or_init(OperationStore::default)
}

#[derive(Default)]
struct OperationStore {
    records: Mutex<HashMap<String, Arc<OperationRecord>>>,
}

impl OperationStore {
    fn begin(&self, operation_id: &str) -> OperationRecorder {
        let mut records = self.records.lock().expect("operation store poisoned");
        let record = records
            .entry(operation_id.to_owned())
            .or_insert_with(|| Arc::new(OperationRecord::new()))
            .clone();
        OperationRecorder {
            operation_id: operation_id.to_owned(),
            record,
        }
    }

    fn record(&self, operation_id: &str) -> PyResult<Arc<OperationRecord>> {
        self.records
            .lock()
            .expect("operation store poisoned")
            .get(operation_id)
            .cloned()
            .ok_or_else(|| error::runtime(format!("operation {operation_id} not found")))
    }

    fn events(&self, operation_id: &str) -> PyResult<Vec<gwz_core::OperationEvent>> {
        Ok(self.record(operation_id)?.events())
    }

    fn wait_events(
        &self,
        operation_id: &str,
        after_sequence: i64,
        timeout: Duration,
    ) -> PyResult<(Vec<gwz_core::OperationEvent>, bool)> {
        Ok(self
            .record(operation_id)?
            .wait_events(after_sequence, timeout))
    }

    fn result(&self, operation_id: &str) -> PyResult<gwz_core::OperationResult> {
        self.record(operation_id)?.result()
    }

    fn try_result(&self, operation_id: &str) -> PyResult<Option<gwz_core::OperationResult>> {
        Ok(self.record(operation_id)?.try_result())
    }
}

struct OperationRecord {
    state: Mutex<OperationState>,
    changed: Condvar,
    started_at_ms: i64,
}

#[derive(Default)]
struct OperationState {
    events: Vec<gwz_core::OperationEvent>,
    result: Option<gwz_core::OperationResult>,
}

impl OperationRecord {
    fn new() -> Self {
        Self {
            state: Mutex::new(OperationState::default()),
            changed: Condvar::new(),
            started_at_ms: now_ms(),
        }
    }

    fn push(&self, event: gwz_core::OperationEvent) {
        self.state
            .lock()
            .expect("operation state poisoned")
            .events
            .push(event);
        self.changed.notify_all();
    }

    fn events(&self) -> Vec<gwz_core::OperationEvent> {
        self.state
            .lock()
            .expect("operation state poisoned")
            .events
            .clone()
    }

    fn wait_events(
        &self,
        after_sequence: i64,
        timeout: Duration,
    ) -> (Vec<gwz_core::OperationEvent>, bool) {
        let mut state = self.state.lock().expect("operation state poisoned");
        loop {
            let events = unseen_events(&state.events, after_sequence);
            let complete = state.result.is_some();
            if !events.is_empty() || complete {
                return (events, complete);
            }

            let wait = self
                .changed
                .wait_timeout(state, timeout)
                .expect("operation state poisoned");
            state = wait.0;
            if wait.1.timed_out() {
                return (
                    unseen_events(&state.events, after_sequence),
                    state.result.is_some(),
                );
            }
        }
    }

    fn finish(&self, envelope: &gwz_core::ResponseEnvelope) -> PyResult<()> {
        let operation_id = envelope
            .meta
            .operation_id
            .clone()
            .ok_or_else(|| error::runtime("response is missing operation_id"))?;
        let result = gwz_core::OperationResult {
            operation_id,
            request_id: envelope.meta.request_id.clone(),
            action: envelope.meta.action,
            aggregate_status: envelope.meta.aggregate_status,
            started_at_ms: self.started_at_ms,
            finished_at_ms: now_ms(),
            members: envelope.members.clone(),
            errors: envelope.errors.clone(),
            attribution: envelope.meta.attribution.clone(),
        };
        self.set_result(result);
        Ok(())
    }

    fn finish_error(
        &self,
        operation_id: String,
        request_id: String,
        _schema_version: String,
        action: gwz_core::ActionKind,
        message: String,
    ) {
        let error = gwz_core::GwzError {
            code: gwz_core::GwzErrorCode::InternalError,
            message,
            member_id: None,
            member_path: None,
            detail: None,
            target_kind: None,
        };
        self.set_result(gwz_core::OperationResult {
            operation_id,
            request_id,
            action,
            aggregate_status: gwz_core::AggregateStatus::Failed,
            started_at_ms: self.started_at_ms,
            finished_at_ms: now_ms(),
            members: Vec::new(),
            errors: vec![error],
            attribution: None,
        });
    }

    fn set_result(&self, result: gwz_core::OperationResult) {
        self.state.lock().expect("operation state poisoned").result = Some(result);
        self.changed.notify_all();
    }

    fn try_result(&self) -> Option<gwz_core::OperationResult> {
        self.state
            .lock()
            .expect("operation state poisoned")
            .result
            .clone()
    }

    fn result(&self) -> PyResult<gwz_core::OperationResult> {
        let mut state = self.state.lock().expect("operation state poisoned");
        loop {
            if let Some(value) = &state.result {
                return Ok(value.clone());
            }
            state = self.changed.wait(state).expect("operation state poisoned");
        }
    }
}

fn unseen_events(
    events: &[gwz_core::OperationEvent],
    after_sequence: i64,
) -> Vec<gwz_core::OperationEvent> {
    events
        .iter()
        .filter(|event| event.sequence >= after_sequence)
        .cloned()
        .collect()
}

#[derive(Clone)]
pub(crate) struct OperationRecorder {
    operation_id: String,
    record: Arc<OperationRecord>,
}

impl OperationRecorder {
    pub(crate) fn finish(&self, envelope: &gwz_core::ResponseEnvelope) -> PyResult<()> {
        self.record.finish(envelope)
    }

    pub(crate) fn finish_error(
        &self,
        request_id: String,
        schema_version: String,
        action: gwz_core::ActionKind,
        message: String,
    ) {
        self.record.finish_error(
            self.operation_id.clone(),
            request_id,
            schema_version,
            action,
            message,
        );
    }
}

impl gwz_core::operation::EventSink for OperationRecorder {
    fn deliver(&self, mut event: gwz_core::OperationEvent) {
        if event.operation_id.is_empty() {
            event.operation_id = self.operation_id.clone();
        }
        let delay = test_event_delay(&event);
        self.record.push(event);
        if let Some(delay) = delay {
            thread::sleep(delay);
        }
    }
}

fn test_event_delay(event: &gwz_core::OperationEvent) -> Option<Duration> {
    if event.kind != gwz_core::EventKind::OperationStarted {
        return None;
    }
    let ms = std::env::var("GWZ_PY_TEST_EVENT_DELAY_MS")
        .ok()?
        .parse()
        .ok()?;
    Some(Duration::from_millis(ms))
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}
