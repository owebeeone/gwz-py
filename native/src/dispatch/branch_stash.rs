use pyo3::PyResult;

use crate::{error, shims};

pub(crate) fn call(
    method: &str,
    _request_message: &str,
    _response_message: &str,
    _request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    match method {
        "branch" | "stash" => shims::backend(method),
        other => error::unsupported(other),
    }
}
