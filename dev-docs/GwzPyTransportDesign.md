# Python transport session for GWZ 1.1.0

Current release-gate status (2026-09-23): **NO-GO for Phase 6 completion and Phase 7 activation** because the single-active-operation rule prevents overlapping network commands on one Python `Client`. See the [operator-directed finding](../../dev-docs/GwzPyTransportConcurrencyNoGo.md) and [merged round-1 remediation](../../dev-docs/GwzPyTransportConcurrency-RemPlan-1.md). The historical design GO below remains the verdict on the earlier review object; it does not close this new finding. The first concurrency amendment received Consistency and Safety NO-GO and has not been implemented.

Status: **S1.1/S1.2 design accepted for implementation, 2026-09-23**.
Consistency, Safety and Surface report GO at Python
`259f73cc030c0da0bf29903bab258de0463b7d02`, paired with the setup-failure
amendment at core `479926c18265276e5a45659c4523a13a71f4a51f`. Reports:
`../../dev-docs/GwzTransportParallelInterfaces-Review{Consistency,Safety,Surface}-1.md`.
Operator authorized implementation. This accepts the bounded package-boundary
amendment to the [1.1.0 plan](../../gwz-core/dev-docs/GwzV110Plan.md) §3 Phase 1/6;
product acceptance, release pins, tags and activation remain separate gates.

## 1. Binding and package decision

The existing `gwz._gwz_core` PyO3 extension in **`gwz-py` is the Python
binding**. It owns an opaque native `TransportSession` that wraps one
long-lived `gwz_core::transport_host::TransportRuntime`. Core remains the sole
owner of its transport mux, endpoint workers, pools, request registration,
cancellation and bounded cleanup. `gwz-transport` provides those Rust
primitives to core; it has no Python-specific owner, facade or PyO3 feature.
The Python extension calls the core host API to manage sessions and operations,
and therefore uses the same `gwz-transport` pooling code as the Rust host.
It does not expose a generic pool, raw stream, transport envelope or clock.

This requires a **bounded amendment** to S1.1's binary choice of bindings
inside `gwz-transport` versus a separate package, and to S6.1/S6.2's direct
binding-crate edge. The revised choice is the already-published `gwz-py`
package's existing extension. S6.1 and S6.2 can be one `gwz-py` implementation
lane after S1.2 GO; no fifth crate or extra Phase 8 release step is needed.
`gwz-transport` still publishes at Phase 8 step 2, core links it at step 5,
and `gwz-py` links registry-pinned core at step 7. The extension need not
declare an ornamental direct `gwz-transport` dependency: it reaches the
transport through core's public host API. This changes package placement,
not the 1.1.0 product scope or acceptance gates.

The reason is visible in current source. `gwz-py/native/src/shims.rs` makes
`Git2Backend::new()` per call, while the candidate core
`TransportRuntime::request(meta, operation_id)` already returns a scoped
`TransportRequest` with `backend()`, `cancel()` and async `finish()`.
`TransportRuntime::shutdown()` drains the host. Core's `Session` already
registers and begins the mux request and owns endpoint cleanup. Adding a
`gwz-transport::python` wrapper to register or cancel the same request would
create duplicate lifecycle authority and require exports of private endpoint
handles. A separate binding crate that depends on the new core host would
also be scheduled for publication before core 1.1.0 in the current Phase 8
order. The existing extension is the narrow, acyclic boundary.

## 2. Host and operation lifetime

`NativeCoreBridge` holds one opaque native `TransportSession`; `Client` keeps
using that bridge. Bridge construction is **lazy**: it creates only the cheap
Python/native holder. Local-only status, tag and snapshot operations must
never construct an SSH/HTTPS endpoint or parse proxy/CA settings. On the
first network operation, the native side captures endpoint environment and
calls `TransportRuntime::from_environment()` to construct the local SSH+gh
HTTPS host. Construction performs no Git-host connection or credential
request. An invalid HTTPS proxy or CA may refuse that network operation, but
cannot break a preceding or later local-only operation. A failed
construction returns to Uninitialized unless close has begun; the next
network request may retry after the environment is corrected. Once
installed, the host and its endpoint environment are stable until close;
changing process environment mid-session does not silently change its
credentials or trust context.

