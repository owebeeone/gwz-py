# GWZ Python Implementation Plan

Status: planning draft for implementation

Companion design: `dev-docs/GwzPyDesign.md`

## Planning Principles

- Optimize for parallel implementation by assigning disjoint write scopes to
  agents in each phase.
- Keep individual steps small. The target is no more than about 500 lines of
  edited production code per step; split any step that grows past that.
- Generated protocol files have one owner at a time. No other agent edits
  `src/gwz/protocol/generated/*`.
- Python public APIs stay thin over generated taut request/response types.
- Shared helpers are preferred over duplicated command-specific logic, especially
  for request metadata, response handling, rendering, and CLI selection flags.
- The Python API never shells out to the Rust `gwz` CLI.
- `clone` and `forall` are CLI workflows, not ordinary generated `GwzCore`
  service methods.
- The native bridge ABI is CBOR by default. Any JSON bridge used during
  development must use taut's IR-driven JSON codec, never ad hoc `json.dumps`.
- Every phase ends with `python run_tests.py`; native phases also run the relevant
  Rust or maturin checks added by that phase.

## Current Baseline

The scaffold already contains:

- `pyproject.toml`, `run_tests.py`, package metadata, and console script entry.
- Generated taut Python API and packaged `gwz.ir.json`.
- `gwz.Client` with async request builders for the current service surface.
- A message-first `CoreBridge` protocol with `call`, `subscribe_events`, and
  `operation_result`.
- A minimal Python CLI covering early status/list/init/materialize commands.
- Unit tests for protocol regeneration, service boundary, request construction,
  and basic CLI parsing.

The baseline is not complete:

- The native `gwz._gwz_core` extension does not exist.
- Protocol wire encode/decode is only partial.
- Streaming is scaffolded but not backed by a native operation runtime.
- CLI parity with Rust `gwz-cli` is incomplete.
- Packaging has not chosen Python CLI, Rust binary dispatch, or both.
- Generated transport stubs are excluded from the runtime package with
  `tautc --api-only`; the handwritten `CoreBridge` is the only Python transport
  boundary.

## Agent Roles

These roles can run concurrently whenever their write scopes do not overlap.

| Role | Primary Write Scope | Purpose |
| --- | --- | --- |
| Coordinator | docs, task integration only | Own sequencing, review merge results, prevent overlap. |
| Protocol Agent | `scripts/regen_protocol.py`, `src/gwz/protocol/*`, generated files | Keep taut API current and implement encode/decode helpers. |
| Bridge Agent | native extension files, `src/gwz/bridge.py` | Build PyO3/maturin bridge and async Python bridge surface. |
| Client Agent | `src/gwz/client.py`, future client helper modules, client tests | Keep Python facade complete, thin, and ergonomic. |
| CLI Agent | `src/gwz/cli.py` and later CLI submodules | Implement Python CLI parity and CLI-only workflows. |
| Test Agent | explicitly assigned test files, fixtures | Build fake/native/parity tests without owning production code. |
| Packaging Agent | `pyproject.toml`, CI, README packaging sections | Own build metadata, wheel strategy, and release checks. |
| Docs Agent | `README.md`, `dev-docs/*` | Keep design, plan, and user docs aligned. |

## Parallelization Rules

- Start each phase by assigning agents to explicit files or modules.
- If a generated file changes, all other agents rebase or refresh before editing
  handwritten code that imports generated symbols.
- Prefer adding narrow modules over inflating `client.py` or `cli.py` once either
  file approaches readability limits.
- Test ownership must be explicit. Either one Test Agent owns a shared fixture or
  each production agent owns a unique test file such as `test_native_read.py`.
  Two agents should not edit the same test file in the same phase.
- Coordinator integrates only after each worker reports changed files and test
  status.

## Phase 0: Baseline Lock

Goal: make the current scaffold a reliable starting line for parallel work.

Parallel agents:

- Protocol Agent
- Client Agent
- Test Agent
- Docs Agent

Steps:

1. Protocol Agent verifies generated files match
   `../gwz-core/protocol/gwz.taut.py`.
   Write scope: `src/gwz/protocol/generated/*`.
   Output: no diff or regenerated protocol commit.
   Verification: `python scripts/regen_protocol.py --check`.

2. Protocol Agent resolves generated transport artifacts.
   Write scope: `scripts/regen_protocol.py`, `src/gwz/protocol/generated/*`,
   `src/tests/test_protocol.py`.
   Completed path:
   - Regeneration uses `tautc --api-only`.
   - Runtime generated artifacts are `api.py`, `__init__.py`, and `gwz.ir.json`.
   - Generated `client.py`/`server.py` are not packaged beside `CoreBridge`.
   Verification: protocol tests assert the chosen generated artifact set.

3. Client Agent verifies the facade covers all current `GwzCore` service methods:
   `create_workspace`, `init_from_sources`, `add_existing_repo`, `create_repo`,
   `repo_sync`, `materialize`, `status`, `ls`, `snapshot`, `tag`, `capture`,
   `commit`, `stage`, `pull_head`, `pull_snapshot`, `push`, `stash`, `branch`,
   `events.subscribe`, and `operation.result`.
   Write scope: `src/gwz/client.py`, future client helper modules,
   `src/tests/test_client.py`.
   Work:
   - Keep wrappers thin over generated request/response types.
   - Let core own branch-create defaults such as `start_ref="HEAD"` unless the
     plan explicitly documents a Python-side reason to duplicate them.
   - Split client helper modules before adding more behavior if `client.py`
     remains near or above the 500-line reviewability guideline.
   Output: missing thin wrappers only.
   Verification: client unit tests.

4. Test Agent adds or confirms tests for the design-review edge cases:
   `repo_sync` selection paths, branch merge `start_ref`, snapshot branch source,
   materialize branch switch, event subscription by operation id, and `forall`
   absence from the service.
   Write scope: `src/tests/test_client.py`, `src/tests/test_protocol.py`.
   Output: boundary tests.
   Verification: `python run_tests.py`.

5. Docs Agent keeps `GwzPyDesign.md` and this plan in sync.
   Write scope: `dev-docs/*`, `README.md`.
   Output: no stale GWS names and no stale generic stream ABI.
   Verification: `rg "Gws|gws-core|event_message|def stream" dev-docs src/gwz -g '!GwzPyPlan.md'`.

Phase gate:

- `python run_tests.py` passes.
- `git status` contains only expected files.
- Design and plan agree on service vs CLI-local boundaries.

## Phase 1: Protocol Codec And Transport Shape

Goal: make Python able to encode and decode generated taut dataclasses through a
single transport abstraction before native Rust implementation lands.

Parallel agents:

- Protocol Agent
- Bridge Agent
- Client Agent
- Test Agent

Steps:

1. Protocol Agent implements the generated-symbol and IR registry.
   Write scope: `src/gwz/protocol/codec.py`.
   Work:
   - Build a name-to-generated-class registry from `gwz.protocol.generated`.
   - Expose schema/message lookup helpers for request, response, event, and result
     messages.
   - Expose one protocol metadata helper for schema version and an IR fingerprint
     that can later be compared with the linked native extension.
   - If no canonical schema-version literal exists in `gwz-core` or the schema,
     record that as a core coordination item instead of inventing a Python value.
   Verification: registry tests cover service request/response names and
   `OperationEvent`/`OperationResult`.

2. Protocol Agent implements full IR-driven `from_wire`.
   Write scope: `src/gwz/protocol/codec.py`, `src/tests/test_codec.py`.
   Work:
   - Preserve existing `to_wire`.
   - Add `from_wire(message_name, payload)` as a recursive IR-driven dataclass
     builder.
   - Walk message fields from the loaded IR, including nested `Msg`, `Enum`,
     `List`, and optional fields.
   - Instantiate generated `slots=True` dataclasses by keyword because optional
     fields still require constructor values.
   - Do not treat decode as a reflection fallback; it is a first-class codec path.
   Verification: full round trip
   `dataclass -> to_wire -> encode -> decode -> from_wire -> equality`.

