//! Process-global store of diff output logs — the PyO3 mirror of the
//! `operations.rs` operation store, but for `diff.output` byte streams.
//!
//! `gwz_core::diff::handle_diff` mints an output `log_id` into a
//! [`DiffLogRegistry`](gwz_core::diff::DiffLogRegistry) and, in the in-process v0
//! model, runs the producer to completion synchronously before returning. For the
//! `diff.output` reader to read those bytes on a *later* PyO3 call, the registry
//! must outlive the `diff` call. This module owns one process-global registry
//! (`OnceLock`), matching how `operations.rs` owns one process-global operation
//! store: the `diff` dispatch borrows it to run `handle_diff`; the
//! `diff_log_read` / `diff_log_end_stream` pyfunctions borrow it to read and to
//! end streams.
//!
//! The registry is keyed by `log_id`; `log_id`s are minted uniquely per process
//! (`difflog_<n>`), so a single shared registry never collides across concurrent
//! diff operations. Nothing here inspects payloads — the log layer hands back the
//! opaque taut-encoded `DiffOutputRecord` blobs the producer pushed.

use std::sync::OnceLock;

use pyo3::PyResult;

use gwz_core::diff::{DiffLogRegistry, LogReadRequest, LogReadResponse, LogReadState};

use crate::error;

static REGISTRY: OnceLock<DiffLogRegistry> = OnceLock::new();

/// The one process-global diff-output log registry. `handle_diff` mints into it;
/// the reader/end-stream pyfunctions read from it.
pub(crate) fn registry() -> &'static DiffLogRegistry {
    REGISTRY.get_or_init(DiffLogRegistry::new)
}

/// One blocking read against a registered log by id (the `diff.output` reader
/// path). `cursor = None` reads from the first record; `timeout_ms = None` blocks
/// until data/terminal, `Some(0)` probes, `Some(n>0)` degrades to a probe (v0 has
/// no core-side clock — see `log_service` docs). Returns the delivered record
/// payloads (opaque taut-encoded `DiffOutputRecord` blobs), the always-present
/// resume cursor, and the delivery state as a stable lowercase string.
pub(crate) fn read(
    log_id: &str,
    stream_id: &str,
    cursor: Option<u64>,
    max_records: Option<u32>,
    max_bytes: Option<u64>,
    timeout_ms: Option<u64>,
) -> PyResult<(Vec<Vec<u8>>, u64, &'static str)> {
    let request = LogReadRequest {
        stream_id: stream_id.to_owned(),
        cursor,
        max_records,
        max_bytes,
        timeout_ms,
    };
    let response = registry()
        .read(log_id, &request)
        .map_err(|err| error::runtime(err.to_string()))?;
    Ok(project(response))
}

/// End a reader's stream (taut-shape D4): drop its held read and, under
/// `stop_when=last_reader`, fire `ProducerStop` if it was the last reader.
/// Unknown `log_id` is treated as already-released (idempotent, no error) so a
/// client's cancellation/close path never fails on a log the operation already
/// dropped.
pub(crate) fn end_stream(log_id: &str, stream_id: &str) {
    if let Ok(log) = registry().get(log_id) {
        log.end_stream(stream_id);
    }
}

fn project(response: LogReadResponse) -> (Vec<Vec<u8>>, u64, &'static str) {
    let records = response
        .records
        .into_iter()
        .map(|record| record.payload)
        .collect();
    (records, response.next_cursor, state_str(response.state))
}

/// Stable wire spelling of the delivery state. These lowercase tokens are the
/// contract the Python `diff_output` cursor loop drives on
/// (`data`/`would_block`/`eof`/`closed`/`failed`/`expired`).
fn state_str(state: LogReadState) -> &'static str {
    match state {
        LogReadState::Data => "data",
        LogReadState::WouldBlock => "would_block",
        LogReadState::Eof => "eof",
        LogReadState::Closed => "closed",
        LogReadState::Failed => "failed",
        LogReadState::Expired => "expired",
    }
}

/// Convert a caller-supplied millisecond timeout into the `LogReadRequest`
/// timeout shape. A negative value (sentinel for "block") maps to `None`.
pub(crate) fn timeout_from_ms(timeout_ms: Option<i64>) -> Option<u64> {
    match timeout_ms {
        None => None,
        Some(ms) if ms < 0 => None,
        Some(ms) => Some(ms as u64),
    }
}
