# GWS Python Design

Status: draft

This document defines the initial design for `gwz-py`, the Python repository
that provides a `gws-core` Python module for accessing GWZ multi-repo
workspaces from Python.

## Hard Requirements

- All public `gws-core` workspace operations MUST be async.
- `gws-core` MUST NOT shell out to `gwz` or use the CLI as its backend.
- `gws-core` MUST communicate with `gwz-core` by passing protocol messages
  across a Python/Rust bridge.
- `gwz-py` release artifacts MUST be built by GitHub Actions against released
  `gwz-core` artifacts.
- The Rust `gwz` CLI MUST be released as a bundled convenience CLI tool as part
  of the `gwz-py` release, including Windows amd64, macOS arm64, Linux amd64,
  and Linux arm64 builds.
- The bundled `gwz` CLI MUST also support a standalone install flow like `uv`,
  but the Python API must not use the CLI as its backend.

## Naming

The current repository names use `gwz-*`, while the requested Python package
name is `gws-core`. Until the project settles the spelling, this design uses the
following names:

| Artifact | Name |
| --- | --- |
| Python repository | `gwz-py` |
| Python distribution package | `gws-core` |
| Python import package | `gws_core` |
| Rust core repository | `gwz-core` |
| CLI repository | `gwz-cli` |
| CLI distribution and executable | `gwz`, released by `gwz-py` |

If the Python package should instead align on `gwz-core`, make that change
before the first public package release.

## Goals

- Provide Python access to GWZ workspace operations without requiring Python
  callers to construct shell commands.
- Keep the Python API message-oriented and compatible with the taut protocol
  used by `gwz-core`.
- Make every operation async so callers can integrate GWZ work into agents,
  web services, notebooks, workers, and IDEs without blocking the event loop.
- Support live operation events as async streams.
- Keep the Python/Rust boundary narrow: Python sends protocol messages, Rust
  core returns protocol messages.
- Keep `gwz` as the canonical user-facing convenience CLI binary.

## Non-Goals

- Do not reimplement Git workspace behavior in Python.
- Do not fork the protocol model away from `gwz-core`.
- Do not make the Python package responsible for terminal rendering or CLI help.
- Do not require a daemon for local use.
- Do not make a synchronous public operation API.
- Do not invoke `gwz` as a subprocess from `gws-core`.

## Architecture

```text
Python caller
  -> gws_core async API
  -> generated or protocol-shaped Python request models
  -> async message bridge
       -> Python encodes a protocol request message
       -> Rust bridge dispatches the request into gwz-core
       -> Rust bridge emits response, event, and result messages
  -> protocol response, event, and result models
```

The Python layer owns API ergonomics, async integration, bridge lifecycle,
Python exceptions, and type hints. The Rust core remains the behavioral
authority for workspace discovery, validation, planning, Git operations,
artifact writes, status calculation, snapshots, tags, pull, push, and
materialization.

## Async API Rule

Every public function that reads, discovers, plans, mutates, or observes a
workspace MUST be declared with `async def` or be an async generator. No sync
equivalent should be added for convenience.

Synchronous symbols are allowed only for inert data: dataclasses, enums,
constants, error classes, and pure request-building helpers that do not touch
the filesystem, Git, the network, or native bridge.

The package MUST NOT call `asyncio.run()` internally. Callers own the event
loop.

## Public API Shape

The primary entrypoint is an async client:

```python
from pathlib import Path

from gws_core import Client


async with Client(root=Path("/work/ws")) as gws:
    status = await gws.status(combined=True)
    async for event in gws.materialize_stream(target="lock"):
        print(event.message)
```

Top-level convenience functions may exist, but they must also be async:

```python
from pathlib import Path

from gws_core import status


response = await status(root=Path("/work/ws"), combined=True)
```

Initial operation surface:

| Operation | Python API |
| --- | --- |
| Create workspace | `await client.create_workspace(...)` |
| Initialize from sources | `await client.init_from_sources(...)` |
| Clone workspace | `await client.clone_workspace(...)` |
| Add existing repo | `await client.add_existing_repo(...)` |
| Create member repo | `await client.create_repo(...)` |
| Status | `await client.status(...)` |
| Materialize | `await client.materialize(...)` |
| Snapshot | `await client.snapshot(...)` |
| Tag | `await client.tag(...)` |
| Pull head | `await client.pull_head(...)` |
| Pull snapshot | `await client.pull_snapshot(...)` |
| Push | `await client.push(...)` |

Operations that can produce progress SHOULD also expose an async generator:

| Operation | Event API |
| --- | --- |
| Initialize from sources | `client.init_from_sources_stream(...)` |
| Clone workspace | `client.clone_workspace_stream(...)` |
| Materialize | `client.materialize_stream(...)` |
| Pull snapshot | `client.pull_snapshot_stream(...)` |

The non-streaming method consumes the operation to completion and returns the
final response envelope. The streaming method yields protocol records as they
arrive and then yields or exposes the final result according to the final API
decision.

## Protocol Models

Python request, response, event, and result types should mirror the taut schema
owned by `gwz-core/protocol/gwz.taut.py`.

Preferred model approach:

- Generate Python dataclasses or typed model classes from the taut schema when
  code generation is available.
- Preserve wire names and enum values exactly.
- Keep JSON serialization deterministic.
- Allow construction from dictionaries for compatibility with protocol message
  payloads.
- Avoid a second hand-authored behavioral model.

The Python package may add ergonomic wrappers, but those wrappers must lower to
protocol requests before crossing the Python/Rust bridge.

## Message Bridge

The client depends on a small async bridge interface:

```python
class CoreBridge:
    async def run(self, request: RequestEnvelope) -> ResponseEnvelope: ...

    async def stream(
        self,
        request: RequestEnvelope,
    ) -> AsyncIterator[ProtocolRecord]: ...
```

This bridge is not a command runner. It is a protocol message boundary between
`gws_core` and `gwz-core`.

The concrete implementation should be an in-process native extension, for
example `gws_core._bridge`, built from a small Rust binding crate that depends
on `gwz-core`.

Responsibilities:

- Accept serialized protocol request messages from Python.
- Dispatch messages through the same `gwz-core` operation semantics used by all
  drivers.
- Return serialized protocol response envelopes.
- Stream operation events as protocol records through an async queue.
- Return final operation results when available.
- Preserve request ids, operation ids, schema versions, attribution, member
  responses, errors, and aggregate status.
- Surface bridge failures separately from GWZ operation failures.
- Support cancellation by cancelling the bridge task and asking the Rust side to
  cancel the operation when the core supports that.

The Rust side may call synchronous `gwz-core` handlers internally. The bridge
must run blocking core work on a dedicated worker thread or extension-managed
blocking pool and deliver events back to Python through event-safe channels.
Python callers must never have to manage threads directly.

Conceptual Rust bridge API:

```text
submit(request_bytes) -> operation_handle + immediate_response_bytes
poll_events(operation_handle, after_sequence) -> event_record_bytes[]
wait(operation_handle) -> result_bytes
cancel(operation_handle) -> cancellation_ack
close(operation_handle)
```

The exact ABI can change, but the boundary must stay message-first. Avoid
exposing a large function-per-operation FFI surface because that would duplicate
the protocol API and make schema evolution harder.

## Concurrency And Cancellation

- Multiple Python operations MAY run concurrently when the caller schedules
  them concurrently.
- Per-member and per-workspace safety remains a `gwz-core` responsibility.
- The Python client MAY add an optional in-process per-root lock to avoid
  accidental concurrent mutation from the same process.
- Cancellation of an async operation MUST cancel the bridge task and attempt
  to cancel the underlying bridge operation.
- Cancellation does not imply rollback; callers must inspect the resulting
  workspace state with `await client.status(...)`.

## Errors

The Python package should expose a small exception hierarchy:

| Exception | Use |
| --- | --- |
| `GwsError` | Base class for package errors. |
| `GwsProtocolError` | Invalid or unsupported protocol records. |
| `GwsOperationError` | A GWZ response has rejected, failed, or partial status. |
| `GwsBridgeError` | The Python/Rust bridge failed outside normal GWZ operation handling. |
| `GwsCoreLoadError` | The native bridge or linked `gwz-core` implementation cannot be loaded. |