The native Rust owner has a **monotonic serialized lifecycle**:
Uninitialized → Constructing → ReadyIdle ↔ ReadyActive → Closing → Closed.
Construction and admission reserve a transition under one lifecycle lock,
then do blocking work without holding that lock or the GIL. Close atomically
enters Closing under that lock. Closing forbids new construction and
admission, even from another Python thread. If construction is in flight,
close joins it; a constructor result that loses the race is shut down once
without publication or request admission, and its cleanup facts contribute
to the final close report. If admission is in flight, close waits for that
transition, cancels any admitted request, and awaits its finish. The
constructor/admitter rechecks the state before publishing the runtime or
running a handler. Close does not return while either can still publish
work. There is exactly one shutdown of each constructed runtime. Closing
and Closed never transition back, including after failed construction.
New calls through this bridge after Closing begins, whether network or
local-only, refuse with a typed `GwzBridgeError(code="InvalidRequest")`
whose message says the client is closed. Existing module-level native
compatibility functions are separate and remain usable.

Every network operation gets an immutable `RequestMeta`, existing request ID,
operation ID and `TransportRequest`. Its handler receives only
`request.backend()`. The backend cannot outlive the request. After handler
completion or error, native dispatch awaits `request.finish()` before
publishing completion or admitting the next network operation. The host
survives request finish, preserving healthy connections. Awaited close
cancels any active request, waits for bounded request finish, then awaits
host shutdown and retains the final `CleanupReport`. It must preserve
nonzero `pending_local_work` and a false `peer_cleanup_confirmed`, without
double-counting the request and host snapshots of the same work. Last-owner
drop initiates shutdown, but awaited close is the inspectable completion
path.

The current endpoint has scheme-specific `gwz-transport::pool::Pool` values
for SSH and HTTPS under one `shared_reservation::Authority`. The plan's “one
pool” requirement means one Rust-owned host and pooling authority across
operations, with no Python pool. It does not imply that current SSH and HTTPS
share a literal `Pool` value. Separate OS processes naturally have separate
hosts and pools.

The native flow is:

```text
Client -> NativeCoreBridge -> gwz._gwz_core.TransportSession (lazy owner)
  -> first network call: core production TransportRuntime constructor
     -> existing core endpoint/mux and gwz-transport pools
  -> each network call: runtime.request(meta, operation_id)
     -> existing core handler(request.backend())
     -> TransportRequest.finish() -> existing response/events
  -> close: enter Closing -> join construction/admission
     -> cancel active request -> finish -> runtime.shutdown() -> Closed
```

The dependency graph is `gwz-py -> gwz-core -> gwz-transport`. The wheel has
one importable native module and no Python-visible pool object. The current
`with_local_transport` helper creates and shuts down a runtime per command,
so the Python bridge must not use it for each call. Every network funnel,
including clone/materialize, remote reads, fetch and push, must use the
bridge's host once Phase 6/7 activates transport. Existing local-only
handlers can retain their current backend path.

## 3. Admission, cancellation and threads

**Rust refuses a second active network operation** on the same native
session, including one arriving through another Python thread. This is the
authoritative overlap rule; a second call receives a typed busy/capacity
error and cannot install a new policy or touch the first request.
`NativeCoreBridge` serializes its async network calls with an `asyncio` lock,
but that is only a convenience. Cancelling a call while it waits for that
lock removes the queued Python call and does not invoke native cancellation, alter pool
capacity, or cancel the active operation. A direct native call still obeys
the Rust refusal. On successful admission, core resolves and installs the
operation policy before the first remote open, while no lease is non-idle.

An admitted operation has a native cancellation handle keyed to its request
ID. Python task cancellation signals that handle from another Rust thread,
lets transport close only that request's streams, awaits bounded finish,
then propagates `CancelledError`. The finish wait is shielded from further
Python task cancellation so `CancelledError` cannot abandon Rust cleanup.
A submitted operation retains its native
owner until result and cleanup are recorded even if its event consumer stops.
Dropping an async event generator cannot shut down the host. The existing
extension detaches the GIL around blocking `call`, `submit` and event waits;
keep it detached for host construction, admission, Git work, cancellation
waits and shutdown. `NativeCoreBridge` can continue to use
`asyncio.to_thread`. Rust workers do not call Python while holding a
session or pool lock.

