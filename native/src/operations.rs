use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pyo3::PyResult;

use crate::error;

static STORE: OnceLock<OperationStore> = OnceLock::new();

thread_local! {
    static SCOPED_STORE: RefCell<Option<Arc<OperationStore>>> = const { RefCell::new(None) };
}

pub(crate) fn with_store<T>(selected: Arc<OperationStore>, action: impl FnOnce() -> T) -> T {
    struct Restore(Option<Arc<OperationStore>>);
    impl Drop for Restore {
        fn drop(&mut self) {
            SCOPED_STORE.with(|slot| *slot.borrow_mut() = self.0.take());
        }
    }
    let previous = SCOPED_STORE.with(|slot| slot.borrow_mut().replace(selected));
    let restore = Restore(previous);
    let result = action();
    drop(restore);
    result
}

fn selected<T>(action: impl FnOnce(&OperationStore) -> T) -> T {
    SCOPED_STORE.with(|slot| {
        let current = slot.borrow();
        if let Some(current) = current.as_ref() {
            action(current)
        } else {
            action(store())
        }
    })
}

pub(crate) fn begin(operation_id: &str) -> OperationRecorder {
    selected(|store| store.begin(operation_id))
}

pub(crate) fn begin_exclusive(operation_id: &str) -> PyResult<OperationRecorder> {
    selected(|store| store.begin_exclusive(operation_id))
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

pub(crate) fn merge_response(operation_id: &str) -> PyResult<gwz_core::MergeResponse> {
    store().merge_response(operation_id)
}

fn store() -> &'static OperationStore {
    STORE.get_or_init(OperationStore::default)
}

#[derive(Default)]
pub(crate) struct OperationStore {
    records: Mutex<HashMap<String, Arc<OperationRecord>>>,
}

impl OperationStore {
    pub(crate) fn issue(&self, operation_id: &str) -> PyResult<()> {
        let mut records = self.records.lock().expect("operation store poisoned");
        if records.len() >= 64 {
            return Err(error::model(gwz_core::model::ModelError::new(
                gwz_core::model::ErrorCode::TransportSessionFull,
                "operation ledger is full; inspect recent operations and release completed records",
            )));
        }
        if records.contains_key(operation_id) {
            return Err(error::runtime("operation identity already issued"));
        }
        records.insert(operation_id.to_owned(), Arc::new(OperationRecord::new()));
        Ok(())
    }

    pub(crate) fn discard(&self, operation_id: &str) -> PyResult<()> {
        self.records.lock().expect("operation store poisoned")
            .remove(operation_id)
            .map(|_| ())
            .ok_or_else(|| error::runtime("operation is not owned by this session"))
    }

    pub(crate) fn contains(&self, operation_id: &str) -> bool {
        self.records.lock().expect("operation store poisoned")
            .contains_key(operation_id)
    }

    pub(crate) fn defer_terminal(&self, operation_id: &str) {
        self.begin(operation_id).record.defer_terminal();
    }

    pub(crate) fn publish_terminal(&self, operation_id: &str) -> PyResult<()> {
        self.record(operation_id)?.publish_terminal();
        Ok(())
    }

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

    fn begin_exclusive(&self, operation_id: &str) -> PyResult<OperationRecorder> {
        let mut records = self.records.lock().expect("operation store poisoned");
        if records.contains_key(operation_id) {
            return Err(error::runtime(format!(
                "operation {operation_id} already exists"
            )));
        }
        let record = Arc::new(OperationRecord::new());
        records.insert(operation_id.to_owned(), Arc::clone(&record));
        Ok(OperationRecorder {
            operation_id: operation_id.to_owned(),
            record,
        })
    }

    fn record(&self, operation_id: &str) -> PyResult<Arc<OperationRecord>> {
        self.records
            .lock()
            .expect("operation store poisoned")
            .get(operation_id)
            .cloned()
            .ok_or_else(|| error::runtime(format!("operation {operation_id} not found")))
    }

    pub(crate) fn events(&self, operation_id: &str) -> PyResult<Vec<gwz_core::OperationEvent>> {
        Ok(self.record(operation_id)?.events())
    }

    pub(crate) fn wait_events(
        &self,
        operation_id: &str,
        after_sequence: i64,
        timeout: Duration,
    ) -> PyResult<(Vec<gwz_core::OperationEvent>, bool)> {
        Ok(self
            .record(operation_id)?
            .wait_events(after_sequence, timeout))
    }

    pub(crate) fn result(&self, operation_id: &str) -> PyResult<gwz_core::OperationResult> {
        self.record(operation_id)?.result()
    }