3. Protocol Agent commits the byte ABI and helpers.
   Write scope: `src/gwz/protocol/codec.py`, `src/tests/test_codec.py`.
   Work:
   - Implement CBOR byte encode/decode using `taut.wire.codec` and packaged
     `gwz.ir.json`.
   - Treat JSON only as a debug/test option, and only via taut's IR-driven
     `jsoncodec` rules.
   - Keep the public helper names transport-neutral.
   Verification: encoded bytes round-trip generated messages.

4. Bridge Agent defines the Python native-call boundary.
   Write scope: `src/gwz/bridge.py`.
   Work:
   - Keep `CoreBridge.call(method, request_message, response_message, request)`.
   - Convert request dataclasses to CBOR bytes before native calls.
   - Decode native response CBOR bytes back into generated dataclasses.
   - Preserve fake bridge compatibility for tests.
   - Add a fake native object so tests exercise the real byte codec without
     requiring Rust.
   Verification: fake native object tests without Rust extension.

5. Bridge Agent defines event/result transport helpers.
   Write scope: `src/gwz/bridge.py`.
   Work:
   - `subscribe_events(operation_id)` decodes CBOR `OperationEvent` records.
   - `operation_result(operation_id)` decodes a CBOR `OperationResult`.
   - Do not reintroduce method/request streaming.
   Verification: fake native event queue tests.

6. Test Agent expands protocol fixture coverage.
   Write scope: `src/tests/test_protocol_boundary.py`,
   `src/tests/test_bridge_transport.py`.
   Work:
   - Cover nested dataclasses, enums, optional values, and lists.
   - Cover generated classes added by branch/stash.
   - Assert unsupported message names fail with `GwzProtocolError`.
   Verification: `python run_tests.py`.

7. Client Agent fills the Python error-model gaps.
   Write scope: `src/gwz/errors.py`, `src/gwz/client.py`,
   `src/tests/test_errors.py`.
   Work:
   - Keep the existing exception hierarchy, but add `member_errors` to
     `GwzOperationError` and populate it from
     `ResponseEnvelope.errors` or `OperationResult.errors`.
   - Preserve request id, operation id, aggregate status, member errors, and the
     original protocol response on `GwzOperationError`.
   - Map decode/unsupported-message failures to `GwzProtocolError`.
   - Map native import, transport, and extension failures to `GwzCoreLoadError` or
     `GwzBridgeError`.
   - Avoid public namespace collisions between exception `GwzError` and generated
     data `GwzError`; either namespace the generated data or expose a distinct
     alias such as `GwzErrorDetail`.
   - Keep high-level methods raising by default for partial, dirty, conflicted,
     failed, and rejected aggregate statuses, but guarantee the exception carries
     the typed response.
   - Cover rejected, failed, partial, dirty, conflicted, and protocol-decode
     cases with generated or fake responses.
   Verification: error tests plus `python run_tests.py`.

Phase gate:

- Python bridge can be tested with fake encoded bytes.
- No native Rust code is required for this phase to pass.
- Codec helpers are centralized; no operation-specific encoding exists.
- CBOR is the committed native bridge ABI before Phase 2 begins.
- Phase 0 or Phase 1 has either established one core/schema-owned schema-version
  literal or recorded the mismatch as a `gwz-core` coordination blocker; the
  packaged IR fingerprint remains the enforceable drift guard.

## Phase 2: Native Extension Skeleton

Goal: create `gwz._gwz_core` with one real end-to-end call before expanding the
service surface.

Parallel agents:

- Bridge Agent
- Packaging Agent
- Test Agent

Steps:

1. Packaging Agent commits the native build backend and dependency model.
   Write scope: `pyproject.toml`, native `Cargo.toml` files, README install notes.
   Completed path:
   - The committed backend is maturin-centered.
   - `Cargo.toml` declares `gwz-core` as a sibling development path dependency.
   - Rust floor follows `gwz-core`: edition 2024 and Rust 1.95 or newer.
   - README records local `maturin develop` and git2/OpenSSL/SSH build notes.
   - Phase 6 may refine wheel/platform strategy but must not reopen the backend.
   Verification: package metadata validation.

