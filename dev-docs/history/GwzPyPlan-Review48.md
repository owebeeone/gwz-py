# GwzPyPlan Review 48

Review targets:
- `dev-docs/GwzPyPlan.md` (the implementation plan)
- `dev-docs/GwzPyDesign.md` (the companion design)

Reviewer: Claude Code (Opus 4.8). Method: code-grounded review via four parallel
sub-reviewers (protocol accuracy, bridge/native architecture, codec/API/error
model, plan/process), each verifying claims against the actual `gwz-py` scaffold,
`gwz-core` (Rust + taut), and the `taut` runtime. Every finding cites `file:line`.

This **complements** `GwzPyPlan-Review55.md` ("Review 55"), which was a
process/parallelism pass that took the design's protocol claims at face value.
The value added here is grounding: I verified the protocol claims (they hold) and
then audited the parts Review55 could not — the bridge/event runtime, the codec
layering, and the packaging/release coupling. Overlaps with Review55 are marked
**(confirms R55)**.

## Verdict

**Approve with fixes — but the fixes are larger than Review55 found, and they are
in the *plan's scoping*, not the design.**

The design is genuinely strong: every load-bearing protocol claim verifies exactly
against the taut schema, the generated Python, *and* the Rust handlers, and the
scaffold already implements the async facade, the encode-side codec, and the DRY
helpers the plan relies on. The problem is that the plan treats several hard
pieces as small steps. Five P1 items — all about work the plan under-budgets —
should be folded into Phase 1–4 scoping before fan-out: the Rust-side per-method
dispatch, the heterogeneous handler shims, the first-use event runtime, the
entire decode (`from_wire`) path, and the unowned byte-encoding boundary. None
contradict the design; they make the plan executable.

## P1 findings (under-scoped work — fold into Phase 1–4 before fan-out)

### [P1] The bridge must hand-build a Rust-side per-method dispatch that gwz-core does not provide

Reference: Plan Phase 2 step 3 / Phase 3; Design "Bridge Contract".

"No function-per-operation FFI" is true on the **Python** side, but the plan lets
it imply the Rust side is router-free. It isn't. `gwz-core` exposes only
individual `handle_*` fns (`workspace_ops/mod.rs:21-37`, no router), and the one
method-name dispatch that exists — `gwz-cli/src/globalargs.rs:440-607` — is
`pub(crate)` inside the private `mod globalargs` of the gwz **binary**
(`gwz-cli/src/lib.rs:18`), so it is unusable by gwz-py. The native extension must
therefore re-author a per-method decode→dispatch→encode match. The message-first
goal doesn't eliminate per-method routing; it relocates it from Python into Rust.

Fix: Add a Phase 2/3 note that the native ext owns a Rust method-name dispatch
table mirroring `globalargs.rs`, scope the Bridge Integration Agent's step to
author it, and consider whether `gwz-core` should expose a reusable dispatch fn so
gwz-py and gwz-cli don't maintain two parallel matches.

### [P1] `handle_*` signatures are heterogeneous — the 4-agent Phase 3 split inherits per-handler glue **(confirms R55)**

Reference: Plan Phase 3 steps 2-5; DRY gate "encode/decode and error mapping must
be centralized".

The handlers do **not** share a signature: `handle_ls(start, request, op_id)` (no
backend, `handle_ls.rs:15`); `handle_create_workspace(request, op_id)` (no
backend/start, `handle_create_repo.rs:19`); `handle_status/snapshot/tag/branch/
stash/commit/stage/repo_sync(&backend, start, request, op_id)`; and
`handle_materialize/init_from_sources/pull_head/pull_snapshot/push(..., events:
&dyn EventSink)` (`handle_materialize.rs:112`, `push_member.rs:26`). So the
dispatch needs per-handler **call shims** (construct `Git2Backend`, thread the
`EventSink` only for the 5 event handlers, special-case the backend-less ones) —
real per-method code, not one centralizable helper, which sharpens Review55's
Phase-3 disjoint-scope concern.

