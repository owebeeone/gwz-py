# GwzPyPlan Review 48-2 (second pass)

Review targets (revised):
- `dev-docs/GwzPyPlan.md`
- `dev-docs/GwzPyDesign.md`

Reviewer: Claude Code (Opus 4.8). Follow-up to `GwzPyPlan-Review48.md`; line
references below are to the **revised** plan/design. (A `GwzPyPlan-Review55-2.md`
from the other reviewer also exists; this pass tracks the Review48 findings.)

## Verdict

**Comprehensive response — recommend proceeding to Phase 0.**

Every Review48 finding is resolved with a concrete, correctly-placed change, and I
re-verified the design-side fixes against the code. The five P1 scoping gaps are
all closed precisely, the P2s are addressed (including the dead-`client.py`
decision and the gwz-core dependency/release-pinning + fingerprint gate), and the
design-side P3s landed. The plan is now executable. One residual is worth a line
because the `schema_version` fix names a single source that does not yet exist;
everything else is closed.

## Disposition of Review48 findings

**P1 — all resolved (and exactly as recommended):**
- **Rust per-method dispatch** → Phase 3 step 1 owns "the Rust method-name dispatch
  table ... because gwz-core exposes individual handlers rather than a public
  reusable router," with a follow-up option to push a shared router into gwz-core
  (lines 342-346). A dedicated **Bridge Integration Agent** owns it.
- **Heterogeneous handler shims** → Phase 3 step 1 "Own the three handler-shape
  shims: no-backend, backend, backend-plus-`EventSink` ... family modules should
  not duplicate `Git2Backend`/event-sink wiring" (lines 352-355) + DRY gate (750-751).
- **Event runtime / `ExecutionReport` mismatch** → Phase 4 step 1 "Treat this as
  first-use runtime code ... adapter between handlers that return typed `*Response`
  and the `OperationRuntime`/`ExecutionReport` model ... or a bridge-owned
  `EventSink` plus operation_id result store" (lines 432-437).
- **`from_wire` / decode path** → Phase 1 step 2 "full IR-driven `from_wire` ...
  recursive IR-driven dataclass builder ... instantiate `slots=True` dataclasses by
  keyword ... not a reflection fallback" + the round-trip gate (170-182).
- **Wire ABI ownership** → Planning Principle "native bridge ABI is CBOR by
  default" (21-22); Phase 1 steps 3-4 commit CBOR via `taut.wire.codec`; Phase 1
  gate "CBOR is the committed native bridge ABI before Phase 2 begins" (249).

**P2 — all resolved:**
- Stream-helper set pinned to `{init_from_sources, materialize, pull_head,
  pull_snapshot, push}` with `pull_head_stream` added and branch/stash explicitly
  excluded (Phase 4 step 4 + gate 485; design 242-249).
- gwz-core dependency/build + release coupling → Phase 2 step 1 (dev path + git+tag
  release override, edition-2024/rustc + git2/openssl notes) and Phase 6 (released
  source, release-ordering note, packaged-IR-vs-linked-tag CI check + build-fail
  gate, 605-657).
- Build backend decided **now** → Phase 2 step 1 + gate; Phase 6 "must not reopen"
  (269-270, 319, 602).
- Dead/incompatible `client.py`/`server.py` → Phase 0 step 2 decision (emit
  `api.py`+`ir.json` only, or prove `CoreBridge` is the sole ABI) + design 157-162.
- Error model → new Phase 1 step 7 adds `member_errors`, the `GwzError` vs
  `GwzErrorDetail` collision fix, and the status→exception mapping (222-242; design
  355-365); **Client Agent added to the Phase 1 roster**.

**P3 — resolved, including the design-side ones:**
- snapshot bare-`--branch` is now documented as coherence-validated, distinct from
  omitting source (design 42-44); the two role-out methods are flagged as scalar
  `operation_id`, not request dataclasses (design 288-289; Bridge Contract "for
  role-in service methods", 259); GIL/`allow_threads` + bounded async-safe queues
  spelled out (design 306-309); `src/tests` tree corrected + note (design 103-107);
  design re-titled "Design" + a Plan Cross-Reference added (1, 417-424).
- Facade `start_ref="HEAD"`: plan/design now say let core own it (Phase 0 step 3;
  design 237-238). JSON-i64 drift closed by mandating taut's IR-driven jsoncodec
  (21-22, 189-190). `_raise_for_response` policy decided (raise by default but carry
  the typed response, Phase 1 step 7 / design 355-358). Phase 3 registry serialized
  under the Integration Agent; Phase 5 cli.py chokepoint handled by CLI Agent A
  inventing the registry first (per-module write scopes); client.py-split trigger
  added; clone is now an explicit Phase 7 known-limitation gate.

## Residual (one item; worth closing, not blocking)

### [P3] The `schema_version` fix names a single source that does not exist yet — and gwz-core is internally inconsistent

Reference: Design "Taut Protocol Use" 180-183 ("`schema_version` ... must come from
one generated/core-owned source"); Plan Phase 1 gate 250-251; Phase 2/6 fingerprint
gates.

The revision correctly removes the *gwz-py-side* hardcode intent, but verification
shows the named source isn't there:

- `schema_version` is a **free-text per-request field** in the schema
  (`gwz.taut.py:418/429` `schema_version=F(2, STR)`), so the generated IR carries
  **no canonical `schema_version` literal to source from** (the IR's `"version": 1`
  is the IR *format* version, not the protocol schema version). `RequestMeta` in the
  generated `api.py:279` is just `schema_version: str` with no default.
- gwz-core itself uses **two different literals**: `"gwz.v0"`
  (`operation_runtime.rs:326,510`) vs `"gwz.protocol/v0"` (`workspace_path.rs:301`,
  `workspace_ops/tests/g02.rs:745`). The old client hardcoded `"gwz.protocol/v0"`,
  which the runtime path does not emit.

So "source `schema_version` from one generated/core-owned source" cannot be
satisfied today: there is no such constant, and "core-owned" is itself ambiguous.
The **IR fingerprint** half of the drift guard is sound (the packaged `gwz.ir.json`
is a hashable content artifact, so Phase 2/6's packaged-IR-vs-linked-gwz-core check
works). It is specifically the version-*literal* handshake that has no ground.

Fix: Make Phase 0/1 establish the canonical literal first — either add a single
exported constant to gwz-core / the schema and have the IR or native extension
surface it, or resolve gwz-core's own `gwz.v0` vs `gwz.protocol/v0` split — before
client/IR/native all "source from" it. Until then, treat `schema_version`
consistency as a gwz-core coordination item, and lean on the IR *fingerprint*
(which is groundable) rather than the version string for the drift gate.

## Minor note

- The "raise by default on partial/dirty/conflicted, but carry the typed response"
  decision (Phase 1 step 7) is reasonable for request/response calls. It would help
  to state the *streaming* interaction once: whether a stream helper whose final
  `operation.result` is `partial`/`conflicted` raises after events are drained, or
  returns the result for per-member inspection. Not blocking — a one-liner in the
  Error Model when stream helpers land.

## Bottom line

No blockers. The plan and design now form an executable, well-scoped implementation
plan with disjoint agent scopes, a committed CBOR ABI, a real decode path, an
owned Rust dispatch, a corrected event/stream model, and a release/fingerprint
story. Close the `schema_version` canonical-literal residual while the Protocol and
Client agents are in Phase 0–1, and proceed.