2. Bridge Agent creates the PyO3 module skeleton.
   Write scope: native extension directory only.
   Completed path:
   - Exports module as `gwz._gwz_core`.
   - Implements `health()` and `version()` smoke functions.
   - Native `call` detaches from the GIL around blocking Rust dispatch.
   Verification: import smoke test.

3. Bridge Agent implements `call` for `ls` only.
   Write scope: native extension dispatch module.
   Completed path:
   - Accepts method name, request message, response message, and CBOR request
     bytes.
   - Decodes `LsRequest` through the `gwz-core` generated CBOR/runtime support.
   - Calls `gwz-core` `handle_ls`.
   - Encodes `LsResponse` as CBOR bytes.
   - Returns unsupported method/message and protocol decode failures as Python
     exceptions that the bridge maps to `GwzBridgeError`/`GwzProtocolError`.
   Verification: native fixture test for `ls`.

4. Test Agent adds native opt-in tests.
   Write scope: `src/tests/test_native_bridge.py`, test fixtures.
   Completed path:
   - Skips cleanly when extension is not built.
   - Exercises import and `ls` against a minimal workspace fixture.
   - Keeps fake bridge tests as default fast coverage.
   Verification: default `python run_tests.py` and opt-in native test command.

5. Packaging Agent adds build instructions.
   Write scope: `README.md`, `dev-docs/GwzPyPlan.md` if needed.
   Completed path:
   - Local editable Python install.
   - Local native extension build.
   - Troubleshooting for missing `gwz._gwz_core`.
   Verification: command snippets reviewed by Bridge Agent.

Phase gate:

- `gwz._gwz_core` imports when built.
- `Client.ls()` can call real `gwz-core` through the native bridge.
- Tests still pass when the extension is absent.
- The native backend choice is committed.
- The native extension links the same `gwz-core` schema/version source that
  produced packaged `gwz.ir.json`, or an explicit drift check fails the build.

## Phase 3: Native Service Coverage

Goal: expand native dispatch across the generated `GwzCore` service without
duplicating per-operation FFI signatures.

Parallel agents:

- Bridge Integration Agent: central registry, codec glue, error mapping
- Bridge Agent A: read/workspace operation module
- Bridge Agent B: materialization/capture operation module
- Bridge Agent C: Git mutation operation module
- Bridge Agent D: branch/stash operation module
- Test Agent

Steps:

1. Bridge Integration Agent creates the native dispatch layout.
   Write scope: central native registry plus shared native error/codec files only.
   Work:
   - Author the Rust method-name dispatch table for the native extension. This
     mirrors the behavior of `gwz-cli`'s private dispatch, because `gwz-core`
     exposes individual handlers rather than a public reusable router.
   - Open a follow-up option for moving shared dispatch into `gwz-core` if keeping
     parallel `gwz-cli` and `gwz-py` method matches becomes too costly.
   - Create per-family dispatch modules under the native module root chosen in
     Phase 2, for example `dispatch/read`, `dispatch/materialize`,
     `dispatch/git_mutation`, and `dispatch/branch_stash`.
   - Keep method-name routing in one central registry.
   - Keep encode/decode and error mapping in shared helpers.
   - Own the three handler-shape shims:
     no-backend handlers, backend handlers, and backend-plus-`EventSink` handlers.
   - Construct shared backend/event-sink plumbing once; family modules should not
     duplicate `Git2Backend` construction or event-sink wiring.
   - Leave operation-family modules empty or stubbed for other agents.
   Verification: central registry compiles with stubs.

2. Bridge Agent A implements read and workspace setup calls.
   Write scope: native `dispatch/read` module,
   `src/tests/test_native_read.py`.
   Methods: `create_workspace`, `init_from_sources`, `add_existing_repo`,
   `create_repo`, `repo_sync`, `status`, `ls`.
   Verification: fixture tests for each method with dry-run where useful.

