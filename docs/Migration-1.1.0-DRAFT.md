# gwz-py 1.1.0: migration notes

Status: **DRAFT, 2026-10-17** (plan step C5 / S7.2). It is finalized after the switch removal (C4) and the acceptance matrix (S5.6), and it moves into the release notes of `gwz-py` when 1.1.0 is cut. Items marked *pending* wait on a number or a decision. Nothing here is a release claim until the marker is gone.

1.1.0 makes gwz's own SSH and HTTPS transport the default for every network operation of the Python API and the `gwz-py` command, on macOS (ARM64), Linux (x86-64) and Windows (x86-64). The Python package calls the same `gwz-transport` pool as the Rust `gwz` command; there is no second pool in Python. The Rust command's [1.1.0 release entry](https://github.com/owebeeone/gwz-cli/blob/main/docs/Releases.md) lists what changes for every user (defaults, the retry and timeout behaviour, the `Capacity` error, the Windows differences); this page lists what is specific to Python.

## 1. Code that constructs protocol objects must pass the new arguments

The generated protocol classes are slotted dataclasses with no defaults, so a constructor call that does not name a new field raises `TypeError`. This is the shape every earlier additive change had (`url_scheme`, `invocation`). It affects only code that builds these two classes itself; `Client` methods build them for you.

| Class | New field(s) | Type |
|---|---|---|
| `OperationPolicy` | `max_retries` | `int \| None` |
| `TransportObservation` | `endpoint_id`, `connection_id`, `stream_id`, `reused` | `str \| None`, `str \| None`, `int \| None`, `bool \| None` |

Before:

```python
policy = OperationPolicy(
    partial=None, destructive=None, sync=None, unsupported_member=None,
    remote=None, concurrency=None, progress_min_interval_ms=None,
    max_connections_per_host=None,
)
```

After (pass `None` for "not set"; `None` keeps the transport's default of 3 extra setup attempts):

```python
policy = OperationPolicy(
    partial=None, destructive=None, sync=None, unsupported_member=None,
    remote=None, concurrency=None, progress_min_interval_ms=None,
    max_connections_per_host=None, max_retries=None,
)
```

A `TransportObservation` you build for a test or a fake host needs the four receipt arguments (`None` for each when you have no transport receipt to report).

What `max_retries` does: it is the number of extra attempts after a failed setup, the same setting as the Rust command's `--max-retries`. The `Client` methods send `max_retries=None`, so the default of 3 applies; the `gwz-py` command has no `--max-retries` option and `Client` has no parameter for it. *(If a Python setting is wanted it is a 1.1.0 scope question for the operator; none is built.)* The native route ignores it, and merge refuses it, as merge refuses every policy field it does not act on.

## 2. `--json` and `--jsonl` rows gain four keys

`gwz-py --json` and `--jsonl` render protocol objects with `dataclasses.asdict`. A `meta.transport` row (one per remote authentication attempt) therefore gains four keys: `endpoint_id`, `connection_id`, `stream_id` and `reused`. All four are always present in `gwz-py`'s output, and `null` when absent. The Rust `gwz` command builds its rows from a fixed field list and its JSON does not change, so a script that reads both commands' output must treat the four keys as optional.

What the four receipts mean when they are not null: which endpoint, connection and stream carried the row's open, and whether the connection was reused from the pool.

**Absence rule (use this wording).** The four receipts are absent unless the transport route reported an open for the row. They are therefore absent on the native route, on a transport-route row whose setup failed or was refused before the open, for remotes the transport does not carry (`file://` and local paths, `git://`, and Windows `http://`), and in an older writer's map. `null` does not mean "native route"; read `credential_method` and the route-selection setting for that.

**HTTPS limitation.** One request row keeps only the last open's receipts: the endpoint reports an open once per libgit2 request, so a fresh connection for the GET followed by a reused connection for the POST reports `reused: true`. This is a limitation, not a defect to work around.

## 3. Choosing the transport, and the defaults

Each network operation reads `GWZ_TRANSPORT` (`gwz` or `native`) when it starts. With the variable unset, `gwz.transport` in `~/.gitconfig` and `$XDG_CONFIG_HOME/git/config` selects the transport; the default is `gwz`. `Client` takes no transport parameter. Changes apply to the next operation, including operations on an existing `Client`. The README's "Choosing the network transport" section has the commands, the include and `includeIf` rules and the warnings and log records (a native selection emits a `UserWarning`; a throttled or waiting host emits a `WARNING` record on logger `gwz`).

Defaults that changed:

| Setting | 1.0.17 | 1.1.0 on gwz's transport | 1.1.0 with native selected |
|---|---|---|---|
| concurrency (jobs) | 50 | 100 | 50 |
| connections per host (`Client(max_connections_per_host=...)`, `--max-per-host`) | 8 | 32, and a ceiling: gwz may use fewer when a host refuses | 8 |
| timeout clock | libgit2's, 3 seconds | 9 seconds, process-wide | 9 seconds, libgit2's connect and read timeout |

*Pending S5.4: the defaults are confirmed or changed by its measure-then-select note.*

The timeout differs from the Rust command in one place: in Python the process's one clock is 9 seconds by default **on either transport**, because it is set once per process and the transport is chosen per operation (the Rust command defaults to 3 seconds only with `--transport native`). Set it with `await client.configure_transport_timeout(seconds)` before the first operation (`gwz-py --ssh-timeout seconds` in the CLI); zero disables the clock. The clock is fixed once a backend exists, so a later call with another value is refused. For gwz's transport it bounds a stalled SSH setup and a stalled fetch, push or pull body read (on SSH and HTTPS); a setup also has a 30 second budget that the same zero removes.

`Client(max_connections_per_host=None)` leaves the host limit unset. Any explicit positive value, including 32, is honoured, and a per-call value overrides the constructor.

## 4. Interpreter exit waits for a running async `call()` (decision D26)

**The limitation.** When a Python program exits while an awaited `Client` method (`await client.fetch()`, `pull_head`, `push`, `clone_*`, `materialize`, and the other awaited network operations, which run through `call()`) is still running, interpreter exit waits for that operation to finish. CPython joins the threads of the default asyncio executor, which carry async `call()`, before gwz's own exit hook runs, so the hook cannot cancel the operation and bound the wait. The wait is bounded only by the operation's own duration (its stall and aggregate limits still apply), not by the cleanup bound gwz uses elsewhere. Queued `call()` operations are also run in full.

**What is not affected.** The stream forms (`fetch_stream`, `pull_head_stream`, `push_stream`, `clone_repo_member_stream`, `materialize_stream` and the others), which are carried by `submit()`, are unaffected: gwz's exit hook cancels and closes them within the cleanup bound.

**What to do.** Before the program ends, await your operations, or cancel them with `await client.cancel_operation(operation_id)` and close the client (`async with Client(...)` or `await client.close()`) so no `call()` is running at exit. Use a stream form where a bounded exit matters.

**Status.** Stated in 1.1.0, fixed in 1.2.0 (decision D26); the fix is not a 1.1.0 prerequisite. A test row (an async `call()` blocked on a stalled fixture, then interpreter exit, ending within the cleanup bound) belongs to the 1.2.0 fix.

## 5. Other limits that touch Python

- **Credential-helper launches on Windows.** gwz serializes a credential helper's launch with its own other child-process launches so that no other child inherits the helper's pipes. The serialization is per operation: gwz-py gives each running operation its own host, so two operations running at once in one process (`submit` threads, or `call` with the GIL released) are not serialized against each other, and neither are your own program's spawns. `gwz-py log`'s `git rev-list` is not serialized. gwz's sign-in worker launch is covered by a gwz-sspi launch guard (decision D31, *pending gwz-sspi 0.1.2*).
- **A very large blob pushed over SSH may be charged to the stall clock** (a hypothesis, not yet reproduced; a 1 GiB single-blob push row is the test). If a push of a very large object ends with a stream stall, raise the timeout. HTTPS does not have this. *Pending that row.*
- **HTTPS authentication.** HTTPS is authenticated through the user's configured git credential helpers (`gh` among them), asked in their configured order; an `https://` URL that carries a password, a query or a fragment is refused before anything is sent. On Windows a server that offers Negotiate or NTLM is answered with the logon session. Credential material does not appear in Python-visible errors.
- **Unsupported in 1.1.0: hardware security keys, Kerberos, and certificates held by Pageant or by the Windows OpenSSH agent service.** They are unsupported on the same terms as in the Rust command, whose 1.1.0 entry (*Known limits of this release*) has the details and the messages (the Kerberos sign-in words follow decision D48, *pending its landing*). In Python, `GWZ_TRANSPORT=native` selects 1.0.17's route when a sign-in that worked with 1.0.17 fails, and `await client.configure_transport_timeout(seconds)` raises the clock that a hardware key's touch is waited under (9 seconds by default; section 3).
- **A failed `gh` or helper and an unsupported proxy refuse** the operation; neither falls back to the native route.
- **`transport_capabilities`.** `file_identity` and `exact_agent_identity` are answered by the core. On Windows the 1.1.0 answers follow decision D30 (`file_identity` true; `exact_agent_identity` true only after one exact-selection row passes) and are *pending* the final capability change; on every platform an identity option (`--identity`, `--remote-identity`) is checked against `file_identity` before the request is sent.
- **Platforms.** macOS ARM64, Linux x86-64 and Windows x86-64 (wheels). Linux ARM64 and macOS x86-64 are not supported in 1.1.0. Building the extension with Bazel's `@gwz_core_crates` is unsupported until the release's registry pins (decision D27).
- **Remotes that stay native** under the default setting: `http://`, `git://`, `file://` and local paths. The Windows `http://` warning is printed before the first request.

## 6. Windows

The Windows differences from 1.0.17 are the same for Python as for the Rust command (non-ASCII home and key paths, `~/` identities from the same home as `known_hosts`, Digest, a rejected Windows account, redirects, retried SSH drops, the machine proxy, the CA file variables, the Pageant confirmation wait); see the Rust command's 1.1.0 entry, "Windows".

## 7. Documentation to update when this is finalized

These README and help texts still say "in the 1.1.0 transport candidate" and must be reworded when C4 retires the switch (help audit, findings P1 to P3): the README's "Choosing the network transport" section and the final paragraph on the candidate native HTTPS composition (which says the Windows endpoint is not enabled), `gwz-py`'s `TRANSPORT_HELP`, `--jobs` and `--max-per-host` help in `src/gwz/cli_shared.py`, and the `Client` docstring.
