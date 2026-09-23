# Concurrent network operations with one Python Client

Status: **DRAFT caller guide for the new session v2 design, 2026-09-24. These methods and concurrent behavior are not active in the current candidate.**

One `Client` can run several independent network operations at once. They share its native SSH/HTTPS host and physical connection pools. Each operation has its own ID, events, result and cancellation. A physical connection still runs one exchange at a time; cancelling one operation does not cancel another.

The handle-returning methods are synchronous: they give you an ID **before** admission can start Git or credential work. Then explicitly await admission:

```python
import asyncio
from gwz import Client, GwzOperationError


async def main():
    async with Client(root="/work/ws") as client:
        a = client.start_fetch(targets=["mem_a"])
        b = client.start_fetch(targets=["mem_b"])
        await asyncio.gather(a.accepted(), b.accepted())
        print(a.operation_id, b.operation_id)

        await a.cancel()  # Joins A's cleanup and terminal result; B keeps running.
        try:
            await a.result()
        except GwzOperationError as exc:
            print(exc.response, exc.effect)

        b_result = await b.result()
        print(b_result.aggregate_status)
        async for event in b.events():  # Retained events replay even after result().
            print(event.kind)
        await a.release()
        await b.release()


asyncio.run(main())
```

Every handle supports `accepted()`, `events()`, `result()`, `cancel()` and `release()`. `accepted()` returns the handle once the operation is fully admitted; it does not mean Git succeeded. `events()` and `result()` require that admission has been attempted; they never secretly start work. After a pre-effect admission refusal, `result()` raises the same retained `GwzBridgeError` and `events()` ends without Git events. Use `async for event in handle.events(): ...`—it is an asynchronous iterator, not an awaitable result. Each event iterator replays that operation's retained events from the beginning; multiple iterators are independent of each other and of `result()`. One primary reader is reserved **before** acceptance, so the first iterator cannot run out of aggregate reader capacity after effects begin. Additional optional iterators may receive `TransportSessionFull` if their bounded cursor metadata cannot fit; that does not change the operation outcome. A terminal event precedes normal iterator end. If a reader reaches its 15-minute record deadline first, it raises `OperationExpired`. `result()` returns an `OperationResult` or raises `GwzOperationError` whose `.response` is that result. `cancel()` joins transport cleanup and result publication; repeat calls return the same cleanup facts while retained. `release()` removes a terminal, refused or unstarted record; releasing an active operation reports `OpenOperation` until it has finished or been cancelled and joined. A second release reports `OperationExpired`. An unstarted handle can be cancelled or released without endpoint effects.

| Existing stream method | Handle factory | Operation arguments |
| --- | --- | --- |
| `init_from_sources_stream` | `start_init_from_sources` | Same as existing stream method |
| `clone_workspace_stream` | `start_clone_workspace` | Same as existing stream method |
| `clone_repo_member_stream` | `start_clone_repo_member` | Same as existing stream method |
| `materialize_stream` | `start_materialize` | Same as existing stream method |
| `pull_head_stream` | `start_pull_head` | Same as existing stream method |
| `pull_snapshot_stream` | `start_pull_snapshot` | Same as existing stream method |
| `push_stream` | `start_push` | Same as existing stream method |
| `fetch_stream` | `start_fetch` | Same as existing stream method |

For the common cases, `client.start_fetch(targets=["mem_app"])` selects one member; `client.start_push(refspec="main:main", targets=["mem_app"])` pushes a refspec; and `client.start_clone_workspace("git@github.com:owner/workspace.git", "/work/new")` clones a workspace. Each returns its handle immediately; then use `await handle.accepted()`, `async for event in handle.events(): ...`, `await handle.result()`, and `await handle.release()`. `start_push` also accepts optional `remote=` and `remote_check=`; omitting `remote` uses the selected member's configured push remote (or, for the workspace root, its first configured push-capable remote), and omitting `remote_check` uses changed-only checking (`RemoteCheck.changed`; `RemoteCheck.always` checks every remote); `start_clone_workspace` requires `url` and `target`; `start_clone_repo_member` requires `url` and optionally `member_path`, `member_id`, and `source_id`. The handle factories accept the same operation arguments as their stream counterparts.