    pub(crate) fn try_result(&self, operation_id: &str) -> PyResult<Option<gwz_core::OperationResult>> {
        Ok(self.record(operation_id)?.try_result())
    }

    pub(crate) fn merge_response(&self, operation_id: &str) -> PyResult<gwz_core::MergeResponse> {
        self.record(operation_id)?.merge_response(operation_id)
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
    merge_response: Option<gwz_core::MergeResponse>,
    defer_terminal: bool,
    pending_terminal: Option<(gwz_core::OperationResult, Option<gwz_core::MergeResponse>)>,
}

impl OperationRecord {
    fn defer_terminal(&self) {
        self.state.lock().expect("operation state poisoned").defer_terminal = true;
    }

    fn publish_terminal(&self) {
        let mut state = self.state.lock().expect("operation state poisoned");
        state.defer_terminal = false;
        if let Some((result, merge_response)) = state.pending_terminal.take() {
            state.merge_response = merge_response;
            state.result = Some(result);
            drop(state);
            self.changed.notify_all();
        }
    }

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
            transport: envelope.meta.transport.clone(),
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
        self.complete(result, None)
    }

    fn finish_error(
        &self,
        operation_id: String,
        request_id: String,
        _schema_version: String,
        action: gwz_core::ActionKind,
        message: String,
    ) -> PyResult<()> {
        let error = gwz_core::GwzError {
            code: gwz_core::GwzErrorCode::InternalError,
            message,
            member_id: None,
            member_path: None,
            detail: None,
            target_kind: None,
            record_context: None,
        };
        self.complete(
            gwz_core::OperationResult {
                transport: None,
                operation_id,
                request_id,
                action,
                aggregate_status: gwz_core::AggregateStatus::Failed,
                started_at_ms: self.started_at_ms,
                finished_at_ms: now_ms(),
                members: Vec::new(),
                errors: vec![error],
                attribution: None,
            },
            None,
        )
    }

    fn finish_model_error(
        &self,
        operation_id: String,
        meta: &gwz_core::RequestMeta,
        action: gwz_core::ActionKind,
        error: &gwz_core::model::ModelError,
    ) -> PyResult<()> {
        self.complete(
            gwz_core::OperationResult {
                transport: error
                    .response_meta
                    .as_ref()
                    .and_then(|meta| meta.transport.clone()),
                operation_id,
                request_id: meta.request_id.clone(),
                action,
                aggregate_status: gwz_core::AggregateStatus::Failed,
                started_at_ms: self.started_at_ms,
                finished_at_ms: now_ms(),
                members: Vec::new(),
                errors: vec![error.into()],
                attribution: meta.attribution.clone(),
            },
            None,
        )
    }