The frozen core additions are
`pub fn TransportRuntime::from_environment() -> ModelResult<Self>` and
`TransportRequest::cancellation_handle(&self) -> TransportCancellation`,
where the cloneable, `Send + Sync` `TransportCancellation::cancel(&self)`
targets only that request and remains callable while its synchronous Git
handler runs. A retained handle cannot cancel a later request because the
session never reuses a request ID. Existing
`TransportRuntime::request(meta, operation_id)`,
`TransportRequest::backend()/finish().await`, and
`TransportRuntime::shutdown().await` remain the operation/cleanup APIs. No
extra registration, `request_with_policy`, Python retry method, generic
host trait, or raw pool export is needed.

## 4. Python API, protocol and endpoint security

Keep `Client.fetch`, `fetch_stream`, `push`, `push_stream`, the existing
`NativeCoreBridge.call/submit` behavior and `Client.meta` inputs. Add the
public immutable Python value
`TransportCleanup(pending_local_work: int, peer_cleanup_confirmed: bool)`.
`async NativeCoreBridge.close() -> TransportCleanup` and
`async Client.close() -> TransportCleanup | None` await native finish and
shutdown. Native close returns the same retained snapshot on every call;
closing before first network use returns `(0, False)` because no peer
cleanup occurred. `Client.close()` delegates to the bridge; for a custom
bridge without close it returns `None` rather than claiming cleanup.
`Client.__aexit__` awaits `Client.close()` and discards the result. The
native session object remains private to `gwz.bridge`.

`async NativeCoreBridge.cancel_operation(operation_id: str) -> TransportCleanup`
and `async Client.cancel_operation(operation_id: str) -> TransportCleanup`
accept the existing public operation ID, map it to the request ID inside
that bridge, signal only its active cancellation handle, and resolve only
after `TransportRequest.finish()` has completed. A second cancellation of
the same active operation joins that completion. The bridge retains at most
the latest completed cancellation snapshot for an idempotent repeat; an
unknown, foreign, older completed or expired ID raises typed
`GwzBridgeError(code="InvalidRequest")` without touching the current
operation. A queued Python-lock waiter has no admitted operation ID and its
cancellation never calls native `cancel_operation`. A custom bridge without
the hook raises `GwzBridgeError(code="UnsupportedOperation")`; it does not
pretend to cancel. Add optional
`Client.meta(max_retries: int | None = None)` beside `concurrency` and
`max_connections_per_host`. It writes only the corresponding accepted
`RequestMeta.policy` field. Custom `CoreBridge` test doubles keep working.

`configure_transport_runtime` remains the typed core message for its
existing server-timeout setting; it neither constructs this host nor
configures Python clocks. `transport_capabilities` remains a typed reporter.
For a network call, the capability preflight and dispatch must use the same
live core receiver/runtime generation. Python already checks explicit file
identity through this message; S6.2 extends that check for explicit
placement. Omitted placement uses local. Explicit `cli` requires an
installed and bound endpoint on the same runtime, supported route and
capability intersection, or refuses before mutation or credential access.
The accepted in-process attachment path stays in Rust. Python does not
interpret `Envelope` values or provide a physical carrier. No new wire
message is required.

The endpoint host owns SSH agent/known-host and explicit-key resolution,
gh execution, HTTPS TLS/proxy policy and physical opens. HTTPS remains
gh-only. The Python surface receives neither agent-socket contents,
known-host bodies, gh tokens/headers nor raw helper output. Sanitize
display text, machine messages, event details and nested causes before
attaching errors to Python exceptions, while preserving stable error codes
and request attribution. A gh failure or unsupported proxy refuses through
the core model error.