The existing `await client.fetch(...)`, `await client.push(...)` and `fetch_stream(...)`/`push_stream(...)` convenience forms keep their operation arguments. A stream helper now returns an `OperationStream` asynchronous iterator with public `operation_id`, `request_id`, `result()` and `aclose()`; it still works with `async for`. Use a `start_*` handle when another task needs the operation ID before progress starts. A caller-provided `request_id=` is a correlation label, separate from `operation_id`. It must be nonempty, at most 128 UTF-8 bytes and contain no Unicode control characters, matching core registration; an invalid ID raises `GwzBridgeError(code="InvalidRequest")` synchronously, before an operation ID or endpoint is created. Two Clients may use the same request ID and get distinct operation IDs. Within one Client's current core generation, a request ID **successfully registered with core** cannot be reused, even after completion; `accepted()` refuses it with `InvalidRequest` before effects. A pre-registration capacity or session refusal does **not** consume it: create a fresh handle and retry the same request ID after the conflict clears. A failed worker launch **after** successful core registration does consume it, so that retry needs a new request ID or a later core generation. A fresh generated request ID is used when you omit it.

| Setting | Python location | Default | Meaning |
| --- | --- | --- | --- |
| Per-operation workers | `concurrency=` on each network method | 100 | Positive upper bound for that operation, within the Client-wide 128-worker ceiling. |
| Per-host physical connections | `Client(max_connections_per_host=...)`; optional per-operation override | 32 | Positive shared physical limit for one host. An omitted operation setting inherits the Client value. |
| Total physical connections | Derived from `concurrency=` | `max(256, concurrency)` | Shared pool ceiling. |
| Pool checkout requests | Derived from `concurrency=` | `max(1024, concurrency)` | Pool checkout ceiling, separate from the eight-operation limit. |
| Setup retries | Core policy default | 3 extra retries | Four total setup attempts; failures during the Git body are not retried. The current Python metadata has no `max_retries=` argument. |

A Client admits at most eight live top-level network operations, 128 member workers and 256 queued member items. Up to 64 issued-but-unreleased records and 64 MiB of charged results/events/reader metadata can be retained. Each unstarted record reserves 4 KiB for identity and its required discovery descriptor. Admission upgrades that reservation to an 8 MiB total allowance including 4 KiB for required identity, discovery and possible close-summary metadata, so eight full admissions fit exactly within 64 MiB. Required `recent_operations()` descriptors and close summaries use their reserved charge after effects; optional extra retained copies may refuse. A close summary stays charged until Client drop even if its operation result expires. Creating a 65th unstarted handle raises `TransportSessionFull` synchronously. A request reserves up to 8 MiB of its own result capacity **including its first event reader** before effects. For example, `Client(root="/work/ws", max_connections_per_host=16)` lets overlapping fetch and push calls that omit the operation keyword both inherit 16 and share that pool. An explicit override to 32 while the 16-capacity operation remains live—even briefly between physical leases—raises `GwzBridgeError(code="TransportCapacityConflict")` before Git or credential effects. Create a new handle and retry after the conflicting operation and cleanup retire; a pre-registration capacity refusal permits reuse of the same explicit `request_id=` on that new handle. If an operation slot or result ledger is full, the pre-effect error is `TransportSessionFull`; call `client.recent_operations()` and release completed records or wait for expiry. A pre-effect refusal leaves the handle with its typed failure and effect `none`; it can be released but cannot be admitted again. `submit()` returns `Accepted` only after the same admission prerequisites. A stream helper reports admission refusal on its first iteration; a unary call reports it before performing Git work.