Fix: Document the three handler shapes (no-backend / backend / backend+EventSink)
and assign the per-shape shims to the Integration Agent's central registry,
leaving family agents only method-name→shim wiring.

### [P1] The operation-event runtime is first-use code with a `*Response`-vs-`ExecutionReport` impedance mismatch

Reference: Plan Phase 4 step 1; Design "Bridge Contract" + Public API.

The good news: `OperationRuntime` (`operation/operation_runtime.rs` +
`push_event.rs`) provides `submit/subscribe/try_result/wait`, an
`operation_id`-keyed result store, a bounded event buffer, and `OperationNotFound`
— so the `operation_id` model is buildable on real APIs. But **no current caller
wires it**: `gwz-cli`'s `execute_invocation` passes a *synchronous* sink and
blocks inline (`globalargs.rs:428-439`); handlers emit through a `&dyn EventSink`
and return typed `*Response`, not the `ExecutionReport` that `submit()` expects
(`push_event.rs:211-251`, `handle_materialize.rs:178`). Phase 4 step 1 ("integrate
gwz-core operation runtime") allocates one terse step to first-use code with a
real adapter problem.

Fix: Expand Phase 4 step 1 to specify the runtime adapter — how a handler that
returns `*Response` reconciles with `submit()`'s `ExecutionReport`, or whether the
bridge instead implements a custom `EventSink` + its own `operation_id` result
store. Note this path is first-use and needs its own tests.

### [P1] The decode half of the codec is entirely unbuilt; `from_wire` is mis-budgeted as a "fallback helper"

Reference: Plan Phase 1 step 1; Design "Taut Protocol Use".

The whole layering rests on a dataclass↔wire round trip, but **only encode
exists**: `src/gwz/protocol/codec.py` (33 lines) has only `to_wire`, no
`from_wire`. `taut.wire.codec.decode` returns a **native dict**, not a dataclass
(`taut/src/taut/wire/codec.py:36-93`), and the generated `api.py` dataclasses are
`slots=True` with **no defaults** (every `Optional` is a required positional arg,
e.g. `RequestMeta` `api.py:277-284`) and expose no `to_wire/from_wire/codec` and
no name→class registry. So the entire decode side is a hand-written recursive
IR-driven reconstructor — not a "reflection fallback."

Fix: Specify `from_wire` as its own step: a recursive IR-driven dataclass builder
(name→class lookup, walk `schema.messages[name].wire_fields()`, recurse into
nested Msg/Enum/List/Optional, instantiate by keyword). Add a full round-trip test
(`dataclass → to_wire → encode → decode → from_wire → equality`) as the phase gate.

### [P1] The bridge byte-encoding boundary is unowned, and the wire ABI is an unresolved Open Decision that gates Phase 2

Reference: Plan Phase 1 step 3 / Phase 2 step 3; Design "Open Decisions".

The design says the native ext accepts "encoded taut request bytes" and Phase 1
step 3 says "convert request dataclasses to encoded bytes," but the current
`NativeCoreBridge.call` passes the **raw dataclass** straight to the PyO3 call —
no `to_wire`, no `codec.encode`, no `codec.decode` anywhere in `bridge.py`
(`bridge.py:40-53`; `codec` is never imported outside `generated/`). PyO3 cannot
consume an arbitrary Python dataclass, so this layer *must* encode to bytes — but
the design's Open Decision ("Python-encoded CBOR into Rust, Rust-encoded CBOR out,
or transitional JSON") is unresolved, and Phase 2 step 3 ("Decode `LsRequest` /
Encode `LsResponse`" in Rust) already assumes a Rust-decodes-bytes ABI. The wire
ABI straddles two phases with conflicting assumptions and no single owner.

Fix: Resolve the wire ABI **before** Phase 2 (it gates the native signatures) and
assign one owner. Recommended: Python encodes `dataclass → to_wire →
taut.wire.codec.encode(schema, msg, native) → CBOR`; Rust round-trips through
`gwz-core`'s `generated.rs` tag-based CBOR. Wire `bridge.py` to actually call the
codec in Phase 1 so the fake-bridge tests exercise real bytes (matching the phase
gate "tested with fake encoded bytes").

## P2 findings

### [P2] The stream-helper set is empirically wrong — omits `pull_head`, speculates branch/stash events that core never emits

Exactly five handlers take an `EventSink` and emit member events: `materialize`,
`init_from_sources`, `pull_head`, `pull_snapshot`, `push`
(`handle_materialize.rs:117`, `handle_init_from_sources.rs:24`,
`pull_head_member_preflight.rs:32`, `push_member.rs:31`). The plan's and design's
stream list **omits `pull_head`**, and both speculate stash/branch stream helpers
"if core emits member events" — it doesn't (`handle_branch.rs`, `handle_stash.rs`,
`handle_snapshot/tag/commit/stage/repo_sync` take no `EventSink`). Fix: pin the
set to `{materialize, init_from_sources, pull_head, pull_snapshot, push}`, add
`pull_head_stream`, and state plainly that the rest are request/response only
(`operation_result` still available; `subscribe_events` yields no member events).

