# GWZ Python Package Design

Status: active design

This plan supersedes the earlier GWS-named design and filename. The repository
remains `gwz-py`, the PyPI distribution is `gwz-py`, the import package is
`gwz`, and the installed console command is `gwz`.

## Review Of The Old Plan

The prior design had the right core constraint: Python API calls must not shell
out to the `gwz` executable. The API should talk to `gwz-core` through a narrow
protocol boundary, and public workspace operations should be async.

The parts that are now stale:

- It used legacy GWS names. The project should align on GWZ before the first
  publish.
- It made bundling the Rust `gwz` binary a hard release requirement. That is now
  a late packaging decision, not the primary deliverable.
- It did not make the taut-generated Python API the central protocol surface.
- It treated release packaging before there was a usable Python package shape.
- It did not leave room for a Python CLI that demonstrates the same `gwz-core`
  protocol plugin used by the API.

## Design Review Disposition

The external review produced actionable corrections and they are now design
requirements:

- The bridge must model operation events by `operation_id`, using the generated
  `events.subscribe` and `operation.result` methods. Do not build a generic
  `stream(method, request, event_type, request)` ABI.
- Branch merge already has a protocol representation: `BranchOp.merge` uses
  `BranchRequest.start_ref` as the merge source. The Python facade may expose a
  clearer `source_ref` keyword, but it must encode to `start_ref`.
- `materialize --switch <branch>` is `MaterializeTargetKind.branch` with
  `MaterializeTarget.name`; expose this as `client.materialize("branch",
  name=...)` and a convenience `client.switch(...)`.
- `snapshot --branch[=<name>]` is `SnapshotSourceKind.current` for bare
  `--branch` and `SnapshotSourceKind.branch` with `branch=<name>` for named
  branch snapshots. Bare `--branch` is not the same as omitting `source`: it
  validates that selected members are attached, non-unborn, and on a coherent
  branch set.
- `repo_sync(member_path)` is just `RequestMeta.selection.paths`. It refreshes
  manifest metadata from materialized local repositories; it does not fetch,
  push, check out branches, or rewrite the lock.
- `clone` and `forall` are CLI workflows outside the generated `GwzCore` service
  surface. They must not be presented as normal taut-backed core methods.

## Product Goals

- Publish `gwz-py` to PyPI.
- Provide `import gwz` Python bindings for `gwz-core` workspace operations.
- Install a `gwz` command when users install `gwz-py`.
- Keep the Python API async and protocol-oriented.
- Use the taut-generated Python API from `gwz-core/protocol/gwz.taut.py` as the
  Python request/response model surface.
- Track the full current `GwzCore` service surface, including repo sync, branch,
  and coordinated stash operations.
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
          gwz.ir.json
    tests/
