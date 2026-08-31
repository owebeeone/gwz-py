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
streaming forms for operation progress events.

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

Build the native extension locally:

```sh
python -m maturin develop
python -m pytest src/tests/test_native_bridge.py -q
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