3. Bridge Agent B implements materialization and capture calls.
   Write scope: native `dispatch/materialize` module,
   `src/tests/test_native_materialize.py`.
   Methods: `materialize`, `snapshot`, `tag`, `capture`.
   Required edge cases:
   - `MaterializeTargetKind.branch`.
   - `SnapshotSourceKind.current`.
   - `SnapshotSourceKind.branch`.
   Verification: fixture tests for lock/snapshot/tag/branch targets.

4. Bridge Agent C implements Git mutation calls.
   Write scope: native `dispatch/git_mutation` module,
   `src/tests/test_native_git_mutation.py`.
   Methods: `commit`, `stage`, `pull_head`, `pull_snapshot`, `push`.
   Work:
   - Preserve attribution and policy fields.
   - Prefer dry-run or local fixture remotes for tests.
   Verification: mutation tests isolated in temporary repos.

5. Bridge Agent D implements branch and stash calls.
   Write scope: native `dispatch/branch_stash` module,
   `src/tests/test_native_branch_stash.py`.
   Methods: `branch`, `stash`.
   Required edge cases:
   - `BranchOp.merge` source is `start_ref`.
   - Branch create default start point is owned by `gwz-core`; Python passes no
     `start_ref` unless the caller supplies one.
   - Stash push/list/apply/pop/drop preserve generated response shape.
   Verification: temporary multi-member workspace tests.

6. Test Agent builds a shared native fixture harness.
   Write scope: `src/tests/fixtures/*`, `src/tests/conftest.py`.
   Work:
   - Workspace factory.
   - Member repo factory.
   - Optional local bare remote factory.
   - Test skip markers for native extension availability.
   Verification: all per-family native test files can use the shared fixtures
   without duplicating workspace setup.

Phase gate:

- Every generated `GwzCore` service method has native dispatch or an explicit
  skipped test with a tracked reason.
- Dispatch remains message-based; no operation-specific Python FFI methods.
- Only the Bridge Integration Agent edits central registry, codec glue, or native
  error mapping.
- Handler-shape shims are centralized; family agents only wire method names and
  typed request/response calls through those shims.
- Shared fixture helpers avoid duplicated repo setup.

## Phase 4: Operation Events And Results

Goal: make progress streaming and final operation result lookup work through
`operation_id`.

Parallel agents:

- Bridge Agent
- Client Agent
- Test Agent

Steps:

1. Bridge Agent integrates `gwz-core` operation runtime.
   Write scope: native runtime wrapper files.
   Work:
   - Treat this as first-use runtime code, not a thin wrapper.
   - Implement or document the adapter between handlers that return typed
     `*Response` values and the `OperationRuntime`/`ExecutionReport` result model.
   - Acceptable paths are: adapt each typed response envelope into the runtime's
     final report shape, or implement a bridge-owned `EventSink` plus
     `operation_id` result store that preserves the generated response details.
   - Submit event-capable long-running operations through the chosen runtime path:
     `init_from_sources`, `materialize`, `pull_head`, `pull_snapshot`, and `push`.
   - Return accepted responses with `ResponseMeta.operation_id`.
   - Preserve direct responses for operations that remain synchronous.
   Verification: native tests assert operation id, event replay, and final result
   for at least one handler using each supported runtime shape.

2. Bridge Agent implements native event subscription.
   Write scope: native runtime wrapper files, `src/gwz/bridge.py`.
   Work:
   - Expose `subscribe_events(operation_id)`.
   - Drain `OperationEvent` records in sequence order.
   - Surface not-found as `GwzOperationError` or `GwzBridgeError` consistently.
   Verification: fake and native event tests.

3. Bridge Agent implements native result lookup.
   Write scope: native runtime wrapper files, `src/gwz/bridge.py`.
   Work:
   - Expose `operation_result(operation_id)`.
   - Return generated `OperationResult`.
   - Preserve request id, aggregate status, members, and errors.
   Verification: operation result tests.

