# GwzPyPlan Review55-2 Resolution

Status: closed after `GwzPyPlan-Review48-2.md` and the follow-up plan/design
edits.

## Verdict

No remaining review blocker before Phase 0 / roll-build.

`GwzPyPlan-Review48-2.md` recommends proceeding to Phase 0. The remaining
`GwzPyPlan-Review55-2.md` required fix and P3 notes have been addressed in
`GwzPyDesign.md` and `GwzPyPlan.md`.

## Closed Items

1. Branch-create default ownership
   - Review55-2 status: P2 required fix.
   - Resolution: Phase 3 now matches the design and Phase 0 guidance:
     branch-create default start point is owned by `gwz-core`; Python passes no
     `start_ref` unless the caller supplies one.

2. Client helper scope
   - Review55-2 status: P3.
   - Resolution: Phase 4 stream-helper work scope now includes the Phase 0 client
     helper module if created, and directs stream mechanics there while leaving
     `client.py` as the facade.

3. Phase 6 docs sequencing
   - Review55-2 status: P3.
   - Resolution: Phase 6 now separates parallel pre-decision agents from the
     post-decision Docs Agent.

4. Canonical `schema_version`
   - Review48-2 status: P3 residual, not blocking.
   - Resolution: the design and plan now say that a missing canonical
     core/schema-owned schema-version literal is a Phase 0/1 `gwz-core`
     coordination item. Until it exists, the packaged IR fingerprint remains the
     enforceable drift guard.

5. Streaming final-status policy
   - Review48-2 status: minor note, not blocking.
   - Resolution: the design and plan now state that stream helpers drain events,
     read `operation.result`, and then apply the same raise-with-response policy
     as request/response client methods.

## Remaining Carry-Forward Work

- Phase 0/1 should either establish one canonical `schema_version` literal in
  `gwz-core` / schema or record the mismatch as a core coordination blocker.
- Phase 0 should still decide whether generated taut `client.py`/`server.py`
  remain reference-only artifacts or whether regeneration is configured to emit
  only runtime artifacts.
- Phase 2 should make the native build backend decision before Rust extension
  implementation begins.

## Verification

- `python run_tests.py`: `regen_protocol: OK`, `10 passed`.
