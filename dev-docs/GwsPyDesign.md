# GWZ Python Package Plan

Status: active replacement plan

This plan supersedes the earlier `gws-core` design. The repository remains
`gwz-py`, the PyPI distribution is `gwz-py`, the import package is `gwz`, and the
installed console command is `gwz`.

## Review Of The Old Plan

The prior design had the right core constraint: Python API calls must not shell
out to the `gwz` executable. The API should talk to `gwz-core` through a narrow
protocol boundary, and public workspace operations should be async.

The parts that are now stale:

- It used `gws-core` / `gws_core` names. The project should align on GWZ before
  the first publish.
- It made bundling the Rust `gwz` binary a hard release requirement. That is now
  a late packaging decision, not the primary deliverable.
- It did not make the taut-generated Python API the central protocol surface.
- It treated release packaging before there was a usable Python package shape.
- It did not leave room for a Python CLI that demonstrates the same `gwz-core`
  protocol plugin used by the API.

## Product Goals

- Publish `gwz-py` to PyPI.
- Provide `import gwz` Python bindings for `gwz-core` workspace operations.
- Install a `gwz` command when users install `gwz-py`.
- Keep the Python API async and protocol-oriented.
- Use the taut-generated Python API from `gwz-core/protocol/gwz.taut.py` as the
  Python request/response model surface.
- Provide a Python implementation of the `gwz` CLI as an example consumer of the
  `gwz-core` bridge.
- Defer the final CLI packaging choice until release hardening: the `gwz` script
  can dispatch to the Python CLI or to a bundled Rust binary.

## Non-Goals

- Do not reimplement Git workspace semantics in Python.
- Do not call the Rust `gwz` CLI from the Python API.
- Do not hand-author protocol model classes that can drift from taut.
- Do not commit to a daemon transport for local use.
- Do not solve every release platform in the first scaffold.

## Package Shape

The package follows the same broad pattern as `taut`:

```text
gwz-py/
  pyproject.toml
  README.md
  run_tests.py
  scripts/
    regen_protocol.py
  src/
    gwz/
      __init__.py
      _version.py
      bridge.py
      client.py
      cli.py
      errors.py
      py.typed
      protocol/
        __init__.py
        codec.py
        generated/
          __init__.py
          api.py
          client.py
          server.py
          gwz.ir.json
    tests/
```

Recommended package metadata:

| Artifact | Name |
| --- | --- |
| Repository | `gwz-py` |
| PyPI distribution | `gwz-py` |
| Import package | `gwz` |
| Console script | `gwz` |
| Native extension | `gwz._gwz_core` |
| Rust dependency | `gwz-core` |
| Taut schema source | `gwz-core/protocol/gwz.taut.py` |

If the bare `gwz` PyPI name becomes available before first publish, the
distribution can be renamed. The import package should still remain `gwz`.

## Architecture

```text
Python caller
  -> gwz.Client async methods
  -> taut-generated request dataclasses
  -> gwz.bridge.CoreBridge
  -> gwz._gwz_core native extension
  -> gwz-core Rust handlers
  -> taut-generated response dataclasses
```

The Python package owns async ergonomics, request construction, bridge lifecycle,
Python exceptions, type hints, and the optional Python CLI. `gwz-core` remains
the authority for workspace discovery, validation, planning, Git operations,
artifact writes, status calculation, snapshots, tags, pull, push, and operation
events.

## Taut Protocol Use

`scripts/regen_protocol.py` regenerates Python protocol files from
`../gwz-core/protocol/gwz.taut.py` using `tautc`. It writes:

- `src/gwz/protocol/generated/api.py`
- `src/gwz/protocol/generated/client.py`
- `src/gwz/protocol/generated/server.py`
- `src/gwz/protocol/generated/__init__.py`
- `src/gwz/protocol/generated/gwz.ir.json`

The generated Python files provide dataclasses and typed client/server stubs.
The packaged IR JSON lets the runtime bridge encode and decode with
`taut.wire.codec` without importing a local `gwz-core` checkout.

Generation is a source-control gate:

```text
python scripts/regen_protocol.py --check
python scripts/regen_protocol.py
```

Release builds should use a released `taut-proto` version and a released
`gwz-core` schema artifact. Local development may point at sibling checkouts.

## Public Python API

The primary entry point is an async client:

```python
from pathlib import Path

from gwz import Client

async with Client(root=Path("/work/ws")) as gwz:
    status = await gwz.status(combined=True)
    async for event in gwz.materialize_stream(target="lock"):
        print(event.message)
```

All public operations that inspect, plan, mutate, or observe a workspace must be
`async def` or async generators. Synchronous public symbols are limited to
dataclasses, enums, constants, errors, and pure request builders.

Initial operation surface:

| Operation | Python API |
| --- | --- |
| Create workspace | `await client.create_workspace(...)` |
| Initialize from sources | `await client.init_from_sources(...)` |
| Add existing repo | `await client.add_existing_repo(...)` |
| Create member repo | `await client.create_repo(...)` |
| Status | `await client.status(...)` |
| List members | `await client.ls(...)` |
| Materialize | `await client.materialize(...)` |
| Snapshot | `await client.snapshot(...)` |
| Tag | `await client.tag(...)` |
| Capture | `await client.capture(...)` |
| Commit | `await client.commit(...)` |
| Stage | `await client.stage(...)` |
| Pull head | `await client.pull_head(...)` |
| Pull snapshot | `await client.pull_snapshot(...)` |
| Push | `await client.push(...)` |

Operations that emit progress should also expose async generators, starting with
`init_from_sources_stream`, `materialize_stream`, `pull_snapshot_stream`, and
`push_stream`.

## Bridge Contract

The Python bridge interface is intentionally message-first:

```python
class CoreBridge:
    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: object,
    ) -> object: ...

    def stream(
        self,
        method: str,
        request_message: str,
        event_message: str,
        request: object,
    ) -> AsyncIterator[object]: ...
```

The native implementation should be a PyO3/maturin extension named
`gwz._gwz_core`. It should accept encoded taut request bytes plus method/message
metadata, dispatch into `gwz-core`, and return encoded taut response/event bytes.
Python can then decode those bytes using the packaged IR and bind them to the
generated dataclasses.

This avoids a function-per-operation FFI surface. Schema evolution stays in the
taut contract, not in duplicated Python/Rust method signatures.

The Rust side may run blocking `gwz-core` handlers internally, but the extension
must move blocking work off the Python event loop and deliver streaming events
back through async-safe queues.

## CLI Strategy

Installing `gwz-py` installs a `gwz` console script. The script is a Python CLI
that uses `gwz.Client` and the same bridge as the API. That CLI is both useful on
its own and a concrete example of how to build a command surface over the
`gwz-core` protocol plugin.

Late in release hardening, choose one of two packaging modes:

1. Keep `gwz` as the Python CLI.
2. Bundle the Rust `gwz` binary and make the Python console script dispatch to
   it when the binary is present.

The decision should be based on parity, startup cost, platform wheel complexity,
and maintenance burden. The Python API must stay bridge-backed either way.

## Error Model

Expose a small Python hierarchy:

| Exception | Use |
| --- | --- |
| `GwzError` | Base class for package errors. |
| `GwzProtocolError` | Invalid or unsupported protocol records. |
| `GwzOperationError` | A GWZ response has rejected, failed, partial, dirty, or conflicted status. |
| `GwzBridgeError` | Bridge transport or native extension failure. |
| `GwzCoreLoadError` | Native extension cannot be imported or initialized. |

When possible, exceptions should preserve request id, operation id, aggregate
status, member errors, and the original protocol response.

## Testing Strategy

- Unit-test request construction with a fake bridge.
- Assert every public operation is async.
- Import-test generated taut API classes and packaged IR JSON.
- Run `python scripts/regen_protocol.py --check` in CI.
- Integration-test the native bridge against `gwz-core` fixtures once the PyO3
  extension exists.
- CLI smoke-test Python `gwz` commands against fake bridge fixtures first, then
  against the native bridge.
- Parity-test Python CLI output against the Rust CLI before choosing the release
  CLI packaging mode.

## Milestones

1. Initialize the `gwz-py` Python package, generated protocol API, async client
   facade, bridge interface, Python CLI entry point, and tests.
2. Implement dataclass/dict/CBOR protocol adapters driven by packaged
   `gwz.ir.json`.
3. Add the PyO3 native extension skeleton and a single bridge call for `ls`.
4. Expand bridge coverage to `status`, `create_workspace`, and
   `init_from_sources`.
5. Add event streaming for `materialize` and `pull_snapshot`.
6. Cover all current `GwzCore` service methods.
7. Expand the Python CLI to match core `gwz-cli` command parsing and rendering.
8. Run CLI parity tests and decide whether release wheels ship the Python CLI or
   a bundled Rust binary behind the `gwz` command.
9. Add GitHub Actions for source distributions, wheels, protocol regen checks,
   native bridge integration tests, and PyPI publish.

## Open Decisions

- Whether release wheels use setuptools plus an optional prebuilt extension or
  switch fully to maturin once `gwz._gwz_core` lands.
- Exact bridge wire ABI: Python-encoded CBOR into Rust, Rust-encoded CBOR out, or
  a transitional JSON ABI while the extension is being built.
- Whether `gwz-py` releases are version-locked to `gwz-core` tags or consume a
  manifest that records separate `gwz-py`, `gwz-core`, and `taut-proto`
  versions.
- Whether final release wheels bundle the Rust CLI, keep the Python CLI, or
  publish both with an environment-controlled dispatch choice.