### [P2] The `gwz-core` Rust dependency + release coupling is undefined, while every sibling repo enforces strict tag-pinned ordering

Two reviewers, one issue. The native ext links `gwz-core` as a Rust crate exactly
as gwz-cli does (`gwz-cli/Cargo.toml`: `gwz-core = { path = "../gwz-core" }`), but
the dev-path-vs-release-tag mechanism is deferred to a single Phase 6 bullet and
an Open Decision. `gwz-core/RELEASE.md` mandates "always release gwz-core before
gwz-cli" with a git-tag pin + remote-tag-existence gate; gwz-py adds a **third**
axis (`taut-proto >= 0.6.0` in `pyproject.toml`) and `regen_protocol.py:31`
defaults to the `../gwz-core` path. Without a defined released-schema source +
pin, a wheel can be built against an unreleased/local schema, and the packaged
`gwz.ir.json` can silently diverge from the `gwz-core` the extension links. Also
unaddressed: the **edition-2024 / rustc-1.95** floor and `git2`+openssl
(https/ssh) system-build impact on wheel portability.

Fix: Promote to a Phase 2 + Phase 6 deliverable — declare `gwz-core` with a dev
path dep and a release-overridable source (git+tag), add a release-ordering note
mirroring `gwz-core/RELEASE.md`, and a CI step asserting the packaged `ir.json`
matches the `gwz-core` tag the extension is built from. Note the toolchain floor +
git2/openssl in the wheel/CI plan.

### [P2] The build backend (maturin vs setuptools) is decided in Phase 6 but committed in Phase 2

`pyproject.toml` today is `setuptools.build_meta` + setuptools-scm; Phase 2 builds
the PyO3 skeleton, but the final backend choice is Phase 6 step 1. maturin and
`setuptools.build_meta` are mutually exclusive and source versions differently, so
a Phase-2 native skeleton under one assumption risks late Cargo.toml/version/
package-data rework. Fix: pull the binding-backend decision into Phase 2 step 1
(it gates every native step), or state Phase 2 commits a backend and Phase 6 only
finalizes wheel/platform strategy on top of it.

### [P2] Generated `client.py`/`server.py` are dead, ABI-incompatible code (source-of-truth violation)

`regen_protocol.py` emits `generated/client.py` (`GwzCoreClient`) and
`server.py`, but gwz-py reimplements the facade by hand and never imports them.
Worse, their transport contract is **incompatible** with the hand-written bridge:
`GwzCoreClient` calls `self._t.call("status", StatusResponse, request=...)`
(response as a **type**, `client.py:9-67`) while `CoreBridge.call(method,
request_message: str, response_message: str, request)` takes the response as a
**name string** (`bridge.py:11-18`). Two divergent transport ABIs side by side.
Fix: either make `CoreBridge` implement the generated transport (and delete the
hand-written duplication), or configure regen to emit `api.py` + `ir.json` only,
and record the decision in Phase 0.

