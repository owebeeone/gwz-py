# GwzPyPlan Review 55

## Verdict
Approved.

No required fixes remain. The corrected plan covers the material requirements in
`dev-docs/GwzPyDesign.md`, matches the current scaffold, and the current
baseline test command passes (`python run_tests.py`: 10 passed).

## Findings

No required fixes.

1. Severity: P3
   Location: `dev-docs/GwzPyPlan.md` Phase 1, lines 125-180
   Issue: Phase 1 assigns the error-model step to the Client Agent, but the
   phase's parallel-agent list omits the Client Agent.
   Impact: The ownership is explicit enough to execute safely, but the agent
   roster is slightly inconsistent.
   Recommendation: Add Client Agent to the Phase 1 parallel-agent list when the
   plan is next touched.

2. Severity: P3
   Location: `dev-docs/GwzPyPlan.md` Phase 6, lines 525-547
   Issue: The final CLI dispatch decision and documentation steps both include
   README or packaging-adjacent edits.
   Impact: This is acceptable late-release coordination, but those edits should
   not be run as fully independent parallel writes.
   Recommendation: Treat the dispatch decision as the integration point, then let
   Docs update README/release notes from the settled release mode.

## Coverage Matrix

| GwzPyDesign.md requirement | GwzPyPlan.md coverage |
| --- | --- |
| GWZ naming, `gwz-py` distribution, `gwz` import package, `gwz` console script | Baseline, Phase 6 packaging, Phase 7 release checklist |
| Taut-generated API is source of truth; generated files and IR JSON are gated | Planning Principles, Phase 0 protocol check, Phase 1 codec, Phase 6 CI |
| Full current `GwzCore` surface including `repo_sync`, `branch`, `stash`, `events.subscribe`, `operation.result` | Phase 0 facade check, Phase 3 native coverage, Phase 4 events/results |
| Async Python API that does not shell out to Rust `gwz` | Planning Principles, Phase 0 async/API checks, Phase 5/6 CLI boundary |
| Corrected edge cases: `repo_sync` selection paths, branch merge `start_ref`, materialize branch switch, snapshot branch forms | Phase 0 tests, Phase 3 native edge cases, Phase 5 CLI edge cases |
| Operation events by `operation_id`, no generic method/request stream ABI | Phase 1 event/result transport helpers, Phase 4 runtime/subscription/result work |
| Message-first bridge, encoded taut bytes, no function-per-operation Python FFI | Phase 1 bridge boundary, Phase 2 `ls` skeleton, Phase 3 dispatch layout/gates |
| Blocking Rust work off the Python event loop with async-safe event delivery | Phase 2 bridge threading, Phase 4 runtime wrapper and event subscription |
| Python CLI parity with Rust CLI, including `branch` and `stash` | Phase 5 command-family modules, Phase 6 dispatch decision |
| `clone` and `forall` are CLI-only workflows outside generated `GwzCore` methods | Planning Principles, Phase 5 CLI-local workflow module, Phase 7 docs |
| Error hierarchy preserving request id, operation id, aggregate status, member errors, original response | Phase 1 explicit Client Agent error-model step, Phase 4 result preservation, Phase 7 failure audits |
| Testing strategy: fake bridge, protocol regen, async API, native integration, CLI parity | Phase 0 through Phase 7 gates |
| Release packaging, CI, wheel strategy, PyPI publish | Phase 6 packaging/CI/release mode, Phase 7 hardening |

## Parallelism Assessment

The prior parallelism blockers are fixed. Phase 3 now lands a central native
dispatch layout first and splits implementation by disjoint operation-family
modules plus per-family test files. Phase 5 now serializes the shared CLI parser,
rendering, and exit-code layer before command-family agents add separate modules.
Test ownership is explicit enough: shared fixtures have one owner, and production
agents use unique focused test files.

Elapsed-time efficiency is good. The only cautions are administrative: add the
Client Agent to the Phase 1 roster, and serialize Phase 6 release-mode docs after
the dispatch decision is made.

## DRY / Maintainability Assessment

The plan follows the intended maintainability model. Generated taut API remains
canonical, protocol encode/decode is centralized, request metadata and enum
coercion have shared helpers, response error handling has one path, CLI rendering
and exit-code logic are shared, and native dispatch centralizes codec/error
mapping while splitting operation families.

No current-codebase mismatch surfaced. The scaffold contains the expected
generated protocol files, async client facade, message-first bridge stub, early
CLI, and baseline tests; the native extension, full codec, streaming runtime, CLI
parity, and packaging decisions are correctly left to later phases.