4. Client Agent adds stream helpers.
   Write scope: `src/gwz/client.py` and the Phase 0 client helper module, if
   created.
   Work:
   - Add or complete exactly these member-event helpers:
     `init_from_sources_stream`, `materialize_stream`, `pull_head_stream`,
     `pull_snapshot_stream`, and `push_stream`.
   - Do not add branch/stash stream helpers until `gwz-core` handlers accept an
     `EventSink`; they remain request/response methods.
   - Keep stream helpers implemented through one shared private helper.
   - If Phase 0 split client helpers, put stream mechanics in the helper module
     and leave `client.py` as the facade.
   - Drain events before reading `operation.result`; if the final aggregate
     status is rejected, failed, partial, dirty, or conflicted, raise with the
     typed result attached.
   Verification: fake bridge tests for all stream helpers.

5. Test Agent adds concurrency and cancellation coverage.
   Write scope: `src/tests/test_operation_events.py`,
   `src/tests/test_operation_results.py`.
   Work:
   - Multiple subscribers or sequential drains if runtime supports it.
   - Operation not found.
   - Event order and final result consistency.
   Verification: native event test suite.

Phase gate:

- No generic `stream(method, request, event)` ABI exists.
- Stream helpers use `operation_id`.
- The stream-helper set matches the core handlers that accept an `EventSink`.
- Operation result lookup works independently of event subscription.

## Phase 5: Python CLI Parity

Goal: make the installed `gwz` command a useful Python implementation of the Rust
CLI surface while keeping the final packaging choice open.

Parallel agents:

- CLI Agent A: parser/shared options and command registration
- CLI Agent B: read/workspace command module
- CLI Agent C: mutation command module
- CLI Agent D: branch/stash command module
- CLI Agent E: CLI-only workflow module
- Test Agent

Steps:

1. CLI Agent A extracts shared CLI infrastructure before other CLI agents start.
   Write scope: `src/gwz/cli.py`, `src/gwz/cli_shared.py`,
   `src/gwz/cli_render.py`, `src/tests/test_cli_parser.py`.
   Work:
   - Invent the command registration structure; the current CLI is a small
     `if`/`elif` monolith, not an existing registry to refactor.
   - Migrate existing status, ls, init, and materialize behavior into the new
     shared parser/registration layer before command-family agents start.
   - Global `--root`, selection flags, dry-run, policy flags, JSON mode.
   - Shared response rendering interface.
   - Shared error to exit-code mapping.
   - Command registration helpers that command-family modules can call without
     editing parser internals.
   Verification: parser tests for global options.

2. CLI Agent B implements read/workspace commands.
   Write scope: `src/gwz/cli_read.py`, `src/tests/test_cli_read.py`.
   Commands: `status`, `ls`, `init`, `repo add`, `repo create`, `repo sync`.
   Work:
   - Register commands through CLI Agent A helpers.
   - Reuse shared rendering and exit-code logic.
   Verification: fake bridge CLI smoke tests.

3. CLI Agent C implements mutation commands.
   Write scope: `src/gwz/cli_mutation.py`, `src/tests/test_cli_mutation.py`.
   Commands: `materialize`, `snapshot`, `tag`, `capture`, `stage`, `commit`,
   `pull`, `push`.
   Required edge cases:
   - `materialize --switch`.
   - `snapshot --branch[=<name>]`.
   Verification: parser and fake bridge request tests.

4. CLI Agent D implements `branch` and `stash`.
   Write scope: `src/gwz/cli_branch_stash.py`,
   `src/tests/test_cli_branch_stash.py`.
   Work:
   - `branch --list/--create/--delete/--merge`.
   - `branch --create --from --switch`.
   - `stash push/list/apply/pop/drop`.
   Verification: request construction tests and JSON rendering tests.

5. CLI Agent E implements `forall`.
   Write scope: `src/gwz/cli_local.py`, `src/tests/test_cli_local.py`.
   Work:
   - Use `client.ls()` to resolve members.
   - Use generated `ExecRequest`/`ExecResponse` as local data only.
   - Execute child processes in Python.
   - Respect partial behavior and output banners.
   Verification: temp command tests with fake members.

