# Concurrent network operations with one Python Client

Status: **REJECTED with the previous concurrency design; these methods and concurrent behavior are not active.** The operator authorized a new [session v2 caller guide](GwzPyConcurrentOperationsV2.md) whose method timing and close semantics replace this historical draft.

One `Client` will be able to run independent fetch, push, pull, clone and other network operations at the same time. The operations share the Client's Rust transport host and SSH/HTTPS connection pools. Each operation has its own result and cancellation; cancelling one does not cancel another. A physical connection still runs only one exchange at a time. The same rules apply when tasks use different Python threads with the same Client.

For a result without a separate handle, keep using `await client.fetch(...)` or `await client.push(...)`. For progress only, keep using `fetch_stream(...)` or `push_stream(...)`. To obtain an operation ID before the first progress event and cancel it from another task, use the matching `start_*` method:

```python
import asyncio
from gwz import Client, GwzOperationError


async def main():
    async with Client(root="/work/ws") as client:
        a, b = await asyncio.gather(
            client.start_fetch(targets=["mem_a"]),
            client.start_fetch(targets=["mem_b"]),
        )
        print(a.operation_id, b.operation_id)
        await a.cancel()                 # A's terminal result is now stable; B keeps running.
        try:
            await a.result()
        except GwzOperationError as exc:
            print(exc.response, exc.effect)  # Inspect the result and possible remote effect.
        b_result = await b.result()      # An OperationResult, after B's cleanup.
        print(b_result.aggregate_status)
        await a.release()
        await b.release()


asyncio.run(main())
```

`start_fetch` and `start_push` return an `OperationHandle` after admission, before Git work finishes or any progress event is required. The same `start_<verb>` form is provided for every network command with a stream form. A handle exposes `operation_id`, `request_id`, `events()`, `result() -> OperationResult`, `cancel() -> TransportCleanup`, and `release()`. Each `events()` iterator replays retained events from the beginning, in order; multiple iterators and `result()` are independent, and events need not be drained before `result()` completes. `result()` raises `GwzOperationError` with an `OperationResult` in `response` on failure. `cancel()` joins transport cleanup and terminal publication; its returned report is stable and repeated cancellation returns the same report while the record is retained. If the operation completed first, `result()` still returns its success. Otherwise it raises `GwzOperationError` with a `Cancelled` result and `effect="possible"` on that Python exception; a cancelled push is not automatically replayable. A terminal event precedes the end of every event iterator. `release()` frees a completed record; a second release reports `OperationExpired`. A handle is valid only with its creating open Client. A caller may pass `request_id="my-id"` to a network method for correlation, but two Clients with that same request ID still receive different operation IDs. One Client cannot reuse a request ID already registered in its current host generation, even after completion; that is refused with `InvalidRequest` before admission. An ID from another Client is refused.

The worker and capacity settings are:

| Setting | Python location | Default | Meaning |
| --- | --- | --- | --- |
| Per-operation workers | `concurrency=` on each network method | 100 | Positive upper bound for that operation, within the Client-wide 128-worker ceiling. |
| Per-host physical connections | `max_connections_per_host=` on each network method | 32 | Positive limit shared across this Client's operations for one host. |
| Total physical connections | Derived from `concurrency=` | `max(256, concurrency)` | Pool-wide ceiling; a larger explicit `concurrency` can alter physical capacity even though actual workers remain capped at 128. |
| Pool checkout requests | Derived from `concurrency=` | `max(1024, concurrency)` | Pool checkout ceiling, separate from top-level operation and worker limits. |
| Setup retries | Core default in the current Python API | 3 extra retries | Four total setup attempts; body failures are not retried. No Python `max_retries=` keyword is offered by this draft surface. |

Up to eight top-level network operations and 128 member workers can be live on one Client, with at most 256 queued member work items. The physical pool's per-host and total limits apply to all operations together. For example, two starts with default settings resolve equal physical capacity and may overlap. A request with `max_connections_per_host=16` while a default-capacity operation is live is refused with `GwzBridgeError.code == "TransportCapacityConflict"` before Git work or credentials; retry after the conflicting operations and their cleanup have finished. Exhausting the Client's operation or retained-result budget is refused before admission with `code == "TransportSessionFull"`; release completed handles or wait for work to retire before retrying. A call, a `start_*` method, or the first iteration of a stream helper raises these errors before an accepted operation exists. Once a handle is returned, later failures are reported by `result()` and the operation's event stream.

`await client.cancel_operation(handle.operation_id)` is equivalent to `await handle.cancel()`. Cancelling the Python task that awaits a unary call or stream cancels only its admitted operation, waits for its bounded cleanup, then propagates `CancelledError`. Cancelling a task waiting for admission leaves no accepted operation. A completed operation's events, result and cancellation facts remain available for 15 minutes unless released earlier **and while the Client is open**. An expired same-Client issued ID reports `OperationExpired`; a foreign or never-issued ID reports `InvalidRequest`. A `TransportRecordLimit` can occur **after admission** only when that one operation exceeds its own 2 MiB event or 8 MiB total record limit. It is a `GwzOperationError` whose `response` contains the operation ID, caller request ID and available transport evidence, and whose Python-only `effect` is `"possible"`, including on a unary call with no handle. Do not automatically replay a push: fetch or otherwise inspect the affected remote ref, compare it with the intended target and reconcile the remote state first. The aggregate Client record budget is reserved at admission, so unrelated retained results cannot turn a within-limit success into this error.

`async with Client(...)` exits by cancelling and joining any remaining operations and closing its one transport host. Inspect needed results before exit: once Closing starts, handle and operation-record methods refuse new calls with `InvalidRequest`. `await client.close()` uses the same shared completion: cancelling one close waiter does not cancel shutdown, and a later close returns the same `TransportCleanup`. Closing a Client does not close another Client's host. Local-only commands do not construct the network host. The Python 1.1 Client uses local endpoint placement; if an internal request explicitly asks for CLI endpoint placement, it is refused before credential access with `UnsupportedOperation` (there is no high-level placement keyword yet). SSH credentials remain endpoint-local and HTTPS authentication uses `gh` only. The Client does not expose transport Envelopes, a socket or a pool object.

`Client` and `OperationHandle` methods may be awaited directly from separate Python event loops on different threads; each awaitable belongs to the loop that created it, and callers must not move a pending `asyncio.Task` between loops. For example, thread B can run `asyncio.run(client.start_fetch(targets=["mem_b"]))`, return that handle to thread A, and thread A can then `await handle.cancel()`, `await handle.result()` and `await client.close()` on its own loop. Each method crosses the native session's synchronization boundary; no operation is bound to the loop that created the Client. An event iterator itself is consumed on one loop, while another iterator can be created on a second loop for the same retained record.