When possible, exceptions should preserve the original protocol response,
operation id, request id, member errors, bridge code, and diagnostic detail.

## Package Layout

```text
gwz-py/
  pyproject.toml
  src/
    gws_core/
      __init__.py
      client.py
      errors.py
      models.py
      bridge.py
      _bridge.pyi
      runtime.py
      py.typed
  tests/
    unit/
    integration/
  dev-docs/
    GwsPyDesign.md
```

Recommended package metadata:

- Distribution: `gws-core`
- Import package: `gws_core`
- Python: `>=3.11`
- Runtime dependencies: keep minimal; the Rust bridge is packaged with the
  wheel.
- Development dependencies: `pytest`, `pytest-asyncio`, type checker, formatter
- Build tooling: likely `maturin` or equivalent for the Rust extension

## GitHub Actions Release Build

`gwz-py` MUST be built and published by GitHub Actions. Local developer builds
are useful for iteration, but release wheels and CLI assets must come from CI
so the native bridge and bundled `gwz` tool are reproducible across supported
platforms.

The `gwz-py` release workflow depends on released GWZ components:

- A matching `gwz-core` release must be available before building `gws-core`
  wheels. The Python bridge must build against the released core source,
  crate, or source archive, not an arbitrary local checkout.
- A matching `gwz-cli` release source, crate, or source archive must be
  available before building the bundled `gwz` convenience binary.
- The `gwz-py` workflow builds the Rust `gwz` binary and publishes it as part of
  the `gwz-py` release artifacts. It should not depend on a separately
  published `gwz` binary.
- The workflow must fail early if the required `gwz-core` or `gwz-cli` release
  inputs are missing.

Version coupling should be explicit. Prefer a release manifest or workflow
inputs such as:

```text
GWS_CORE_VERSION=0.1.0
GWZ_CORE_VERSION=0.1.0
GWZ_CLI_VERSION=0.1.0
```

If all three repositories share one tag, the workflow may derive these from the
tag name. If they can drift independently, the release manifest is mandatory.

Minimum required release targets:

| Platform | CPU | Rust target | Python wheel platform | Bundled `gwz` asset |
| --- | --- | --- | --- | --- |
| Windows | amd64 | `x86_64-pc-windows-msvc` | `win_amd64` | `.zip` |
| macOS | arm64 | `aarch64-apple-darwin` | `macosx_*_arm64` | `.tar.gz` |
| Linux | amd64 | `x86_64-unknown-linux-gnu` | `manylinux_*_x86_64` | `.tar.gz` |
| Linux | arm64 | `aarch64-unknown-linux-gnu` | `manylinux_*_aarch64` | `.tar.gz` |

Recommended additional targets:

- macOS amd64: `x86_64-apple-darwin`
- Windows arm64: `aarch64-pc-windows-msvc`, once the Rust and Python packaging
  path is proven

Release workflow outline:

1. Resolve and validate the `gwz-core` and `gwz-cli` versions.
2. Download the released `gwz-core` source/crate artifact.
3. Download the released `gwz-cli` source/crate artifact.
4. Build `gws-core` wheels with the Rust bridge linked against that released
   core.
5. Build the Rust `gwz` CLI binary for the current CI platform.
6. Bundle the `gwz` binary into the platform wheel and/or attach it as a
   sibling release asset.
7. Run Python unit tests, bridge integration tests, and CLI parity smoke tests
   against the bundled `gwz` binary.
8. Repair/audit wheels where required by platform policy.
9. Upload wheels, source distribution, and standalone `gwz` installer assets as
   release artifacts.
10. Publish to the package index only after all required platforms pass.

The bundled `gwz` binary is a user-facing convenience tool shipped by
`gwz-py`. It is not the backend for `gws-core`; Python API calls still use the
message bridge into `gwz-core`.

## `gwz` CLI Packaging

