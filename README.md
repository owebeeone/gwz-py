# gwz-py

`gwz-py` provides Python bindings to `gwz-core` and a Python implementation of
the GWZ (Git Workspace Zone) CLI. The CLI keeps the bindings exercised through
real user-facing workflows, helping ensure that Python applications can rely on
the workspace operations exposed by the message-driven core engine.

The `gwz-py` CLI is intended to be functional and follows the same command
model. For general terminal use, the Rust [`gwz`](https://github.com/owebeeone/gwz-cli)
CLI is the primary and more thoroughly tested implementation. Use `gwz-py` when
Python API integration or a Python-distributed CLI is the requirement.

Both the Python API and CLI call the native `gwz-core` extension; the package
does not shell out to or bundle the Rust `gwz` executable. The current package
uses an in-process bridge, while the typed message boundary is designed to also
support a separately hosted core through a remote adapter.

## Install

```sh
python -m pip install gwz
```

The distribution installs the `gwz-py` command and the `gwz` Python package.

## Python API

```python
from pathlib import Path

from gwz import Client


async with Client(root=Path(".")) as client:
    response = await client.status(combined=True)
```

Long-running operations such as clone, materialize, pull, and push also expose
streaming forms for operation progress events. Transfer progress arrives at most
once per member every 100 ms, as in gwz-cli; pass `progress_min_interval_ms=0`
to receive every update.

Every method except `log` raises `gwz.GwzOperationError` when the aggregate
status is not `ok`, `noop` or `accepted`. The exception keeps the typed
response in `response`. Its `member_errors` is the response's top-level
`errors`: on a `partial`, `failed` or `rejected` result it holds, first, the
error of each member that failed or was refused; on a `partial` result, the
other members succeeded
([Failed, rejected and partial results](https://owebeeone.github.io/gwz-cli/MachineOutput/#failed-rejected-and-partial-results)).

## Python CLI

```sh
gwz-py --help
gwz-py status
gwz-py diff
gwz-py log
```

Workspace concepts and workflows are shared with the Rust CLI. Start with the
[GWZ Quick Start](https://owebeeone.github.io/gwz-cli/QuickStart/) and use the
[repository lifecycle guide](https://owebeeone.github.io/gwz-cli/RepoLifecycle/)
for create, publish, detach, attach, and identity-verification behavior.

### Unified commit log

`gwz-py log` renders the same core commit-log records as the Rust CLI. Its
compact default shows the recorded date, workspace-relative member set,
short hash, and subject. Use `gwz-py log --full --body` for git-style blocks
with the complete member table and commit body. Human degradations are written
to stderr; output is never paged, and `--color=auto` colors only a terminal.

`--json` emits one `{"schema": "gwz.log/v0", "records": [...]}` document.
`--jsonl` begins with the schema header and then emits one entry or degradation
record per line. Both machine forms are byte-compatible with Rust `gwz` for
the same protocol records, including the explicit `lossy` flag for source
bytes converted to U+FFFD.

## Native Bridge And Repository Lifecycle

The asynchronous client sends generated protocol requests through the native
extension. For example:

```python
from pathlib import Path

from gwz import Client


async with Client(root=Path("/work/ws")) as client:
    await client.clone_repo_member(
        "git@github.com:org/shared.git",
        "libs/shared",
        member_id="mem_shared",
        source_id="src_shared",
    )
    await client.detach_repo_member("mem_shared")
    await client.attach_repo_member("mem_shared")
```

Use `clone_repo_member_stream(...)` when the caller needs clone progress. The
native core verifies snapshot and marker commit evidence before reactivating a
historical designation; it does not fetch missing history automatically.

## Development

Install the development dependencies and run the Python tests:

```sh
python -m pip install -e ".[dev]"
python run_tests.py
```

Cross-driver tests need a `gwz` CLI. `GWZ_RUST_BIN` names one explicitly and is
always honoured; otherwise the runner builds the sibling `gwz-cli` checkout and
picks the binary up from where cargo actually put it. A `gwz-cli` that is a
member of the cargo workspace above it (its parent `Cargo.toml` lists it under
`[workspace] members`) is built into the *workspace* `target/`, so that one is
preferred and any leftover `gwz-cli/target/debug/gwz` is ignored; a standalone
`gwz-cli` checkout uses its own `target/`. The runner prints the binary it chose
and the reason.

Build the native extension locally:

```sh
python -m maturin develop
python -m pytest src/tests/test_native_bridge.py -q
```

The transport rows (`src/tests/test_client_host_transport.py`) run against the
candidate extension, built with `--cfg gwz_transport_candidate` on the `gwz-core`
and `gwz-transport` checkouts beside this one, and skip without it.
`scripts/build_candidate_extension.py DIR` builds it in `DIR`, a new directory
outside the workspace, and prints the module's path, which
`GWZ_PY_NATIVE_MODULE` names to the suite. The runner does both:

```sh
python run_tests.py --candidate /tmp/gwz-py-candidate
```

Check or regenerate the protocol API against the sibling `gwz-core` checkout:

```sh
python scripts/check_protocol_drift.py
python scripts/regen_protocol.py --check
```

The release smoke test builds and repairs a wheel, installs it in a fresh
environment, exercises the installed CLI against a workspace fixture, and
checks operation events and materialized state:

```sh
python scripts/package_smoke.py
```

## Platform And Status

Status: alpha.

CI validates macOS, Linux, and Windows. Source builds require Rust 1.95 or newer
and may need platform OpenSSL, libgit2, and SSH prerequisites when a wheel is
not available. Windows source builds can provide those dependencies through
`vcpkg` with `VCPKG_ROOT` set.

If `gwz._gwz_core` is missing in a development checkout, run
`python -m maturin develop` from this directory.

## License

`gwz-py` is licensed under GPL-2.0-only.
