# gwz-py on the per-operation transport entry — design for 1.1.0

Date: 2026-10-01. Status: **draft. The operator decided OD14 on 2026-10-01 and directed that this design be skim-reviewed for obvious issues, without the full review loop**.
- The [skim review](../../gwz-core/dev-docs/GwzTransportReleasePlanAmendment-2-ReviewSkim.md) found six P2 and three P3 text defects, and all are applied. Its [re-check](../../gwz-core/dev-docs/GwzTransportReleasePlanAmendment-2-ReviewSkim-1.md) reported GO with every finding closed. It filed four new P3s, which are applied as it specified.
- Erratum, 2026-10-02: §2.6's first bullet gives as its reason that an operation's thread needs the GIL to build its Python error. Since gwz-py `a671054` that error is lazy: it is built only where Python raises or reads it (`native/src/error.rs`), so no operation's thread attaches to build it. The rule stands for a narrower reason: a failed `submit`'s worker still takes the GIL to render its error's text into the record that `operation_result` waits for, so a wait that held the GIL could deadlock with that worker. §2.6 is left as reviewed.

## Decision and scope

- **The decision.** On 2026-10-01 the operator chose OD14's alternative ([release plan amendment 2](../../gwz-core/dev-docs/GwzTransportReleasePlanAmendment-2.md)): in 1.1.0, gwz-py's network operations take the transport. Each runs inside its own per-operation runtime, through gwz-core's `with_local_transport` family, as the `gwz` CLI's commands do.
- **What this design is.** It is the 1.1.0 amendment's S1.1 revision (GwzV110PlanAmendment §3.2): the per-operation meanings of gwz-py's public API that S6.2 and S6.3 point to.
  - OD1 retired S1.1 unwritten, and amendment 2 (§3.17) restores S6.1–S6.3 for 1.1.0.
  - The operator's decision and the skim review stand in for S1.2's GO.
  - S7.5 (1.1.0)'s Surface review covers the documented behaviour change, the environment's capture point (§2.8).
- **What it supersedes.** It supersedes [`GwzPyTransportDesign.md`](GwzPyTransportDesign.md) for 1.1.0. That design's long-lived `TransportSession` carried the NO-GO of 2026-09-23, and was removed at gwz-py `a342b95`.
- **In 1.1.0:**
  - one runtime per network operation;
  - cancellation of a waiting or running operation;
  - `Client.close()` and interpreter exit, which cancel or join;
  - at most 8 network operations at once per `Client`;
  - the transport route on macOS ARM64, Linux x86-64 and Windows x86-64.
- **Not in 1.1.0.** These are 1.2.0's, through the session host:
  - connection reuse across operations;
  - one runtime per process;
  - `SocketCoreBridge` and `gwz-py server`.

## 1. The tree today

- **Two native entries.** The extension exposes module-level functions only (`native/src/lib.rs`). `call` and `submit` both release the GIL (`py.detach`) and dispatch by method name.
  - `submit` returns an "accepted" response.
  - It runs the operation on its own `gwz-py-operation` thread, inside `catch_unwind` (`native/src/dispatch/mod.rs`, `spawn_call`).
- **Operation records.** Events and results live in `operations::STORE`, a static `OnceLock` (`native/src/operations.rs:10`).
- **The error path takes the GIL.** An operation's thread acquires the GIL to build its Python error (`native/src/error.rs:40`).
- **The backend.** Every handler gets its backend from `shims::with_backend`, which is `Git2Backend::new()`, with no host context (`native/src/shims.rs`).
  - After amendment 2's TR2.11, a backend without a host context takes the native route.
- **The bridge** (`src/gwz/bridge.py`).
  - `NativeCoreBridge` decides which calls are network calls with `_needs_transport`: `init_from_sources`, `materialize`, `clone_workspace`, `clone_repo_member`, `attach_repo_member`, `pull_head`, `pull_snapshot`, `push`, `fetch`, and `tag` when it pushes or fetches, or lists or deletes on a remote.
  - It serializes those calls per event loop with a "legacy" `asyncio.Lock`.
  - Both entries run through `asyncio.to_thread`.
  - `close()` returns `TransportCleanup(0, False)`.
  - `cancel_operation` raises `UnsupportedOperation`.
- **gwz-cli's set** (`globalargs/dispatch.rs`, `transport_meta`) has `repo_sync` and lacks `attach_repo_member`.
  - gwz-core's production `with_transport` call sites number nine: fetch, init_from_sources, clone_repo_member, tag, push, pull_head, pull_snapshot, materialize and clone_workspace.
  - Neither `attach_repo_member` nor `repo_sync` calls it, so each driver includes one method too many today.
