//! Process-global owner for operation-scoped commit-log output spools.

use std::sync::OnceLock;

use pyo3::PyResult;

use gwz_core::operation::{CommitLogOutputRegistry, CommitLogReadRequest, CommitLogReadState};

use crate::{codec, error};

static REGISTRY: OnceLock<CommitLogOutputRegistry> = OnceLock::new();

pub(crate) fn registry() -> &'static CommitLogOutputRegistry {
    REGISTRY.get_or_init(CommitLogOutputRegistry::new)
}

pub(crate) fn read(
    log_id: &str,
    cursor: Option<u64>,
    max_records: Option<u32>,
) -> PyResult<(Vec<Vec<u8>>, u64, &'static str)> {
    let response = registry()
        .read(
            log_id,
            &CommitLogReadRequest {
                cursor,
                max_records,
            },
        )
        .map_err(error::model)?;
    let records = response
        .records
        .into_iter()
        .map(|record| codec::encode_message("encode LogOutputRecord", || record.to_cbor()))
        .collect::<PyResult<Vec<_>>>()?;
    let state = match response.state {
        CommitLogReadState::Data => "data",
        CommitLogReadState::Eof => "eof",
    };
    Ok((records, response.next_cursor, state))
}

pub(crate) fn release(log_id: &str) {
    registry().release(log_id);
}
