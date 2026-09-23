# Concurrent network operations with one Python Client

Status: **DRAFT caller guide for the Python concurrency design review, 2026-09-24. These methods and concurrent behavior are not active in the current candidate.**

One `Client` will be able to run independent fetch, push, pull, clone and other network operations at the same time. The operations share the Client's Rust transport host and SSH/HTTPS connection pools. Each operation has its own result and cancellation; cancelling one does not cancel another. A physical connection still runs only one exchange at a time. The same rules apply when tasks use different Python threads with the same Client.

For a result without a separate handle, keep using `await client.fetch(...)` or `await client.push(...)`. For progress only, keep using `fetch_stream(...)` or `push_stream(...)`. To obtain an operation ID before the first progress event and cancel it from another task, use the matching `start_*` method:

```python
import asyncio
from gwz import Client


async def main():
    async with Client(root="/work/ws") as client:
        a, b = await asyncio.gather(
            client.start_fetch(targets=["mem_a"]),
            client.start_fetch(targets=["mem_b"]),
        )
        print(a.operation_id, b.operation_id)
        await a.cancel()                 # B keeps running.
        b_result = await b.result()      # An OperationResult, after B's cleanup.
        print(b_result.aggregate_status)
        await a.release()
        await b.release()


asyncio.run(main())
```

`start_fetch` and `start_push` return an `OperationHandle` after admission, before Git work finishes or any progress event is required. The same `start_<verb>` form is provided for every network command with a stream form. A handle exposes `operation_id`, `request_id`, `events()`, `result() -> OperationResult`, `cancel() -> TransportCleanup`, and `release()`. `events()` yields that operation's ordered events. `result()` raises the same structured operation error as the existing stream helper on failure. `cancel()` is idempotent while that operation's record is retained; `release()` frees a completed record and is idempotent. A handle is valid only with its creating Client. A caller may pass `request_id="my-id"` to a network method for correlation, but two Clients with that same request ID still receive different operation IDs. An ID from another Client is refused.

The default per-operation worker setting is 100 (`concurrency=` in Python, `--jobs` in the CLI), `max_connections_per_host` is 32 and `max_retries` is 3. Up to eight top-level network operations and 128 member workers can be live on one Client, with at most 256 queued member work items. `concurrency=` is a per-operation upper bound, not an increase to the Client-wide worker budget. The physical pool's per-host and total limits apply to all of those operations together. Two requests whose resolved physical pool capacities are exactly equal may overlap. A request for a different physical capacity while another operation or lease is live is refused with `GwzBridgeError.code == "TransportCapacityConflict"` before Git work or credentials; retry after the conflicting operations and their cleanup have finished. Exhausting the Client's operation or retained-result budget is refused before admission with `code == "TransportSessionFull"`; release completed handles or wait for work to retire before retrying. A call, a `start_*` method, or the first iteration of a stream helper raises these errors before an accepted operation exists. Once a handle is returned, later failures are reported by `result()` and the operation's event stream.

`await client.cancel_operation(handle.operation_id)` is equivalent to `await handle.cancel()`. Cancelling the Python task that awaits a unary call or stream cancels only its admitted operation, waits for its bounded cleanup, then propagates `CancelledError`. Cancelling a task waiting for admission leaves no accepted operation. A completed operation's events, result and cancellation facts remain available for 15 minutes unless released earlier. A record-limit failure is explicit (`TransportRecordLimit`); the Git effect may be possible, so do not automatically replay a push. An expired same-Client ID reports `OperationExpired`; a foreign ID reports `InvalidRequest`.

`async with Client(...)` exits by cancelling and joining any remaining operations and closing its one transport host. `await client.close()` uses the same shared completion: cancelling one close waiter does not cancel shutdown, and a later close returns the same `TransportCleanup`. Closing a Client does not close another Client's host. Local-only commands do not construct the network host. The Python 1.1 Client uses local endpoint placement; an explicit CLI endpoint placement is refused before credential access with `UnsupportedOperation`. SSH credentials remain endpoint-local and HTTPS authentication uses `gh` only. The Client does not expose transport Envelopes, a socket or a pool object.