- **`with_local_transport`** (gwz-core `src/transport_host/local_command.rs`):
  - It builds a runtime from the process environment (`std::env::vars_os()` and `SshEndpointConfig::from_environment()`), runs the action, then finishes and shuts down.
  - The process-wide runtime configuration that `configure_transport_runtime` sets also feeds it, through `transport_support::server_timeout_ms` (`transport_host/mod.rs:66`).
  - It has two gaps that matter in a host process, and not in a CLI process:
    - **Panics.** If the action panics, `Command`'s `Drop` runs `finish()`, a `block_on`, while unwinding. A second panic there aborts the process, and the process is the Python interpreter.
    - **Environment.** It reads the environment on the operation's thread, after the GIL is released, where it can race with `os.environ` writes from Python threads. `putenv` takes no Rust lock.

## 2. The stage

### 2.1 Which operations

- One predicate in gwz-core decides whether a request is in transport scope, for both drivers. It equals gwz-core's production `with_transport` call sites, and a source test pins the two equal.
- The bridge's `_needs_transport` is pinned to the native predicate's method set by a test. That drops `attach_repo_member` from it, and drops `repo_sync` from gwz-cli's set.

### 2.2 The entry (1.1.0 S6.1, extended)

A variant of `with_local_transport` takes three things from its caller:
- **an environment snapshot.** The runtime then reads no process environment: `SshEndpointConfig`, `HttpsEndpointConfig` and the TLS and proxy configuration are all built from the snapshot. The SSH home follows amendment 2's TR1.8 order on Windows.
  - The process-wide runtime configuration that `configure_transport_runtime` sets is gwz state, not environment. The runtime reads it once, at its start.
- **the operation's metadata and ID;**
- **a cancellation token,** as S6.1 states it: it refuses with `Cancelled` once the token is cancelled, and otherwise builds, runs, finishes and shuts down.

**Library safety, as S6.1 states it:**
- The variant catches a panic in the action first.
- It runs finish and shutdown under their own panic guard, and never finishes from `Drop` while unwinding.
- It reports failure with cleanup unconfirmed.

gwz-cli keeps `with_local_transport`, whose snapshot is `std::env::vars_os()` at the command's start.