6. CLI Agent E implements or defers `clone`.
   Write scope: `src/gwz/cli_local.py`, `src/tests/test_cli_local.py`,
   release decision notes if deferred.
   Options:
   - Named non-service native bridge extension.
   - Python-local Git clone followed by `materialize("lock")`.
   - Rust binary dispatch in packaging mode that includes the binary.
   Output: one chosen implementation path or a Phase 7 known limitation that
   explicitly blocks declaring full Rust CLI parity.
   Verification: clone workflow test or explicit skipped test.

7. Test Agent creates CLI parity fixtures.
   Write scope: `src/tests/fixtures/cli_parity/*`,
   `src/tests/test_cli_parity.py`.
   Work:
   - Compare parser behavior against Rust CLI for representative commands.
   - Compare JSON shape where Python/Rust output should match.
   - Keep human output tests focused to avoid brittle formatting lock-in.
   Verification: `python run_tests.py` and optional Rust CLI parity command.

Phase gate:

- Python CLI can cover the current Rust CLI command surface or has explicit
  documented exceptions.
- Request construction reuses `Client`; no command shells out to Rust CLI except
  an intentional final packaging dispatch path.
- After CLI Agent A lands, command-family agents do not edit parser internals,
  shared rendering, or shared exit-code mapping.
- Shared CLI helpers prevent repeated global option and response handling logic.

## Phase 6: Packaging, CI, And Release Mode

Goal: make the package publishable and choose the release behavior of the
installed `gwz` command.

Parallel agents before the release-mode decision:

- Packaging Agent
- Bridge Agent
- CLI Agent
- Test Agent

Post-decision integration agent:

- Docs Agent

Steps:

1. Packaging Agent finalizes wheel/platform strategy on the Phase 2 backend.
   Write scope: `pyproject.toml`, native build files.
   Work:
   - Do not reopen the build-backend decision made in Phase 2.
   - Ensure package data includes generated IR JSON and `py.typed`.
   - Keep source distribution install behavior explicit.
   - Configure release builds to use a released `gwz-core` source, such as a
     git tag, instead of a sibling checkout.
   - Add a release-ordering note mirroring the sibling repositories: release or
     tag `gwz-core` before building `gwz-py` wheels against it.
   Verification: local sdist/wheel build.

2. Packaging Agent adds CI.
   Write scope: `.github/workflows/*` if this repo owns CI.
   Work:
   - Protocol regen check.
   - Packaged `gwz.ir.json` matches the `gwz-core` tag linked into the extension.
   - Python unit tests.
   - Native build/test matrix.
   - Packaging smoke test.
   Verification: local workflow command where available or documented dry run.

3. CLI Agent and Packaging Agent decide `gwz` command dispatch.
   Write scope: `src/gwz/cli.py`, packaging metadata, README.
   Options:
   - Python CLI only.
   - Rust binary dispatch when bundled.
   - Environment-controlled selection with Python fallback.
   Criteria:
   - CLI parity results.
   - Wheel complexity.
   - Startup cost.
   - Maintenance burden.
   Verification: console script smoke tests.

4. Bridge Agent prepares wheel artifacts.
   Write scope: native build metadata.
   Work:
   - Platform tags.
   - Rust dependency pinning.
   - Audit packaged extension contents.
   Verification: wheel install in clean virtualenv.

5. Docs Agent updates README and release notes.
   Write scope: `README.md`, `dev-docs/*`.
   Work:
   - Install instructions.
   - API examples.
   - CLI examples.
   - Native bridge troubleshooting.
   Verification: documentation command snippets are current. Run this after the
   CLI dispatch decision is settled so docs do not race packaging edits.

Phase gate:

- Fresh virtualenv can install the built wheel and import `gwz`.
- Console script works.
- The wheel build fails if packaged IR and linked `gwz-core` protocol fingerprint
  disagree.
- CI covers protocol, Python tests, native build, and packaging smoke tests.

