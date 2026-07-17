use std::time::Duration;

use pyo3::prelude::*;

mod codec;
mod diff_logs;
mod dispatch;
mod error;
mod operations;
mod shims;

#[pyfunction]
fn health() -> &'static str {
    "ok"
}

#[pyfunction]
fn version() -> &'static str {
    gwz_core::version()
}

#[pyfunction]
fn call(
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

    py.detach(move || dispatch::call(&method, &request_message, &response_message, &request_bytes))
}

#[pyfunction]
fn submit(
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

    py.detach(move || {
        dispatch::submit(&method, &request_message, &response_message, &request_bytes)
    })
}

#[pyfunction]
fn subscribe_events(operation_id: &str) -> PyResult<Vec<Vec<u8>>> {
    operations::events(operation_id)?
        .into_iter()
        .map(|event| codec::encode_message("encode OperationEvent", || event.to_cbor()))
        .collect()
}

#[pyfunction]
fn wait_events(
    py: Python<'_>,
    operation_id: &str,
    after_sequence: i64,
    timeout_ms: u64,
) -> PyResult<(Vec<Vec<u8>>, bool)> {
    let operation_id = operation_id.to_owned();
    py.detach(move || {
        let (events, complete) = operations::wait_events(
            &operation_id,
            after_sequence,
            Duration::from_millis(timeout_ms),
        )?;
        let event_bytes = events
            .into_iter()
            .map(|event| codec::encode_message("encode OperationEvent", || event.to_cbor()))
            .collect::<PyResult<Vec<_>>>()?;
        Ok((event_bytes, complete))
    })
}

/// Blocking read against a `diff.output` log by `log_id` (the byte-bearing patch
/// stream minted by a `diff` call). Runs under `py.detach` because a held read
/// blocks the calling thread on the log's condvar until the producer releases
/// data / seals / closes. Returns `(records, next_cursor, state)` where `records`
/// are the opaque taut-encoded `DiffOutputRecord` payloads (NUL-safe `PyBytes`),
/// `next_cursor` is the always-present resume position (taut-shape D8), and
/// `state` is the delivery state token (`data` / `would_block` / `eof` /
/// `closed` / `failed` / `expired`). `cursor = None` reads from the first record;
/// `timeout_ms = None` (or negative) blocks, `Some(0)` probes.
// The arg list mirrors the taut-shape LogReadRequest surface 1:1 (log_id +
// stream_id + cursor + the two batch bounds + timeout), so it is a deliberate
// wide pyfunction signature rather than a struct-worthy group.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (log_id, stream_id, cursor=None, max_records=None, max_bytes=None, timeout_ms=None))]
fn diff_log_read(
    py: Python<'_>,
    log_id: &str,
    stream_id: &str,
    cursor: Option<u64>,
    max_records: Option<u32>,
    max_bytes: Option<u64>,
    timeout_ms: Option<i64>,
) -> PyResult<(Vec<Vec<u8>>, u64, String)> {
    let log_id = log_id.to_owned();
    let stream_id = stream_id.to_owned();
    let timeout = diff_logs::timeout_from_ms(timeout_ms);
    py.detach(move || {
        let (records, next_cursor, state) =
            diff_logs::read(&log_id, &stream_id, cursor, max_records, max_bytes, timeout)?;
        Ok((records, next_cursor, state.to_owned()))
    })
}

/// End a `diff.output` reader's stream (taut-shape D4): drop its held read and,
/// under `stop_when=last_reader`, fire `ProducerStop` if it was the last reader,
/// letting core release retained render state. Idempotent — an unknown/released
/// `log_id` is a no-op, so a client's cancel/close path never fails.
#[pyfunction]
fn diff_log_end_stream(log_id: &str, stream_id: &str) {
    diff_logs::end_stream(log_id, stream_id);
}

#[pyfunction]
fn operation_result(operation_id: &str) -> PyResult<Vec<u8>> {
    let result = operations::result(operation_id)?;
    codec::encode_message("encode OperationResult", || result.to_cbor())
}

#[pyfunction]
fn try_operation_result(operation_id: &str) -> PyResult<Option<Vec<u8>>> {
    operations::try_result(operation_id)?
        .map(|result| codec::encode_message("encode OperationResult", || result.to_cbor()))
        .transpose()
}

#[pymodule]
fn _gwz_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(health, module)?)?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_function(wrap_pyfunction!(call, module)?)?;
    module.add_function(wrap_pyfunction!(submit, module)?)?;
    module.add_function(wrap_pyfunction!(subscribe_events, module)?)?;
    module.add_function(wrap_pyfunction!(wait_events, module)?)?;
    module.add_function(wrap_pyfunction!(operation_result, module)?)?;
    module.add_function(wrap_pyfunction!(try_operation_result, module)?)?;
    module.add_function(wrap_pyfunction!(diff_log_read, module)?)?;
    module.add_function(wrap_pyfunction!(diff_log_end_stream, module)?)?;
    Ok(())
}