### [P2] `schema_version` mismatch + no packaged-IR-vs-native drift guard

`client.py:72` hardcodes `SCHEMA_VERSION = "gwz.protocol/v0"`, but `gwz-core`'s
runtime emits `"gwz.v0"` (`operation_runtime.rs:326,510`; `tests/protocol.rs:31`)
— and core itself is inconsistent (`status/workspace_path.rs:301` uses
`"gwz.protocol/v0"`). The taut field is a real version handshake
(`gwz.taut.py:417`). Separately, `gwz.ir.json` carries only IR-format `version=1`,
and nothing checks the packaged protocol matches the linked `gwz-core`; wire is
tag-based CBOR, so drift mis-decodes silently rather than erroring. Fix: pin
`SCHEMA_VERSION` to the single literal core validates against (sourced from the
IR, not hardcoded), and add a build/load-time fingerprint check.

### [P2] Error model is still a stub — `member_errors` dropped + a `GwzError` name collision **(confirms R55 finding 4)**

`errors.py` defines the 5-class hierarchy, but `GwzOperationError` preserves only
message/response/aggregate_status/operation_id/request_id (`errors.py:23-34`) —
the design and Phase 1 step 6 require **member errors**, which `_raise_for_response`
never extracts (`client.py:218-234`), though `ResponseEnvelope.errors` /
`OperationResult.errors` carry them (`api.py:544,571`). Also a real hazard: the
generated protocol **dataclass** `GwzError` (`api.py:297`) and the
**exception** `GwzError` (`errors.py:7`) share a name and both get star-exported.
Fix: add `member_errors` to `GwzOperationError` and populate it; rename the
generated data type (e.g. `GwzErrorDetail`) or namespace/avoid star-exporting it.

## P3 findings (precision + doc fixes)

- **Facade duplicates core's `start_ref="HEAD"` create default.** `client.py:484-485`
  injects `"HEAD"`, but `handle_branch.rs:130` already defaults the same field —
  drift risk across the FFI. Let `None` flow to core, or document core as
  authoritative.
- **`snapshot --branch` (`.current`) is coherence-validated, not a passthrough.**
  It rejects detached/unborn/mixed (`handle_materialize.rs:531-566`), unlike
  omitting the source. Add one sentence distinguishing the two.
- **Bridge Contract prose excludes the two scalar-param OUT methods.**
  `events.subscribe`/`operation.result` take a scalar `operation_id`, not a request
  dataclass (`gwz.taut.py:81-87`); state that `call()` covers only role-in methods.
- **Async-off-loop / GIL is asserted, not designed.** Specify PyO3 `allow_threads`
  around blocking handlers + a bounded async-safe event queue, and reconcile
  `subscribe_events`'s signature (the Protocol types it `def → AsyncIterator` while
  the native impl is an async generator, `bridge.py:20`).
- **Transitional JSON ABI would mistype i64.** taut's `jsoncodec` string-encodes
  i64 for precision (`jsoncodec.py:39-41`; 29 INT fields incl. `OperationEvent.timestamp_ms`);
  a naive `json.dumps` drifts. Mandate the IR-driven jsoncodec on both sides, or
  keep CBOR-only for the bridge.
- **`_raise_for_response` raises on `partial`/`dirty`/`conflicted`** (`client.py:218-234`),
  but the design lists these as states to *describe*; raising discards the typed
  response and can throw before a stream's members are drained. Decide raise-vs-return.
- **(confirms R55)** Phase 3's central registry is a serialization point — the
  Integration Agent owns it; A–D contribute family modules. Make explicit; the
  `dispatch/<family>` layout doesn't exist yet (no native ext at all).
- **(confirms R55)** Phase 5 CLI: `cli.py` is an if/elif monolith (`cli.py:54-73`);
  Agent A must *invent* (not refactor) the command registry and migrate the 5
  existing commands before B–E start. Hard serialization gate.