```

Tests live under `src/tests`, matching `pyproject.toml` test discovery. Do not add
package-internal `gwz.tests` modules unless the packaging strategy changes.

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
artifact writes, status calculation, snapshots, tags, branch management,
coordinated stash bundles, pull, push, and operation events.

## Taut Protocol Use

`scripts/regen_protocol.py` regenerates Python protocol files from
`../gwz-core/protocol/gwz.taut.py` using `tautc --api-only`. It writes:

- `src/gwz/protocol/generated/api.py`
- `src/gwz/protocol/generated/__init__.py`
- `src/gwz/protocol/generated/gwz.ir.json`

The generated Python API provides dataclasses and enum types. The packaged IR
JSON lets the runtime bridge encode and decode with `taut.wire.codec` without
importing a local `gwz-core` checkout. Generated taut `client.py` and `server.py`
transport stubs are intentionally not emitted into the runtime package; the
handwritten `CoreBridge` is the only Python transport boundary.

Generation is a source-control gate:

```text
python scripts/regen_protocol.py --check
python scripts/regen_protocol.py
```

Release builds should use a released `taut-proto` version and a released
`gwz-core` schema artifact. Local development may point at sibling checkouts.

The generated API must stay current with the service declared in
`gwz-core/protocol/gwz.taut.py`. As of this plan, that includes
`repo_sync`, `stash`, `branch`, `events.subscribe`, and `operation.result`.
`ExecRequest` and `ExecResponse` remain generated protocol data only because
taut module splitting is not available yet; they are not service methods.

`RequestMeta.schema_version` and any bridge protocol fingerprint must come from
one generated/core-owned source, not from independent hardcoded Python literals.
If `gwz-core` does not yet expose one canonical schema-version literal, Phase 0
must resolve that core/schema coordination item before Python treats the version
string as an enforceable drift guard. The native extension should expose enough
protocol metadata for Python to fail fast if packaged `gwz.ir.json` does not
match the linked `gwz-core` protocol; until the version literal is canonical, the
packaged IR fingerprint is the enforceable drift guard.

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

Current operation surface:

| Operation | Python API |
| --- | --- |
| Create workspace | `await client.create_workspace(...)` |
| Initialize from sources | `await client.init_from_sources(...)` |
| Add existing repo | `await client.add_existing_repo(...)` |
| Create member repo | `await client.create_repo(...)` |
| Refresh repo metadata | `await client.repo_sync(...)` |
| Status | `await client.status(...)` |
| List members | `await client.ls(...)` |
| Materialize | `await client.materialize(...)` |
| Snapshot | `await client.snapshot(...)` |
| Tag | `await client.tag(...)` |
| Branch | `await client.branch(...)` |
| Stash | `await client.stash(...)` |
| Capture | `await client.capture(...)` |
| Commit | `await client.commit(...)` |
| Stage | `await client.stage(...)` |
| Pull head | `await client.pull_head(...)` |
| Pull snapshot | `await client.pull_snapshot(...)` |
| Push | `await client.push(...)` |

Ergonomic helpers should cover current CLI/core edge cases:

- `await client.materialize("branch", name="feature")` and
  `await client.switch("feature")` encode `MaterializeTargetKind.branch`.
- `await client.snapshot("snap", current_branch=True)` encodes a bare
  `snapshot --branch`.
- `await client.snapshot("snap", branch="release/1")` encodes
  `SnapshotSourceKind.branch`.
- `await client.branch(op="merge", source_ref="refs/heads/topic")` encodes the
  source ref into `BranchRequest.start_ref`.
- Branch create should let `gwz-core` own default start-ref behavior unless a
  Python helper explicitly documents a stronger CLI compatibility reason.
- `await client.repo_sync("member/path")` encodes the member path into
  `RequestMeta.selection.paths`.

Operations that emit member progress should expose async generators for the
handlers that actually accept an `EventSink`: `init_from_sources_stream`,
`materialize_stream`, `pull_head_stream`, `pull_snapshot_stream`, and
`push_stream`. A streaming helper submits the operation, reads
`ResponseMeta.operation_id`, subscribes through `events.subscribe`, and can read
the final record through `operation.result`. Branch, stash, snapshot, tag,
capture, commit, stage, status, ls, create, and repo-sync methods are
request/response APIs unless `gwz-core` later adds event-sink support for them.

Branch and stash are no longer future design items. `gwz-core` now exposes
`BranchRequest`/`BranchResponse` and `StashRequest`/`StashResponse` in the taut
service, and `gwz-cli` now documents `gwz branch` and `gwz stash`. The Python
wrappers should be thin request builders over those generated types, not
separate Python behavior.

## Bridge Contract

The Python bridge interface is intentionally message-first for role-in service
methods:

```python
class CoreBridge:
    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: object,
    ) -> object: ...

    def subscribe_events(
        self,
        operation_id: str,
    ) -> AsyncIterator[object]: ...

    async def operation_result(
        self,
        operation_id: str,
    ) -> object: ...
