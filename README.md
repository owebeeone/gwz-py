# gwz-py

Python bindings and an installable `gwz` command for GWZ multi-repository
workspaces.

Status: alpha scaffold. The Python package shape, generated taut protocol API,
async client facade, and CLI entry point exist. The native bridge to `gwz-core`
is the next implementation milestone.

```sh
python -m pip install -e ".[dev]"
python run_tests.py
```

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

The Python API must use the `gwz-core` bridge. It must not shell out to the
`gwz` executable.