    fn finish_merge(&self, response: &gwz_core::MergeResponse) -> PyResult<()> {
        let envelope = &response.response;
        let operation_id = envelope
            .meta
            .operation_id
            .clone()
            .ok_or_else(|| error::runtime("response is missing operation_id"))?;
        let result = gwz_core::OperationResult {
            transport: envelope.meta.transport.clone(),
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
        self.complete(result, Some(response.clone()))
    }

    fn complete(
        &self,
        result: gwz_core::OperationResult,
        merge_response: Option<gwz_core::MergeResponse>,
    ) -> PyResult<()> {
        let mut state = self.state.lock().expect("operation state poisoned");
        if state.result.is_some() || state.pending_terminal.is_some() {
            return Err(error::runtime(format!(
                "operation {} is already complete",
                result.operation_id
            )));
        }

        // Publish the typed response and terminal result under the same lock.
        // Readers can therefore never observe completion without also seeing
        // the successful merge response.
        if state.defer_terminal {
            state.pending_terminal = Some((result, merge_response));
            return Ok(());
        }
        state.merge_response = merge_response;
        state.result = Some(result);
        drop(state);
        self.changed.notify_all();
        Ok(())
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

    fn merge_response(&self, operation_id: &str) -> PyResult<gwz_core::MergeResponse> {
        let mut state = self.state.lock().expect("operation state poisoned");
        loop {
            if let Some(value) = &state.merge_response {
                return Ok(value.clone());
            }
            if state.result.is_some() {
                return Err(error::runtime(format!(
                    "operation {operation_id} completed without a successful merge response"
                )));
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
    ) -> PyResult<()> {
        self.record.finish_error(
            self.operation_id.clone(),
            request_id,
            schema_version,
            action,
            message,
        )
    }

    pub(crate) fn finish_model_error(
        &self,
        meta: &gwz_core::RequestMeta,
        action: gwz_core::ActionKind,
        error: &gwz_core::model::ModelError,
    ) -> PyResult<()> {
        self.record
            .finish_model_error(self.operation_id.clone(), meta, action, error)
    }

    pub(crate) fn finish_merge(&self, response: &gwz_core::MergeResponse) -> PyResult<()> {
        if response.response.meta.operation_id.as_deref() != Some(self.operation_id.as_str()) {
            return Err(error::runtime(format!(
                "merge response operation_id does not match {}",
                self.operation_id
            )));
        }
        self.record.finish_merge(response)
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

#[cfg(test)]
mod tests {
    use std::sync::{Arc, mpsc};
    use std::thread;

    use super::*;

    #[test]
    fn session_stores_isolate_identical_operation_ids() {
        let a = OperationStore::default();
        let b = OperationStore::default();
        let a_recorder = a.begin_exclusive("same-id").unwrap();
        let b_recorder = b.begin_exclusive("same-id").unwrap();
        a_recorder
            .finish_error(
                "request-a".into(),
                "gwz.protocol/v0".into(),
                gwz_core::ActionKind::Push,
                "a failed".into(),
            )
            .unwrap();
        b_recorder
            .finish_error(
                "request-b".into(),
                "gwz.protocol/v0".into(),
                gwz_core::ActionKind::Fetch,
                "b failed".into(),
            )
            .unwrap();
        assert_eq!(a.result("same-id").unwrap().request_id, "request-a");
        assert_eq!(b.result("same-id").unwrap().request_id, "request-b");
    }

    #[test]
    fn scoped_recorder_does_not_publish_to_process_legacy_store() {
        let scoped = Arc::new(OperationStore::default());
        with_store(scoped.clone(), || {
            let recorder = begin("scoped-id");
            recorder
                .finish_error(
                    "request".into(),
                    "gwz.protocol/v0".into(),
                    gwz_core::ActionKind::Push,
                    "failed".into(),
                )
                .unwrap();
        });
        assert!(scoped.result("scoped-id").is_ok());
        assert!(result("scoped-id").is_err());
    }

    #[test]
    fn native_terminal_is_not_visible_until_transport_finishes() {
        let store = OperationStore::default();
        let recorder = store.begin_exclusive("transport-id").unwrap();
        store.defer_terminal("transport-id");
        recorder.finish_error(
            "request".into(), "gwz.protocol/v0".into(),
            gwz_core::ActionKind::Fetch, "failed".into(),
        ).unwrap();
        assert!(store.try_result("transport-id").unwrap().is_none());
        store.publish_terminal("transport-id").unwrap();
        assert!(store.try_result("transport-id").unwrap().is_some());
    }

    #[test]
    fn issued_records_are_bounded_independently_of_active_workers() {
        let store = OperationStore::default();
        for index in 0..64 {
            store.issue(&format!("issued-{index}")).unwrap();
        }
        let error = store.issue("issued-64").unwrap_err();
        assert!(error.to_string().contains("ledger is full"));
        store.discard("issued-0").unwrap();
        store.issue("issued-64").unwrap();
    }

    fn request_meta(request_id: &str) -> gwz_core::RequestMeta {
        gwz_core::RequestMeta {
            request_id: request_id.to_owned(),
            schema_version: "gwz.protocol/v0".to_owned(),
            ..gwz_core::RequestMeta::default()
        }
    }

    fn merge_response(request_id: &str) -> gwz_core::MergeResponse {
        gwz_core::MergeResponse {
            response: gwz_core::ResponseEnvelope {
                meta: gwz_core::ResponseMeta {
                    transport: None,
                    request_id: request_id.to_owned(),
                    schema_version: "gwz.protocol/v0".to_owned(),
                    action: gwz_core::ActionKind::Merge,
                    aggregate_status: gwz_core::AggregateStatus::Ok,
                    operation_id: Some("op_test".to_owned()),
                    message: None,
                    attribution: None,
                    ..gwz_core::ResponseMeta::default()
                },
                members: Vec::new(),
                errors: Vec::new(),
            },
            state: gwz_core::MergeOperationState::Idle,
            ..gwz_core::MergeResponse::default()
        }
    }

    #[test]
    fn duplicate_completion_preserves_the_first_result_and_response() {
        let record = OperationRecord::new();
        let first = merge_response("req_first");
        let second = merge_response("req_second");

        record.finish_merge(&first).unwrap();
        assert!(record.finish_merge(&second).is_err());

        assert_eq!(record.result().unwrap().request_id, "req_first");
        assert_eq!(record.merge_response("op_test").unwrap(), first);
    }

    #[test]
    fn model_failure_retains_structured_member_error_and_completes() {
        let record = OperationRecord::new();
        let model_error = gwz_core::model::ModelError::new(
            gwz_core::model::ErrorCode::GitCommandFailed,
            "source ref was not found",
        )
        .with_member("mem_app", "repos/app");

        record
            .finish_model_error(
                "op_test".to_owned(),
                &request_meta("req_failed"),
                gwz_core::ActionKind::Merge,
                &model_error,
            )
            .unwrap();

        let result = record.result().unwrap();
        assert_eq!(result.aggregate_status, gwz_core::AggregateStatus::Failed);
        assert_eq!(result.errors.len(), 1);
        assert_eq!(
            result.errors[0].code,
            gwz_core::GwzErrorCode::GitCommandFailed
        );
        assert_eq!(result.errors[0].member_id.as_deref(), Some("mem_app"));
        assert_eq!(result.errors[0].member_path.as_deref(), Some("repos/app"));
        assert_eq!(
            result.errors[0].target_kind,
            Some(gwz_core::TargetKind::Member)
        );
        assert!(record.merge_response("op_test").is_err());
    }

    #[test]
    fn completion_wakes_all_readers_with_response_already_visible() {
        let record = Arc::new(OperationRecord::new());
        let (sender, receiver) = mpsc::channel();

        for _ in 0..2 {
            let record = Arc::clone(&record);
            let sender = sender.clone();
            thread::spawn(move || {
                sender.send(record.result().unwrap().request_id).unwrap();
            });
        }
        for _ in 0..2 {
            let record = Arc::clone(&record);
            let sender = sender.clone();
            thread::spawn(move || {
                let response = record.merge_response("op_test").unwrap();
                sender.send(response.response.meta.request_id).unwrap();
            });
        }
        {
            let record = Arc::clone(&record);
            let sender = sender.clone();
            thread::spawn(move || {
                let (_, complete) = record.wait_events(0, Duration::from_secs(5));
                assert!(complete);
                let response = record.merge_response("op_test").unwrap();
                sender.send(response.response.meta.request_id).unwrap();
            });
        }
        drop(sender);

        record.finish_merge(&merge_response("req_success")).unwrap();

        for _ in 0..5 {
            assert_eq!(
                receiver.recv_timeout(Duration::from_secs(2)).unwrap(),
                "req_success"
            );
        }
    }

    #[test]
    fn failure_finish_event_precedes_completion_and_wakes_all_waiters() {
        let record = Arc::new(OperationRecord::new());
        let (sender, receiver) = mpsc::channel();
        record.push(gwz_core::OperationEvent {
            operation_id: "op_test".to_owned(),
            request_id: "req_failed".to_owned(),
            sequence: 0,
            kind: gwz_core::EventKind::OperationStarted,
            ..gwz_core::OperationEvent::default()
        });

        for _ in 0..2 {
            let record = Arc::clone(&record);
            let sender = sender.clone();
            thread::spawn(move || {
                let result = record.result().unwrap();
                sender
                    .send(format!("result:{:?}", result.errors[0].code))
                    .unwrap();
            });
        }
        for _ in 0..2 {
            let record = Arc::clone(&record);
            let sender = sender.clone();
            thread::spawn(move || {
                let mut kinds = Vec::new();
                let mut next_sequence = 0;
                loop {
                    let (events, complete) =
                        record.wait_events(next_sequence, Duration::from_secs(5));
                    if let Some(last) = events.last() {
                        next_sequence = last.sequence + 1;
                    }
                    kinds.extend(events.into_iter().map(|event| event.kind));
                    if complete {
                        sender.send(format!("events:{kinds:?}")).unwrap();
                        break;
                    }
                }
            });
        }
        drop(sender);

        record.push(gwz_core::OperationEvent {
            operation_id: "op_test".to_owned(),
            request_id: "req_failed".to_owned(),
            sequence: 1,
            kind: gwz_core::EventKind::OperationFinished,
            ..gwz_core::OperationEvent::default()
        });
        assert!(
            record.try_result().is_none(),
            "the finish event must be stored before completion is published"
        );
        record
            .finish_model_error(
                "op_test".to_owned(),
                &request_meta("req_failed"),
                gwz_core::ActionKind::Merge,
                &gwz_core::model::ModelError::new(
                    gwz_core::model::ErrorCode::InvalidRequest,
                    "invalid attribution",
                ),
            )
            .unwrap();

        let messages = (0..4)
            .map(|_| receiver.recv_timeout(Duration::from_secs(2)).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(
            messages
                .iter()
                .filter(|message| *message == "result:InvalidRequest")
                .count(),
            2
        );
        assert_eq!(
            messages
                .iter()
                .filter(|message| { *message == "events:[OperationStarted, OperationFinished]" })
                .count(),
            2
        );
    }
}