## Phase 7: Hardening And Release Readiness

Goal: reduce risk before first PyPI release.

Parallel agents:

- Test Agent
- Docs Agent
- Packaging Agent
- Coordinator

Steps:

1. Test Agent runs failure-mode audits.
   Write scope: tests only.
   Work:
   - Missing native extension.
   - Unsupported method.
   - Protocol decode failure.
   - Dirty/conflicted member responses.
   - Operation not found.
   Verification: focused tests.

2. Test Agent runs multi-platform smoke checks.
   Write scope: test docs or CI matrix.
   Work:
   - macOS/Linux at minimum.
   - Python 3.10 through current supported version.
   - Optional Windows notes if not supported initially.
   Verification: CI or documented manual results.

3. Docs Agent validates public API examples.
   Write scope: README and docs.
   Work:
   - Keep examples executable where possible.
   - Confirm async usage is correct.
   - Explain `clone`/`forall` CLI-only boundaries.
   Verification: snippets reviewed against code.

4. Coordinator performs release checklist.
   Write scope: release checklist doc if needed.
   Work:
   - Versioning decision.
   - License metadata.
   - PyPI package name decision.
   - Wheel contents.
   - Known limitations.
   Verification: dry-run publish or equivalent.

Phase gate:

- Known limitations are explicit.
- First PyPI publish has a single release mode.
- User-facing API and CLI examples match implementation.

## Fastest Safe Execution Order

The fastest safe schedule is:

1. Run Phase 0 immediately with four agents because scopes are mostly disjoint.
2. Run Phase 1 Protocol Agent first for the generated registry, `from_wire`, and
   CBOR helpers; then Bridge Agent and Client Agent can proceed in parallel while
   Test Agent writes fake byte-transport and error tests.
3. Run Phase 2 Packaging Agent first far enough to commit the native backend and
   `gwz-core` dependency model; then Bridge Agent can build the extension skeleton
   while Test Agent prepares opt-in native tests.
4. Run Phase 3 by landing the Bridge Integration Agent's dispatch layout first,
   then run four Bridge Agents split by operation family plus one Test Agent
   building shared fixtures.
5. Run Phase 4 after the native call path exists; event work depends on runtime
   integration but client stream helpers can proceed against fake bridge tests.
6. Run Phase 5 by landing CLI Agent A's shared parser/rendering layer first, then
   run the four command-family CLI agents plus the parity Test Agent.
7. Run Phase 6 packaging and final CLI parity checks in parallel where scopes are
   disjoint, then let Docs update README/release notes after the dispatch mode is
   settled.
8. Run Phase 7 as release hardening.

## DRY And Maintainability Gates

Before accepting any phase:

- Repeated request metadata construction must live in `Client.meta` or one CLI
  helper.
- Repeated enum coercion must use one helper.
- Response error handling must use one path.
- CLI parser command modules may differ, but rendering and exit-code logic should
  be shared.
- Native dispatch may match on method names, but encode/decode and error mapping
  must be centralized.
- Native dispatch must centralize handler-shape shims and backend/event-sink
  plumbing; family modules should not duplicate those mechanics.
- Test workspace setup must use fixture factories, not per-test ad hoc shell
  scripts.

## Review Checklist

The plan reviewer should check:

- Every requirement in `GwzPyDesign.md` appears in at least one phase.
- Parallel agents have disjoint write scopes.
- Steps are small enough to keep reviewable, approximately under the 500 LOC
  guideline.
- The plan avoids duplicated protocol, CLI, or bridge logic.
- The plan keeps generated taut API as the source of truth.
- The plan does not treat `clone` or `forall` as generated `GwzCore` service
  methods.
- The plan names the committed bridge byte ABI and does not defer it past native
  extension work.
- The plan includes a full IR-driven decode path and protocol fingerprint drift
  checks.
- The event stream helpers match the `gwz-core` handlers that accept an
  `EventSink`.
- The native build backend and `gwz-core` release source are fixed before native
  dispatch work fans out.
- The plan can be executed quickly without making integration chaotic.
