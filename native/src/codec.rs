use std::panic::{AssertUnwindSafe, catch_unwind};

use pyo3::PyResult;

use crate::error;

pub(crate) fn require_request(method: &str, actual: &str, expected: &str) -> PyResult<()> {
    if actual == expected {
        Ok(())
    } else {
        Err(error::protocol(format!(
            "{method} expects {expected}, got {actual}"
        )))
    }
}

pub(crate) fn require_response(method: &str, actual: &str, expected: &str) -> PyResult<()> {
    if actual == expected {
        Ok(())
    } else {
        Err(error::protocol(format!(
            "{method} returns {expected}, got {actual}"
        )))
    }
}

pub(crate) fn decode_cbor(data: &[u8]) -> PyResult<gwz_core::Cbor> {
    catch_protocol("decode CBOR", || gwz_core::decode(data))
}

pub(crate) fn encode_cbor(value: &gwz_core::Cbor) -> Vec<u8> {
    gwz_core::encode(value)
}

pub(crate) fn catch_protocol<T>(context: &'static str, f: impl FnOnce() -> T) -> PyResult<T> {
    catch_unwind(AssertUnwindSafe(f)).map_err(|_| error::protocol(format!("{context} failed")))
}