Cancellation after acceptance can race remote success. If success won, `result()` still returns it. Otherwise it raises `GwzOperationError` whose `.response` has typed `GwzErrorCode.cancelled` and whose Python-only `.effect` is conservatively `"possible"`. A result larger than that operation's own record limits fails with typed `GwzErrorCode.transport_record_limit` and the same possible-effect classification. The exception retains `operation_id`, `request_id`, nonsecret transport facts and the failed result even for a unary call. **Do not replay a possible-effect push automatically**: inspect the affected remote ref, compare it with the intended target, and reconcile before retrying. The full per-operation record allowance is reserved at admission, so unrelated retained results cannot turn a within-limit success into a possible-effect record-limit failure.

Cancelling a task awaiting `handle.accepted()` leaves the previously returned handle available. After cleanup, `GwzOperationCancelled` (a subclass of `asyncio.CancelledError`) has `.handle`, `.operation_id`, `.request_id`, `.response: OperationResult | None`, and `.effect`. Its `.response` is the detached typed terminal result after acceptance, or `None` before admission; its effect is respectively `"possible"` or `"none"`. You may still inspect and release the handle. If you cancel an existing unary or stream helper task, the same exception carries a **detached** `.response` and identity; its internal ledger record is released so repeated task cancellations cannot exhaust retention. Catch that exception if you need to decide whether a push may have taken effect. `await client.cancel_operation(handle.operation_id)` has the same cleanup semantics as `await handle.cancel()`.

If you stop consuming a stream early, keep its `OperationStream` object and call `outcome = await stream.aclose()`: this cancels and joins the operation, returning both IDs, terminal status/code and conservative effect. Its full result remains available through `await client.operation_result(stream.operation_id)`. If the stream object is dropped, the Client ledger retains the result; `client.recent_operations()` lists up to 64 IDs/status/effects (including early-abandoned streams), and you can inspect or release them by ID. Dropping a stream asks the native cancellation token to stop work but does not silently erase its outcome. If 64 retained records fill the Client, new work refuses before effects until you inspect and release completed records or their 15-minute deadlines expire.

On the native bridge, `await client.close()` refuses new Git work, cancels and joins every active operation, and shuts its physical host once. It returns a `TransportCleanup` with physical cleanup facts and at most eight compact operation summaries for work live when close began, including IDs, terminal code/status and possible effect. The same report is available as `client.close_report` after `async with` exits and from a later `close()` call. The public return type remains `TransportCleanup | None`: a custom bridge without a `close` hook returns `None` and leaves `close_report=None`. Custom bridges retain their existing call/stream methods; the new handle and ledger APIs require native session hooks and otherwise refuse `UnsupportedOperation`. **Closing does not erase retained results.** You can still use a retained handle or `client.operation_result(id)`, `operation_events(id)`, `cancel_operation(id)`, `release_operation(id)` and `recent_operations()` after close; these read only the ledger and never reopen the host. Dropping a handle or stream does not release an accepted record: it remains discoverable by ID through its 15-minute deadline. The compact close summary remains available for work live at close. An event iterator waiting through close wakes for its terminal event and can drain it. Results expire 15 minutes after terminal publication (unstarted handles after creation), including after close. A handle can keep its ledger available after the Client is dropped, but cannot restore expired data. Repeatedly cancelling a Python close waiter does not interrupt native shutdown; cleanup finishes before cancellation propagates.

`Client` and handle methods may be awaited on different event loops in different threads; each individual pending `asyncio.Task` stays on the loop that created it. For example, thread B may run `asyncio.run(handle.accepted())` on a handle created in thread A, then thread A may await `handle.cancel()`, `handle.result()` and `client.close()` on its loop. Each event iterator is consumed on one loop; another iterator may be created on a different loop. The Python Client supports local endpoint placement only; an internal request for explicit CLI placement is refused before credentials with `UnsupportedOperation` (there is no high-level placement keyword yet). SSH credentials stay at the endpoint and HTTPS authentication uses `gh` only. The Client exposes no transport Envelope, socket or pool object.