- **(confirms R55)** Phase 1 step 6 over-scopes the error model (the hierarchy
  already exists) and the **Client Agent is missing from the Phase 1 roster**;
  re-scope to the real gaps (`member_errors`, decode→`GwzProtocolError`,
  native→`GwzBridgeError`).
- **(confirms R55)** `clone` deferral is code-justified (no taut method;
  `handle_clone_workspace` is a direct Rust call, `globalargs.rs:442`), but the plan
  never names it a release gate. Commit to the Python-local "git clone +
  `materialize("lock")`" path or mark it a Phase 7 known-limitation.
- **Doc fixes:** the Design "Package Shape" tree shows `src/gwz/tests/` but the
  scaffold + `pyproject` `testpaths` use `src/tests/` — agents could fragment test
  ownership; fix the tree. `client.py` is already **508 LOC**, past the plan's own
  ~500-LOC step budget, and Phase 4 adds more stream helpers to it — split by family
  before it becomes the facade chokepoint. The Design file's H1 says "Plan"; add a
  milestone→phase cross-reference (the design's 10 milestones aren't 1:1 with the
  plan's 8 phases).

## Strengths (verified against code)

- **Every protocol claim verifies exactly** against the taut schema, the generated
  Python, *and* the Rust handlers: `BranchOp.merge`→`start_ref`
  (`handle_branch.rs:294-297`), `materialize --switch`→`MaterializeTargetKind.branch`
  +`name` (`api.py:108-114,336-339`), `snapshot` bare/named→`SnapshotSourceKind`
  `.current`/`.branch`, `repo_sync`→`selection.paths` (`RepoSyncRequest` is meta-only),
  and `ExecRequest/ExecResponse` data-only (no client method, matching the explicit
  "NO service method" comment `gwz.taut.py:872-876`).
- **The async facade is complete and ergonomic** — all 18 service methods +
  `operation_result` async, enforced by `test_public_operations_are_async`; the
  ergonomic helpers (`switch`, `snapshot current_branch/branch`, `branch source_ref`,
  `repo_sync` path) are implemented and tested.
- **The encode side and the "decode without a gwz-core checkout" claim hold**:
  `to_wire` round-trips byte-identically through `taut.wire.codec` CBOR, and the
  codec is a pure IR interpreter driven only by the packaged `gwz.ir.json`.
- **The `operation_id` event model is sound**: `events.subscribe` uses
  `shape="log"` (append-only history, `shapes.py:36-39`), so a late subscriber
  replays from sequence 0 — no submit-then-subscribe race. The runtime primitives
  it needs already exist.
- **The `clone`/`forall` boundary is correctly diagnosed**, the regen `--check`
  gate is wired into `run_tests.py` (mirroring the gwz-core gate), and the DRY
  helpers (`meta`/`_enum_value`/`_raise_for_response`/`_stream_call`) already exist.

## Recommended gating actions (priority order)

1. **Resolve the wire ABI and assign one owner** before Phase 2 — it gates every
   native signature. Wire the codec into `bridge.py` in Phase 1 so the fake-bridge
   tests use real bytes.
2. **Re-scope Phase 1 step 1** to include the full `from_wire` IR-driven decoder as
   its own step with a round-trip gate.
3. **Re-scope Phase 3** to acknowledge the Rust per-method dispatch + the three
   handler-shape shims, owned by the Integration Agent; family agents wire only.
4. **Re-scope Phase 4 step 1** to specify the runtime/`ExecutionReport` adapter, and
   fix the stream-helper set (`+pull_head`, drop branch/stash speculation).
5. **Move the `gwz-core` dependency pinning + build-backend decision into Phase 2**,
   with a release-ordering + ir.json-drift guard mirroring `gwz-core`/`gwz-cli`.
6. Fold in the P2 error-model + dead-`client.py`/`server.py` + `schema_version`
   fixes, and the P3 doc/precision nits, while the Protocol/Client agents are in
   Phase 0–1.