The [retry plan](../../gwz-core/dev-docs/GwzRemoteTransportRetryPlan.md)
was accepted for this program after Consistency, Safety and Surface GO on
plan SHA-256
`08e198e00c5f6ff697dca6b71f8117ce8963afb91126a30af2b5ea94a6ac6619`
at core `ef29f890`.
Python sends the accepted operation policy: default `jobs=100`,
`max_per_host=32`, `max_retries=3`, with explicit larger values preserved.
Core installs pool per-host caps from the operation, pool total
`max(256, jobs)` and `max_requests=max(1024, jobs)`.
Setup retry classification and backoff stay in Rust. The 9 s stall and
30 s aggregate defaults, zero disabling both network deadlines, and 60 s
idle rule are host clocks. `asyncio.to_thread` and event-wait polling are
scheduling tools, not transport deadlines. Python has no retry loop, jitter,
attempt count, timeout override, or replay of a failed push body.

## 5. Integration and verification

After S1.2 accepts the bounded amendment, S6 implementation lives in
`gwz-py` with the narrow core host API above. Update `gwz-py/RELEASE.md`,
its release script and publish workflow so the release branch uses
`gwz-core = "=1.1.0"` from the registry, with no `git` key or sibling path.
Core's own registry pin brings in the published `gwz-transport` version.
Development checkouts may retain local path pins. Protocol regeneration
runs only for accepted schema changes. Phase 7 activation and Phase 8
registry-only wheel smoke remain separate gates.

Focused tests use the actual native extension and disposable SSH/HTTPS
endpoints. One bridge makes two sequential network operations against the
same endpoint; endpoint instrumentation or sanitized Rust pool facts prove
reuse of the physical session, beyond equal responses. Test both SSH and
HTTPS, a second bridge's independent host, and process isolation. Local-only
status/tag/snapshot succeed before and after an invalid HTTPS proxy/CA
network refusal. Barrier tests race close against first construction and
against admission: close joins in-flight work, a losing constructor never
publishes its runtime, no request starts after close, each constructed host
shuts down exactly once, and no later helper or credential access occurs.
Close before first use returns the no-host snapshot. A network or local-only
call after close gives the typed closed error. Await `Client.close()` twice
and compare the retained `TransportCleanup`; context-manager exit follows
the same close path. An overlapping direct native call is refused by Rust;
Python lock queue cancellation leaves the active operation intact. Cancelling
an admitted setup or stream ends exactly that request, finishes cleanup, and
allows a later operation to use the host. Exercise local and installed
in-process `cli` placement and refusal of unbound explicit `cli`. A gh
failure, unsupported proxy, host-key/identity refusal and an injected
secret-bearing helper failure preserve codes while Python-visible fields
contain no credential material. Verify another Python thread progresses
while native construction, Git work and close block. A close test asserts
the returned cleanup facts, including a nonzero pending-work case. Cancel an
active operation by its public operation ID and verify the returned cleanup
snapshot is observable only after finish; repeat on the latest completed ID.
A wrong, foreign or expired ID must fail without cancelling the active
operation, and no unbounded completed-cancellation registry may accumulate.

Candidate correction (2026-09-24, pending review): the
[concurrent-session design](../../dev-docs/GwzPyTransportConcurrencyDesign-1.md)
would replace the one-active-operation and Python network-lock rules above
with session-owned operation records, bounded overlapping work, equal-capacity
admission and independent cancellation. It would also refuse explicit Python
CLI placement before endpoint or credential work. The
[caller guide](GwzPyConcurrentOperations.md) is a draft surface, not an active
API. The historical design remains the current implementation contract until
the correction has review GO and passes a separate implementation gate.
That correction would also narrow the §2 post-Closing rule: new work and
operation-record lookups refuse, but repeated `close()` can read its retained
cleanup report. Before Closing, a completed result remains available for up
to 15 minutes unless released; callers must inspect it before close.

Review must catch a second host constructed by `shims.rs` for the next
operation, lost cancellation during a blocking handler, and accidental
credential/proxy parsing on local-only operations. New conditional platform
sections use explicit `cfg_if!` boundaries, and syntax-aware checks inspect
disabled branches as required by workspace policy.
