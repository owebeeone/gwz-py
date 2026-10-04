use std::time::Duration;

use pyo3::prelude::*;

mod client_host;
mod codec;
mod diff_logs;
mod dispatch;
mod error;
mod log_outputs;
mod operations;
mod route;
mod shims;
mod worker_host;

/// Installed descriptor only; HTTP composition remains deferred. Loader lookup
/// cannot be redirected with Python __file__, cwd, PATH or environment values.
#[pyfunction]
fn sspi_worker_descriptor() -> PyResult<std::ffi::OsString> {
    worker_descriptor_path(worker_host::descriptor())
}

fn worker_descriptor_path(
    result: Result<gwz_sspi::WorkerExecutable, gwz_sspi::ErrorKind>,
) -> PyResult<std::ffi::OsString> {
    result
        .map(|worker| worker.path().as_os_str().to_owned())
        .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(format!("{error:?}")))
}

#[pyfunction]
fn health() -> &'static str {
    "ok"
}

#[pyfunction]
fn version() -> &'static str {
    gwz_core::version()
}

#[pyfunction]
fn provenance() -> &'static str {
    gwz_core::BUILD_PROVENANCE
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

/// Read one bounded batch from the commit-history output spool.
#[pyfunction]
#[pyo3(signature = (log_id, cursor=None, max_records=None))]
fn log_output_read(
    py: Python<'_>,
    log_id: &str,
    cursor: Option<u64>,
    max_records: Option<u32>,
) -> PyResult<(Vec<Vec<u8>>, u64, String)> {
    let log_id = log_id.to_owned();
    py.detach(move || {
        let (records, next_cursor, state) = log_outputs::read(&log_id, cursor, max_records)?;
        Ok((records, next_cursor, state.to_owned()))
    })
}

/// Idempotently release a commit-history output and its anonymous spool.
#[pyfunction]
fn log_output_release(log_id: &str) {
    log_outputs::release(log_id);
}

#[pyfunction]
fn operation_result(py: Python<'_>, operation_id: &str) -> PyResult<Vec<u8>> {
    let operation_id = operation_id.to_owned();
    // The wait must not hold the GIL: a failed submit's worker renders its
    // Python error's text into the record this waits for, which takes the
    // GIL, so a waiter holding it would deadlock with that worker.
    py.detach(move || {
        let result = operations::result(&operation_id)?;
        codec::encode_message("encode OperationResult", || result.to_cbor())
    })
}

#[pyfunction]
fn try_operation_result(operation_id: &str) -> PyResult<Option<Vec<u8>>> {
    operations::try_result(operation_id)?
        .map(|result| codec::encode_message("encode OperationResult", || result.to_cbor()))
        .transpose()
}

#[pyfunction]
fn merge_operation_response(py: Python<'_>, operation_id: &str) -> PyResult<Vec<u8>> {
    let operation_id = operation_id.to_owned();
    py.detach(move || {
        let response = operations::merge_response(&operation_id)?;
        codec::encode_message("encode MergeResponse", || response.to_cbor())
    })
}

#[pymodule]
fn _gwz_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(health, module)?)?;
    module.add_function(wrap_pyfunction!(sspi_worker_descriptor, module)?)?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_function(wrap_pyfunction!(provenance, module)?)?;
    // Each Client's network operations run through its own host (1.1.0 S6.2).
    module.add_class::<client_host::ClientHost>()?;
    module.add_function(wrap_pyfunction!(subscribe_events, module)?)?;
    module.add_function(wrap_pyfunction!(wait_events, module)?)?;
    module.add_function(wrap_pyfunction!(operation_result, module)?)?;
    module.add_function(wrap_pyfunction!(try_operation_result, module)?)?;
    module.add_function(wrap_pyfunction!(merge_operation_response, module)?)?;
    module.add_function(wrap_pyfunction!(diff_log_read, module)?)?;
    module.add_function(wrap_pyfunction!(diff_log_end_stream, module)?)?;
    module.add_function(wrap_pyfunction!(log_output_read, module)?)?;
    module.add_function(wrap_pyfunction!(log_output_release, module)?)?;
    Ok(())
}

// Qualification artifacts must never silently select another platform/route.
cfg_if::cfg_if! { if #[cfg(all(gwz_windows_https_qualification, not(all(windows, gwz_transport_candidate))))] {
    compile_error!("gwz_windows_https_qualification requires Windows and gwz_transport_candidate");
} }