**The off switch** (amendment 2's TR1.5 and TR2.5) governs gwz-py through its environment form and its user-configuration form, resolved from the snapshot and the user configuration at the operation's start. The flag form is the CLI's only.
- With the switch on, a gwz-py network operation takes the native route and never builds the variant's runtime.
- TR1.5 states gwz-py's form of the first-operation notice. The default is one Python warning per process, through the `warnings` module.

### 2.3 The environment snapshot

- gwz-py takes the snapshot at the native entry, in `call` and `submit`, while it still holds the GIL. That is before `py.detach`, and for `submit` before the operation's thread is spawned.
- It takes one only for a request in transport scope.
- A Python thread that writes `os.environ` needs the GIL, so it cannot race the capture.

### 2.4 The per-`Client` host object, the limit and threads

- **The host object.** `NativeCoreBridge` constructs one new native object, a `#[pyclass]` called `ClientHost`, for each `Client`. It is not a static and not a thread-local. It owns:
  - the limit of 8 network operations;
  - the `Client`'s cancellation tokens (§2.5);
  - the running aggregate of its operations' cleanups (§2.7).

  Operation records stay in `operations::STORE`.
- **The limit.** `ClientHost` counts network operations from both `call` and `submit`. The bridge's per-event-loop lock is removed, because the limit is per `Client`, not per loop.
- **Where a waiting operation waits.** This answers S1.1's question.
  - A ninth `submit` returns its accepted response at once. The operation then waits for a slot on its own `gwz-py-operation` thread, so it holds that native thread while it waits.
  - A ninth `call` waits on the `asyncio` default-executor thread that runs it, and holds that thread.
  - A waiting operation can be cancelled. It is then refused with `Cancelled`, and builds no runtime.
- **No sharing.** Each running operation owns its runtime: an executor on the operation's thread, the SSH worker, the HTTPS thread, the placement threads and the reaper. It also owns its own pool. Nothing is shared, and no connection is reused across operations.
- **Connections.** A running operation opens connections as the CLI's commands do, within its own per-host limit. Eight overlapping operations can therefore open eight times that limit to one host, as today's native path can.
- **Recorded:** the construction cost and connection counts for 1, 2 and 8 overlapping operations (S6.3), for S7.2 (1.1.0)'s notes.

### 2.5 Cancellation

`Client.cancel_operation(operation_id)` looks the operation's token up in `ClientHost`, never in `STORE`:
- **A waiting operation, or one not yet started,** is refused with `Cancelled`.
- **A running operation:** the entry cancels its request, and returns the operation's `TransportCleanup`.
- **A wrong, foreign or completed operation, or a non-network one,** fails without cancelling anything. The record of completed cancellations stays bounded.

### 2.6 Close and interpreter exit

- **No wait holds the GIL.** Every wait in `close()`, in `cancel_operation` and in the `atexit` hook runs with the GIL released (`py.detach`), bounded by the cleanup bound. An operation's thread needs the GIL to build its Python error, so a wait that held the GIL would deadlock with an operation that is failing.
- **`Client.close()`** cancels the `Client`'s waiting and running network operations, and joins them up to the bound. Its `TransportCleanup` counts what was cancelled, and reports cleanup as unconfirmed for any operation that has not finished. `close_report` keeps that report.
- **Interpreter exit.** Each `ClientHost` registers its own exit callback with `atexit` when it is created, holding only a weak reference to the host, and unregisters it on close.
  - gwz keeps no registry of hosts, so a dropped `Client` leaks nothing but that callback.
  - At exit, the callback closes the host the same way.
- **An operation that outlives the bound,** at close or at exit, records its outcome in its `ClientHost` without attaching to the interpreter, so finalization never waits on it.
- **Neither leaves a helper process behind** (S6.3).

### 2.7 Results and cleanup

- An operation's result carries its transport observations, as the CLI's responses do.
- `ClientHost` keeps a running aggregate, not a per-operation record: the number of operations whose cleanup was not confirmed, and their pending local work. `close()`'s `TransportCleanup` adds that aggregate in. The only per-operation record is §2.5's bounded record of completed cancellations.
- 1.1.0 adds no protocol field.

### 2.8 The public API under the per-operation model (S1.1's list)

- **`TransportCleanup`** keeps its shape. `cancel_operation` returns one operation's report, and `close()` returns the `Client`'s report (§2.5–§2.7).
- **`Client.close` and `close_report`:** §2.6.
- **`Client.cancel_operation`** becomes available for network operations (§2.5). Today it raises `UnsupportedOperation`.
- **`transport_capabilities`** reports what the ordinary build provides. After S7.1 (1.1.0), that is the transport in the `local` placement and no session route, which S7.3 (1.1.0) asserts.
- **`configure_transport_runtime` stays process-wide.** Two `Client`s in one process share its setting, and each operation's runtime reads it at its start.
- **`Client.meta(max_retries)`**, which the contract's §14 list names, does not exist in gwz-py today, and 1.1.0 adds no Python form of it. A Python operation's runtime applies the retry plan's default budget (TR2.1). The per-request form comes with the session host, in 1.2.0.
- **Kept from the contract's §14 list:**
  - isolation from invalid proxy or CA settings, refused before any connection opens;
  - the file-identity preflight;
  - gh-only HTTPS, with Python-visible errors sanitised;
  - the release, registry-pin and credential-hygiene checks (TR3.4).
- **Changed in documented behaviour.** The environment is captured at each operation's start (§2.3), and is stable only within that operation. A change to the process environment takes effect at the next operation. It is no longer stable for the life of a `Client`.
- **Across `Client`s,** the process-wide helper caps remain the only bound.

### 2.9 Windows

The entry's Unix and Windows arms sit under the candidate switch, matching 1.1.0 S4.5. S6.3's dabeest rows wait on S4.5. Each includes one row with `HOME` unset.

## 3. Tests (1.1.0 S6.3, extended)

The tests run on macOS ARM64, Linux x86-64 and dabeest, against the disposable SSH and HTTPS fixtures. Each network test asserts through its result's transport observations that it took the transport route.
- S6.3's nine rows, as the 1.1.0 amendment's §3.4 states them.
- S6.1's core unit tests:
  - a cancel before the start, and a cancel while running, each with its cleanup report;
  - a panic injected into finish after a panic in the operation. The process stays alive, and the next operation succeeds.
- **No deadlock.** `close()` with a failing operation in flight returns within the bound.
- **The limit counts both entries:** with 8 `call`s and `submit`s running, a ninth of either kind waits, and can be cancelled while it waits.
- **The off switch:** with its environment form on, a gwz-py network operation takes the native route and builds no runtime.
- **Exit with an operation that outlives the bound:** the process exits cleanly, and no helper process remains.
- **The predicate:** the source test of §2.1, and the bridge's method-set test.
- **The snapshot:** a runtime built from a snapshot reads nothing from the process environment, and a change made after capture is not seen.

## 4. What 1.2.0 replaces

In 1.2.0 the session host replaces this stage:
- one runtime per process, and reuse across operations;
- the session's cancellation and close;
- the environment snapshot at `open`.

gwz-py's public API stays as this design leaves it, apart from `SocketCoreBridge`.
