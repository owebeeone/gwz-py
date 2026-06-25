use pyo3::PyResult;

use crate::{error, shims};

pub(crate) fn call(
    method: &str,
    _request_message: &str,
    _response_message: &str,
    _request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    match method {
        "commit" | "stage" => shims::backend(method),
        "pull_head" | "pull_snapshot" | "push" => shims::backend_with_events(method),
        other => error::unsupported(other),
    }
}
