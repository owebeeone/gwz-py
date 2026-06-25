mod branch_stash;
mod git_mutation;
mod materialize;
mod read;

use pyo3::PyResult;

use crate::error;

pub(crate) fn call(
    method: &str,
    request_message: &str,
    response_message: &str,
    request_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    match method {
        "create_workspace" | "init_from_sources" | "add_existing_repo" | "create_repo"
        | "repo_sync" | "status" | "ls" => {
            read::call(method, request_message, response_message, request_bytes)
        }
        "materialize" | "snapshot" | "tag" | "capture" => {
            materialize::call(method, request_message, response_message, request_bytes)
        }
        "commit" | "stage" | "pull_head" | "pull_snapshot" | "push" => {
            git_mutation::call(method, request_message, response_message, request_bytes)
        }
        "branch" | "stash" => {
            branch_stash::call(method, request_message, response_message, request_bytes)
        }
        other => Err(error::unsupported_method(other)),
    }
}
