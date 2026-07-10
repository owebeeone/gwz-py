# gwz-py

Python bindings and an installable `gwz-py` command for GWZ multi-repository
workspaces.

Status: alpha. The Python package shape, generated taut protocol API, async
client facade, CLI entry point, and native `gwz-core` bridge exist. The native
bridge supports request/response calls plus operation event streaming for
long-running operations such as `clone`, `materialize`, `pull`, and `push`.
The nested `repo` surface is argv-compatible with Rust `gwz` for `add`,
`clone`, `create`, `detach`, `attach`, and `sync`, including member/source id
overrides and repo-clone dry-run behavior.

Release mode: installing the PyPI distribution `gwz` installs the Python `gwz-py` CLI
(`gwz.cli:main`). The CLI uses the same native `gwz-core` extension as the
Python API; first-line PyPI wheels do not bundle or dispatch to the Rust `gwz`
binary.

```sh
python -m pip install gwz
```

```sh
python -m pip install -e ".[dev]"
python run_tests.py
```

Build the native extension locally with maturin:

```sh
python -m maturin develop
python -m pytest src/tests/test_native_bridge.py -q
```

Run the package smoke test before release-oriented changes. It builds a repaired
wheel, installs it into a fresh virtualenv, runs `gwz-py --help`, creates a
local workspace fixture, exercises installed `gwz-py clone`, verifies clone
progress events and materialized member state, then runs `gwz-py status` in the
clone:

```sh
python scripts/package_smoke.py
```

Check that the packaged protocol IR still matches the sibling `gwz-core` schema:

```sh
python scripts/check_protocol_drift.py
```

The native crate requires Rust 1.95 or newer and links the sibling
`../gwz-core` checkout during local development. `gwz-core` depends on `git2`
with HTTPS and SSH support, so source builds may need platform OpenSSL, libgit2,
and SSH build prerequisites when wheels are not available.

CI validates macOS, Linux, and Windows. Windows source builds use the same
native extension path as other platforms and need OpenSSL/libgit2 prerequisites
available to Cargo, for example through `vcpkg` with `VCPKG_ROOT` set.

If `gwz._gwz_core` is missing, pass a custom bridge in tests or run
`python -m maturin develop` from this directory.

Regenerate the protocol API from the sibling `gwz-core` checkout:

```sh
python scripts/regen_protocol.py
python scripts/regen_protocol.py --check
```

Example API shape:

```python
from pathlib import Path

from gwz import Client


async with Client(root=Path(".")) as client:
    response = await client.status(combined=True)
```

## Repository Member Lifecycle

The async client and `gwz-py repo` commands follow the same lifecycle contract
as Rust `gwz`:

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

Use `clone_repo_member_stream(...)` instead of `clone_repo_member(...)` when the
caller needs clone progress events. The corresponding CLI forms are `gwz-py
repo clone`, `gwz-py repo detach`, and `gwz-py repo attach`; their positional
arguments and member/source id options match Rust `gwz`.

`detach_repo_member` accepts exactly one active member id or
workspace-relative path. `attach_repo_member` requires one literal historical
member id. Their positional selector cannot be combined with selection keyword
arguments in `**meta`.

Bare `add_existing_repo` can reactivate a detached row only when exactly one
inactive row at the same path has a non-empty historical commit evidence set
and every recorded commit exists locally. An explicit new `member_id` creates a
new designation instead. Explicit attach with no recorded evidence succeeds
with this exact warning:

```text
attached <member_id>; no snapshot or marker commit evidence was available to verify repository identity
```

Attach, evidence-backed add, and reuse of an existing `source_id` fail with
`GwzErrorCode.source_identity_mismatch` when any required snapshot or marker
commit is missing. The native core does not fetch automatically; fetch
sufficient history into shallow or incomplete repositories before retrying.

The Python API uses the `gwz-core` bridge. It must not shell out to the Rust
`gwz` executable.
