# Retired Python session train text from GwzPyTransportDesign.md

Retired on 2026-09-24 with the Python concurrency design train, when the operator called a clean-slate redesign of the client, core and transport boundary. Nothing here is normative. Relative links resolve from `gwz-py/dev-docs/`.

## Release-gate status line as it stood

Current release-gate status (2026-09-24): **NO-GO for Phase 6 completion and Phase 7 activation** because the single-active-operation rule prevents overlapping network commands on one Python `Client`. See the [operator-directed finding](../../dev-docs/GwzPyTransportConcurrencyNoGo.md) and the [rejected concurrency verdict](../../dev-docs/GwzPyTransportConcurrencyDesign-Verdict-2.md). The historical design GO below remains the verdict on the earlier review object; it does not close this new finding. The operator has authorized the [session v2 redesign](../../dev-docs/GwzPyTransportSessionV2Design.md), which is still draft and unimplemented.

## Candidate correction

Candidate correction (2026-09-24, pending review): the
[session v2 design](../../dev-docs/GwzPyTransportSessionV2Design.md)
would replace the one-active-operation and Python network-lock rules above
with session-owned operation records, bounded overlapping work, equal-capacity
admission and independent cancellation. It would also refuse explicit Python
CLI placement before endpoint or credential work. The
[caller guide](GwzPyConcurrentOperationsV2.md) is a draft surface, not an active
API. The historical design remains the current implementation contract until
the correction has review GO and passes a separate implementation gate.
That correction replaces the §2 post-Closing rule: new Git work refuses, but
session-owned operation outcomes and event readers remain readable from the
bounded ledger after physical host shutdown, until expiry or release. A
repeated `close()` reads its retained cleanup and per-operation summary.
It also replaces §2's installed-runtime-stable-until-close sentence and §4's
never-reused-request-ID cancellation rationale. The captured endpoint
configuration remains stable while runtime generations roll over after 256
registered IDs. A cancellation handle binds its public operation ID and the
original core generation; a caller request ID reused after rollover cannot
redirect an old handle to new work.

## Draft foundation pointer

Draft foundation pointer (2026-09-24, pending review): for the native Python
session, the [v4 foundation design](../../dev-docs/GwzPyTransportSessionV4FoundationDesign.md)
would also supersede §2's per-call `runtime.request(meta, operation_id)` flow
and §3's statement that no extra registration is needed. The session would
bootstrap each generation at construction through one reserved internal
registration, and admit each operation through `admit_local` and
`register_and_open`. §4's rule that the capability preflight and dispatch use
the same runtime generation is kept. CLI and local-command paths keep
`request()`. This design remains authoritative until that draft has review GO.
