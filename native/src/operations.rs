use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use pyo3::PyResult;

use crate::error;

static STORE: OnceLock<OperationStore> = OnceLock::new();

pub(crate) fn begin(operation_id: &str) -> OperationRecorder {
    store().begin(operation_id)
}

pub(crate) fn events(operation_id: &str) -> PyResult<Vec<gwz_core::OperationEvent>> {
    store().events(operation_id)
}

pub(crate) fn result(operation_id: &str) -> PyResult<gwz_core::OperationResult> {
    store().result(operation_id)
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
        let record = Arc::new(OperationRecord::new());
        self.records
            .lock()
            .expect("operation store poisoned")
            .insert(operation_id.to_owned(), Arc::clone(&record));
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

    fn result(&self, operation_id: &str) -> PyResult<gwz_core::OperationResult> {
        self.record(operation_id)?.result()
    }
}

struct OperationRecord {
    events: Mutex<Vec<gwz_core::OperationEvent>>,
    result: Mutex<Option<gwz_core::OperationResult>>,
    started_at_ms: i64,
}

impl OperationRecord {
    fn new() -> Self {
        Self {
            events: Mutex::new(Vec::new()),
            result: Mutex::new(None),
            started_at_ms: now_ms(),
        }
    }

    fn push(&self, event: gwz_core::OperationEvent) {
        self.events
            .lock()
            .expect("operation events poisoned")
            .push(event);
    }

    fn events(&self) -> Vec<gwz_core::OperationEvent> {
        self.events
            .lock()
            .expect("operation events poisoned")
            .clone()
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
        *self.result.lock().expect("operation result poisoned") = Some(result);
        Ok(())
    }

    fn result(&self) -> PyResult<gwz_core::OperationResult> {
        self.result
            .lock()
            .expect("operation result poisoned")
            .clone()
            .ok_or_else(|| error::runtime("operation result is not available yet"))
    }
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
}

impl gwz_core::operation::EventSink for OperationRecorder {
    fn deliver(&self, mut event: gwz_core::OperationEvent) {
        if event.operation_id.is_empty() {
            event.operation_id = self.operation_id.clone();
        }
        self.record.push(event);
    }
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}
