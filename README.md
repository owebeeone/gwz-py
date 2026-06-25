# gwz-py

Python bindings and an installable `gwz` command for GWZ multi-repository
workspaces.

Status: alpha scaffold. The Python package shape, generated taut protocol API,
async client facade, CLI entry point, and initial native `gwz-core` bridge exist.
The native bridge currently supports the health/version smoke path and `ls`.

```sh
python -m pip install -e ".[dev]"
python run_tests.py
```

Build the native extension locally with maturin:

```sh
python -m maturin develop
python -m pytest src/tests/test_native_bridge.py -q
```

The native crate requires Rust 1.95 or newer and links the sibling
`../gwz-core` checkout during local development. `gwz-core` depends on `git2`
with HTTPS and SSH support, so source builds may need platform OpenSSL, libgit2,
and SSH build prerequisites when wheels are not available.

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