```

The native implementation should be a PyO3/maturin extension named
`gwz._gwz_core`. It should accept encoded taut request bytes plus method/message
metadata for `GwzCore` service calls, dispatch into `gwz-core`, and return
encoded taut response bytes. Event streaming is not method/request based:
Python subscribes to `events.subscribe` with an `operation_id`, and reads final
records with `operation.result`. These two role-out methods use scalar
`operation_id` parameters rather than request dataclasses.

The initial native bridge ABI is CBOR, not ad hoc JSON: Python encodes generated
dataclasses through `to_wire` and `taut.wire.codec` into bytes, and Rust decodes
with the `gwz-core` generated CBOR/runtime support. A transitional JSON ABI is
allowed only if it uses taut's IR-driven JSON codec on both sides; naive
`json.dumps` is not acceptable because integer and enum wire rules would drift.

This avoids a function-per-operation FFI surface. Schema evolution stays in the
taut contract, not in duplicated Python method signatures. The Rust extension
still owns a method-name dispatch table because `gwz-core` exposes individual
handlers, not a reusable public router. That Rust table should mirror the current
`gwz-cli` behavior or, preferably, move toward a shared `gwz-core` dispatch API
so the CLI and Python extension do not maintain parallel routing logic.
Non-service CLI workflows such as `clone` and `forall` are explicit exceptions
and must be kept outside `CoreBridge.call`.

The Rust side may run blocking `gwz-core` handlers internally, but the extension
must move blocking work off the Python event loop, release the GIL around
blocking Rust handlers through PyO3's thread APIs, and deliver streaming events
back through bounded async-safe queues.

The committed native build path is maturin-centered. The initial PyO3 extension
is `gwz._gwz_core`; it exposes `health()`, `version()`, and a CBOR byte-oriented
`call()` implementation for `ls`. Later phases expand the Rust method dispatch
table and operation event/result runtime.

## CLI Strategy

Installing `gwz-py` installs a `gwz` console script. The script is a Python CLI
that uses `gwz.Client` and the same bridge as the API. That CLI is both useful on
its own and a concrete example of how to build a command surface over the
`gwz-core` protocol plugin.

The Python CLI target is now the current Rust CLI surface, including `branch`
and `stash`.

Two commands are intentionally special:

- `forall` remains CLI-local because `gwz-core` defines only support data for it;
  it does not execute child processes.
- `clone` is a one-shot CLI workflow, not a generated `GwzCore` method. Rust core
  implements helper logic that clones the root repository and then materializes
  the lock, but the taut service has no `CloneWorkspaceRequest`. The Python CLI
  must either use a named native bridge extension for this workflow, implement
  the local Git clone step before calling `materialize("lock")`, or dispatch to
  the Rust binary in packaging modes that include it. First release must either
  implement `gwz clone` through one of these paths or list it as a known CLI
  limitation.

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

By default, high-level client methods raise `GwzOperationError` for rejected,
failed, partial, dirty, or conflicted aggregate statuses. That exception must
preserve request id, operation id, aggregate status, member errors, and the
original protocol response so callers can still inspect the typed response.
Stream helpers should drain all events, read the final `operation.result`, and
then apply the same raise-with-response policy. Lower-level bridge/test helpers
may expose raw responses directly.

Avoid a public-name collision between the generated protocol data class
`gwz.protocol.generated.GwzError` and the Python exception base class
`gwz.errors.GwzError`. The package should not star-export both into the same
namespace; if an ergonomic alias is needed, prefer a data-name such as
`GwzErrorDetail`.

## Testing Strategy

- Unit-test request construction with a fake bridge.
- Assert every public operation is async.
- Import-test generated taut API classes and packaged IR JSON.
- Run `python scripts/regen_protocol.py --check` in CI.
- Integration-test the native bridge against `gwz-core` fixtures once the PyO3
  extension exists.
- CLI smoke-test Python `gwz` commands against fake bridge fixtures first, then
  against the native bridge.
- Parity-test Python CLI output against the Rust CLI, including `branch` and
  `stash`, before choosing the release CLI packaging mode.

## Milestones

1. Initialize the `gwz-py` Python package, generated protocol API, async client
   facade, bridge interface, Python CLI entry point, and tests.
2. Regenerate protocol files from the current `gwz-core` taut schema and keep
   `python scripts/regen_protocol.py --check` green.
3. Implement dataclass/dict/CBOR protocol adapters driven by packaged
   `gwz.ir.json`.
4. Add the PyO3 native extension skeleton and a single bridge call for `ls`.
5. Expand bridge coverage to `status`, `create_workspace`, `repo_sync`, and
   `init_from_sources`.
6. Add operation-id event subscription and result lookup for
   `init_from_sources`, `materialize`, `pull_head`, `pull_snapshot`, and `push`.
7. Cover all current `GwzCore` service methods, explicitly including
   `repo_sync`, `branch`, and `stash`.
8. Expand the Python CLI to match core `gwz-cli` command parsing and rendering,
   including `gwz branch`, `gwz stash`, and explicit non-service handling for
   `gwz clone` and `gwz forall`.
9. Run CLI parity tests and decide whether release wheels ship the Python CLI or
   a bundled Rust binary behind the `gwz` command.
10. Add GitHub Actions for source distributions, wheels, protocol regen checks,
   native bridge integration tests, and PyPI publish.

## Open Decisions

- Whether release wheels use setuptools plus an optional prebuilt extension or
  switch fully to maturin. This must be decided before native extension work
  begins; later packaging work should only refine wheel/platform strategy.
- Whether `gwz clone` is implemented through a named non-service bridge
  extension, Python-local Git clone followed by `materialize("lock")`, or Rust
  binary dispatch.
- Whether `gwz-py` releases are version-locked to `gwz-core` tags or consume a
  manifest that records separate `gwz-py`, `gwz-core`, and `taut-proto`
  versions.
- Whether final release wheels bundle the Rust CLI, keep the Python CLI, or
  publish both with an environment-controlled dispatch choice.

## Plan Cross-Reference

`GwzPyPlan.md` breaks these milestones into implementation phases. Phase 0 locks
the scaffold and protocol boundary, Phase 1 owns codec/transport/error-model
work, Phase 2 commits the native build backend and first bridge call, Phase 3
expands native service coverage, Phase 4 implements operation-id events/results,
Phase 5 builds CLI parity, Phase 6 handles packaging/CI/release mode, and Phase
7 hardens for PyPI.