`gwz-py` should publish the user-facing Rust CLI as `gwz`, not `gwz-cli`.

The CLI implementation comes from `gwz-cli`, but the release vehicle is
`gwz-py`. The Python package and the CLI should both use `gwz-core` protocol
semantics, but neither should call the other.

The install experience should mirror `uv`:

Reference behavior: `uv` documents standalone installers for macOS/Linux,
Windows, and versioned installer URLs in
<https://docs.astral.sh/uv/getting-started/installation/>.

```text
# macOS and Linux
curl -LsSf <install-base>/gwz/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm <install-base>/gwz/install.ps1 | iex"
```

Versioned installs should also be supported:

```text
curl -LsSf <install-base>/gwz/0.1.0/install.sh | sh
powershell -ExecutionPolicy ByPass -c "irm <install-base>/gwz/0.1.0/install.ps1 | iex"
```

Installer requirements:

- Download a platform-specific release asset from GitHub Releases or the
  project CDN.
- Verify checksums before installing.
- Install the `gwz` executable into a user-local bin directory by default.
- Support an override such as `GWZ_INSTALL_DIR`.
- Avoid requiring Rust, Python, or Cargo on the target machine.
- Be idempotent and safe to rerun.
- Print clear PATH guidance when the install directory is not on `PATH`.
- Support uninstall or document manual removal.

Release assets should be named around the executable package, for example:

```text
gws_core-<version>-<python>-<platform>.whl
gwz-<version>-x86_64-unknown-linux-gnu.tar.gz
gwz-<version>-aarch64-unknown-linux-gnu.tar.gz
gwz-<version>-aarch64-apple-darwin.tar.gz
gwz-<version>-x86_64-apple-darwin.tar.gz
gwz-<version>-x86_64-pc-windows-msvc.zip
SHA256SUMS
```

The Python API should not require the CLI internally. Users who install
`gws-core` receive Python access to `gwz-core`; users who want terminal
workflows can use the bundled or standalone `gwz` tool from the same
`gwz-py` release.

## Testing Strategy

- Unit-test request construction, response parsing, and error mapping with fake
  bridges.
- Unit-test that every public workspace operation is async.
- Integration-test the native message bridge against `gwz-core`.
- Contract-test protocol parsing against `gwz-core` protocol fixtures.
- Release-test `gwz-py` wheels against the released `gwz-core` artifact.
- Smoke-test the bundled `gwz` binaries against the Python package for
  command/API parity, without making the CLI the Python API backend.
- Exercise cancellation with long-running or fixture-controlled operations.
- Test installer scripts on Linux, macOS, and Windows CI before publishing a
  tagged release.

## Initial Milestones

1. Scaffold `gws-core` Python package with async client, errors, bridge
   interface, and fake bridge tests.
2. Add protocol-shaped Python models and message encoding/decoding.
3. Implement the native message bridge for `status`, `create_workspace`, and
   `init_from_sources`.
4. Add streaming support for operations that emit protocol event records.
5. Cover all current `gwz-core` workspace operations.
6. Build and bundle the Rust `gwz` CLI as part of the `gwz-py` release.
7. Add the GitHub Actions release workflow for `gwz-py` wheels and bundled
   `gwz` assets.
8. Harden wheel builds for Windows amd64, macOS arm64, Linux amd64, and Linux
   arm64.

## Open Decisions

- Finalize whether the Python public name is `gws-core` / `gws_core` or should
  align with `gwz-core`.
- Choose the canonical installer base URL for `gwz`.
- Decide whether all GWZ repos share one release tag or whether `gwz-py` reads
  an explicit release manifest with separate `gwz-core` and `gwz-cli` versions.
- Decide whether platform wheels should install `gwz` directly as a script,
  include it as package data behind a small Python launcher, or publish it only
  as a sibling release asset plus standalone installer.
- Decide whether streaming methods should yield a final result record or expose
  it through an operation handle.
- Decide whether `gws-core` should expose JSON, MessagePack, or another wire
  encoding across the native bridge.
- Decide how operation cancellation is represented in the protocol before
  `gwz-core` has full cooperative cancellation support.
