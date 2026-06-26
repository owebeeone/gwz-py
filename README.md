# gwz-py

Python bindings and an installable `gwz` command for GWZ multi-repository
workspaces.

Status: alpha. The Python package shape, generated taut protocol API, async
client facade, CLI entry point, and native `gwz-core` bridge exist. The native
bridge supports request/response calls plus operation event streaming for
long-running operations such as `clone`, `materialize`, `pull`, and `push`.

Release mode: installing `gwz-py` installs the Python `gwz` CLI
(`gwz.cli:main`). The CLI uses the same native `gwz-core` extension as the
Python API; first-line PyPI wheels do not bundle or dispatch to the Rust `gwz`
binary.

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
wheel, installs it into a fresh virtualenv, runs `gwz --help`, creates a local
workspace fixture, exercises installed `gwz clone`, verifies clone progress
events and materialized member state, then runs `gwz status` in the clone:

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

The Python API uses the `gwz-core` bridge. It must not shell out to the `gwz`
executable.
