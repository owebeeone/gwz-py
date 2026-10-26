"""The transport route's rows (1.1.0 S6.3, and the design's §3; gwz-py
dev-docs/GwzPyPerOperationTransportDesign.md).

They run against a candidate extension, which ``GWZ_PY_NATIVE_MODULE`` names,
and the disposable loopback SSH and HTTPS fixtures, and each asserts through
its operation's transport observations, or where an open never completed its
transport's own failure, that it took the transport route. Without the
variable they skip. ``scripts/build_candidate_extension.py`` builds the
extension, and ``python run_tests.py --candidate DIR`` builds it and runs the
suite with it named. The rows that need no network fixture are in
``test_client_host.py``; the off switch's seam is a unit test of the route
(``native/src/route/transport_tests.rs``).

On Windows (plan step 5.3, 1.1.0 S4.5) the SSH, HTTPS and overlapping-operation
rows run against the wheel's extension, on the portable ``sshd.exe``
``GWZ_TEST_SSHD`` names (``ssh_fixture.py``); the rows that need a Unix shell
or process group (the stalling server, the fake ``gh`` script, the exit child's
process group) skip there with their reason.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import logging
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from taut.wire import cbor as wire_cbor

from gwz.protocol.generated import TransportOptions
from host_helpers import (
    CLEANUP_BOUND,
    DELAY_VARIABLE,
    call,
    error_code,
    init_request,
    started,
    submit,
    wait_until,
)
from ssh_fixture import WINDOWS, SshFixture, seed_bare_repository

# Candidate wire keys (gwz-core protocol/candidate): OperationResult.transport,
# ResponseMeta.transport and TransportObservation's endpoint receipt.
RESULT_TRANSPORT, META_TRANSPORT = 10, 8
ROW_ENDPOINT, ROW_CONNECTION = 9, 10
# Time for a cancelled operation's own I/O to fail; it would otherwise wait
# out the transport's 9 s stall.
PROMPT = 3.0
# Since TR2.17 the endpoint answers a Cancel on an attached SSH stream, so a
# cancel or close mid-exchange returns, and its operation ends, in tens of
# milliseconds (0.01 to 0.13 s measured on 2026-10-02, on a heavily loaded
# host), where before they waited out the 5 s cleanup bound. 1 s leaves a
# loaded runner room and still fails an entry that waits the bound out again.
PROMPT_CLEANUP = 1.0
PROXY_VARIABLES = (
    "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY",
    "GIT_SSL_CAINFO", "SSL_CERT_FILE", "SSH_AUTH_SOCK",
)


def load(path: str) -> Any:
    loader = importlib.machinery.ExtensionFileLoader("gwz._gwz_core", path)
    spec = importlib.util.spec_from_file_location("gwz._gwz_core", path, loader=loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def candidate() -> Any:
    path = os.environ.get("GWZ_PY_NATIVE_MODULE")
    if not path:
        pytest.skip(
            "GWZ_PY_NATIVE_MODULE names no candidate extension to take the transport route; "
            "run_tests.py --candidate DIR builds one (scripts/build_candidate_extension.py)"
        )
    return load(path)


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No ambient agent, proxy or CA reaches an operation's snapshot."""
    for name in PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(DELAY_VARIABLE, raising=False)
    return monkeypatch


@pytest.fixture
def ssh(tmp_path: Path, clean: pytest.MonkeyPatch) -> Any:
    fixture = SshFixture(tmp_path / "ssh", count=True)
    clean.setenv("HOME", str(fixture.home))
    yield fixture
    fixture.close()


@pytest.fixture
def stalling(tmp_path: Path, clean: pytest.MonkeyPatch) -> Any:
    if WINDOWS:
        pytest.skip("the stalling server is a POSIX shell script that holds its channel open")
    fixture = SshFixture(tmp_path / "stalling", stall=True)
    clean.setenv("HOME", str(fixture.home))
    yield fixture
    fixture.close()


def identity(fixture: SshFixture, path: str | None = None) -> TransportOptions:
    return TransportOptions(
        default_identity=path or str(fixture.identity), remote_identities=[], url_scheme=None,
                                                                              placement=None,
                                                                              endpoint_path_base=None,
    )


def raw_result(native: Any, operation_id: str) -> dict[int, Any]:
    return wire_cbor.loads(bytes(native.operation_result(operation_id)))


def receipts(rows: list[dict[int, Any]] | None) -> list[tuple[str, str]]:
    """The endpoint and connection each row's open was served on."""
    return [(row[ROW_ENDPOINT], row[ROW_CONNECTION]) for row in rows or [] if row.get(ROW_ENDPOINT)]


def error_receipts(error: BaseException) -> list[tuple[str, str]]:
    meta = getattr(error, "response_meta_cbor", None)
    return receipts(wire_cbor.loads(meta).get(META_TRANSPORT) if meta else None)


def test_an_ssh_operation_takes_the_transport_route(
    candidate: Any, ssh: SshFixture, tmp_path: Path
) -> None:
    host = candidate.ClientHost()
    operation = submit(host, init_request(tmp_path, "req_ssh_route", ssh.url, identity(ssh)))
    result = raw_result(candidate, operation)
    assert result[4] == 1, result.get(8)
    seen = receipts(result[RESULT_TRANSPORT])
    assert seen, "the operation took the transport route: its observations carry an endpoint receipt"
    assert ssh.proxy is not None and ssh.proxy.accepted == 1
    assert host.close() == (0, False)


def test_an_https_operation_takes_the_transport_route(
    candidate: Any, clean: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with HttpsFixture(tmp_path / "https", repository=True) as https:
        clean.setenv("GIT_SSL_CAINFO", str(https.ca))
        clean.setenv("HOME", str(tmp_path))
        host = candidate.ClientHost()
        operation = submit(host, init_request(tmp_path, "req_https_route", https.url))
        result = raw_result(candidate, operation)
        assert result[4] == 1, result.get(8)
        seen = receipts(result[RESULT_TRANSPORT])
        assert seen, "the operation took the transport route: its observations carry an endpoint receipt"
        assert https.requests >= 2, "and it reached the fixture over HTTPS"
        assert host.close() == (0, False)


def test_a_throttled_https_host_is_noted_as_a_gwz_log_record(
    candidate: Any, clean: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The note of the adaptive concurrency design's section 9 is a `logging`
    record on `gwz` (Code P3-9): a host that asks for a wait says so, once, at
    WARNING, in words the CLI's handler prints as a `gwz-py: note:` line."""
    with HttpsFixture(tmp_path / "https", repository=True, throttle=1) as https:
        clean.setenv("GIT_SSL_CAINFO", str(https.ca))
        clean.setenv("HOME", str(tmp_path))
        host = candidate.ClientHost()
        with caplog.at_level(logging.DEBUG, logger="gwz"):
            operation = submit(host, init_request(tmp_path, "req_https_note", https.url))
            result = raw_result(candidate, operation)
        assert result[4] == 1, result.get(8)
        assert https.throttled == 1
        asked = [
            record
            for record in caplog.records
            if record.name == "gwz" and "asked gwz to wait 2 s; waiting" in record.getMessage()
        ]
        assert len(asked) == 1, [record.getMessage() for record in caplog.records]
        assert asked[0].levelno == logging.WARNING
        message = asked[0].getMessage()
        assert message.startswith("gwz: ") and f"127.0.0.1:{https.port} asked gwz" in message, message
        assert host.close() == (0, False)


def test_two_overlapping_operations_complete_independently_each_on_its_own_runtime(
    candidate: Any, ssh: SshFixture, clean: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean.setenv(DELAY_VARIABLE, "800")
    host = candidate.ClientHost()
    operations = [
        submit(host, init_request(tmp_path, f"req_overlap_{i}", ssh.url, identity(ssh)))
        for i in range(2)
    ]
    results = [raw_result(candidate, operation) for operation in operations]
    assert [result[4] for result in results] == [1, 1], "both succeed (AggregateStatus ok)"
    seen = [receipts(result[RESULT_TRANSPORT]) for result in results]
    assert all(seen), f"each took the transport route: {seen}"
    endpoints = [{endpoint for endpoint, _ in rows} for rows in seen]
    assert endpoints[0].isdisjoint(endpoints[1]), "each ran on its own runtime's endpoint"
    started_at, finished_at = [r[5] for r in results], [r[6] for r in results]
    assert max(started_at) < min(finished_at), "the two overlapped"
    assert ssh.proxy is not None and ssh.proxy.accepted == 2, "and opened its own connection"
    assert host.close() == (0, False)


def test_runtime_cost_and_connection_counts_for_1_2_and_8_overlapping_operations(
    candidate: Any, ssh: SshFixture, tmp_path: Path
) -> None:
    """Recorded for S7.2 (1.1.0)'s notes: each operation builds its own
    runtime and opens its own connection, and none is reused."""
    host = candidate.ClientHost()
    lines = []
    for count in (1, 2, 8):
        accepted = ssh.proxy.accepted
        begun = time.monotonic()
        operations = [
            submit(host, init_request(tmp_path, f"req_cost_{count}_{i}", ssh.url, identity(ssh)))
            for i in range(count)
        ]
        first_started = wait_until(
            lambda: any(started(candidate, op) for op in operations), 10, "an operation starting"
        )
        results = [raw_result(candidate, operation) for operation in operations]
        elapsed = time.monotonic() - begun
        rows = [receipts(result[RESULT_TRANSPORT]) for result in results]
        connections = ssh.proxy.accepted - accepted
        lines.append(
            f"{count} overlapping: first started after {first_started * 1000:.0f} ms, all done in "
            f"{elapsed * 1000:.0f} ms, {connections} connections, "
            f"{len({endpoint for r in rows for endpoint, _ in r})} endpoints"
        )
        assert [result[4] for result in results] == [1] * count
        assert all(rows)
        assert connections == count
        assert len({endpoint for r in rows for endpoint, _ in r}) == count
    print("\n" + "\n".join(lines))
    host.close()


def test_a_change_to_the_environment_after_an_operation_starts_does_not_affect_it(
    candidate: Any, ssh: SshFixture, clean: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The snapshot is taken at the native entry (design §2.3): `HOME`, whose
    known hosts trust the fixture, is replaced the moment the first operation
    is accepted, and that operation still succeeds; the next one, which
    captures the replacement, cannot trust the server."""
    stranger = tmp_path / "stranger-home"
    (stranger / ".ssh").mkdir(parents=True)
    (stranger / ".ssh" / "known_hosts").write_text("")
    host = candidate.ClientHost()
    # Its identity is `~/key`, which resolves against the snapshot's HOME too.
    (ssh.home / "key").write_bytes(ssh.identity.read_bytes())
    (ssh.home / "key").chmod(0o600)
    first = submit(host, init_request(tmp_path, "req_before", ssh.url, identity(ssh, "~/key")))
    clean.setenv("HOME", str(stranger))
    before = raw_result(candidate, first)
    assert before[4] == 1, before.get(8)
    assert receipts(before[RESULT_TRANSPORT])
    # Its identity is the fixture's key by its absolute path, so only the
    # replaced HOME's known hosts, which trust nothing, refuse it.
    with pytest.raises(RuntimeError) as after:
        call(host, init_request(tmp_path, "req_after", ssh.url, identity(ssh)))
    # A host key the snapshot's known hosts do not trust fails as libgit2's own
    # SSH-class error does on the native route (gwz-core step 3.1, `0caf3190`),
    # a Git command failure and not a remote rejection.
    assert error_code(after.value) == "GitCommandFailed"
    # Its words name the known hosts of the replacement HOME it captured.
    assert "gwz cannot trust the host key" in str(after.value)
    assert "stranger-home" in str(after.value)
    host.close()


def test_a_cancel_during_setup_returns_promptly_with_its_cleanup_report(
    candidate: Any, ssh: SshFixture, tmp_path: Path
) -> None:
    with StallServer() as stall:
        host = candidate.ClientHost()
        operation = submit(
            host, init_request(tmp_path, "req_setup_cancel", stall.url(ssh.user), identity(ssh))
        )
        wait_until(lambda: stall.accepted, 10, "the operation's connection")
        begun = time.monotonic()
        report = host.cancel_operation(operation)
        took = time.monotonic() - begun
        print(f"cancel during setup returned after {took * 1000:.0f} ms with {report}")
        assert took < PROMPT
        assert report == (0, False)
        result = raw_result(candidate, operation)
        assert result[4] == 5, "it failed (AggregateStatus failed)"
        assert "Cancelled" in result[8][0][2]
        host.close()


def test_cancelling_a_running_network_operation_returns_its_cleanup_report(
    candidate: Any, stalling: SshFixture, tmp_path: Path
) -> None:
    """A cancel mid-exchange fails the operation's read at once, and since
    TR2.17 the endpoint answers the Cancel on the attached stream, so the
    entry no longer waits out the host's 5 s cleanup bound: the cancel
    returns the operation's own report, and the call ends, within
    PROMPT_CLEANUP. No pending job is left; peer cleanup is never confirmed
    in 1.1.0."""
    host = candidate.ClientHost()
    with ThreadPoolExecutor(max_workers=1) as threads:
        running = threads.submit(
            call, host, init_request(tmp_path, "req_mid_cancel", stalling.url, identity(stalling))
        )
        wait_until(lambda: stalling.stalled_pids(), 10, "the exchange stalling")
        begun = time.monotonic()
        report = host.cancel_operation("op_req_mid_cancel")
        took = time.monotonic() - begun
        with pytest.raises(RuntimeError) as failed:
            running.result(timeout=CLEANUP_BOUND)
        ended = time.monotonic() - begun
    print(f"cancel mid-exchange returned after {took:.2f} s with {report}; the call ended after {ended:.2f} s")
    assert took < PROMPT_CLEANUP
    assert ended < PROMPT_CLEANUP
    assert report == (0, False)
    assert "Cancelled" in str(failed.value)
    assert error_receipts(failed.value), "it took the transport route"
    host.close()


def test_a_cancel_naming_a_wrong_foreign_or_completed_operation_cancels_nothing(
    candidate: Any, stalling: SshFixture, tmp_path: Path
) -> None:
    host = candidate.ClientHost()
    other = candidate.ClientHost()
    running = submit(host, init_request(tmp_path, "req_held", stalling.url, identity(stalling)))
    foreign = submit(other, init_request(tmp_path, "req_foreign_network", stalling.url, identity(stalling)))
    wait_until(lambda: len(stalling.stalled_pids()) == 2, 10, "both exchanges stalling")
    completed = submit(host, init_request(tmp_path, "req_done"))
    raw_result(candidate, completed)
    for wrong, owner in (("op_req_unknown", host), (foreign, host), (running, other), (completed, host)):
        with pytest.raises(RuntimeError) as refused:
            owner.cancel_operation(wrong)
        assert error_code(refused.value) == "InvalidRequest"
    time.sleep(0.2)
    assert candidate.try_operation_result(running) is None, "the running operation runs on"
    assert candidate.try_operation_result(foreign) is None
    for pid in stalling.stalled_pids():
        os.kill(pid, 0)
    for owner in (host, other):
        owner.close()


def test_close_with_a_running_network_operation_cancels_it_and_counts_it(
    candidate: Any, stalling: SshFixture, tmp_path: Path
) -> None:
    host = candidate.ClientHost()
    running = submit(host, init_request(tmp_path, "req_closing", stalling.url, identity(stalling)))
    wait_until(lambda: stalling.stalled_pids(), 10, "the exchange stalling")
    begun = time.monotonic()
    report = host.close()
    took = time.monotonic() - begun
    print(f"close with a running network operation returned after {took:.2f} s with {report}")
    # The operation ends at once (TR2.17), so close counts it with its own
    # report, and no operation outlives the bound to leave a pending job.
    assert took < PROMPT_CLEANUP
    assert report == (0, False)
    result = raw_result(candidate, running)
    assert result[4] == 5
    assert "Cancelled" in result[8][0][2]


def test_a_gh_failure_and_an_unsupported_proxy_refuse_without_credential_material(
    candidate: Any, clean: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if WINDOWS:
        pytest.skip("the fake gh is a POSIX shell script; S6.3's gh row on Windows is not plan step 5.3's")
    secret = "ghp_FIXTURESECRET0123456789"
    with HttpsFixture(tmp_path / "https") as https:
        gh = https.fake_gh(secret)
        clean.setenv("GIT_SSL_CAINFO", str(https.ca))
        clean.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
        clean.setenv("HOME", str(tmp_path))
        # gh is invoked only when Git's conventional configuration names it.
        # Clear inherited helpers before selecting this disposable fixture.
        (tmp_path / ".gitconfig").write_text(
            "[credential]\n\thelper =\n\thelper = !gh auth git-credential\n"
        )
        host = candidate.ClientHost()
        with pytest.raises(RuntimeError) as gh_failure:
            call(host, init_request(tmp_path, "req_gh", https.url))
        # Git asked the configured gh helper for the credential, which gh refused.
        assert https.requests == 1 and https.gh_calls() == ["auth git-credential get"]
        assert error_code(gh_failure.value) == "RemoteRejected"
        # gwz-core tells a helper that gave no password from a configuration with no helper (D13).
        assert "The credential helper in your git configuration gave no password for 127.0.0.1" in str(
            gh_failure.value
        )

        clean.setenv("https_proxy", f"http://fixture:{secret}@127.0.0.1:9/")
        with pytest.raises(RuntimeError) as proxy_refusal:
            call(host, init_request(tmp_path, "req_proxy", https.url))
        assert error_code(proxy_refusal.value) == "InvalidRequest"
        assert https.requests == 1, "refused before any connection opened"
        for failure in (gh_failure.value, proxy_refusal.value):
            visible = " ".join(
                str(part) for part in (failure, getattr(failure, "machine_message", None),
                                       getattr(failure, "detail", None))
            )
            assert secret not in visible and "fixture:" not in visible, visible
        host.close()


def test_without_configure_transport_runtime_a_stalled_setup_fails_on_the_default_clock(
    candidate: Any, ssh: SshFixture, tmp_path: Path
) -> None:
    """S3.3's stall regression through gwz-py's path: no timeout is
    configured, and a setup stage that stalls expires with reason `stall` at
    the default 9 s, while the aggregate is still ahead. Since TR2.1 a stalled
    setup is retried: gwz-py has no `--max-retries` in 1.1.0
    (dev-docs/GwzPyPerOperationTransportDesign.md §2.8), so the default 3
    retries give four attempts, one connection each, with waits of 1, 2 and
    4 s and under 1 s of jitter between them, and only the fourth attempt's
    stall fails the operation, about 44 s in, as `attempt 4 of 4`."""
    with StallServer() as stall:
        host = candidate.ClientHost()
        begun = time.monotonic()
        with pytest.raises(RuntimeError) as stalled:
            call(host, init_request(tmp_path, "req_default_clock", stall.url(ssh.user), identity(ssh)))
        took = time.monotonic() - begun
        print(f"the stalled setup failed after {took:.2f} s: {stalled.value}")
        assert "ssh setup timeout: no progress for 9 seconds (--ssh-timeout 9; attempt 4 of 4)" in str(stalled.value)
        assert stall.accepted == 4, "one connection for each of the four attempts"
        assert 4 * 9 + 7 - 1 < took < 4 * 9 + 7 + 15
        host.close()


EXIT_CHILD = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path

    sys.path.insert(0, sys.argv[2])
    from host_helpers import init_request, submit, wait_until
    from gwz.protocol.generated import TransportOptions
    from test_client_host_transport import load

    native = load(os.environ["GWZ_PY_NATIVE_MODULE"])
    host = native.ClientHost()
    base, url, key, started = Path(sys.argv[1]), sys.argv[3], sys.argv[4], Path(sys.argv[5])
    transport = TransportOptions(default_identity=key, remote_identities=[], url_scheme=None)
    submit(host, init_request(base, "req_exit", url, transport))
    wait_until(started.exists, 10, "the exchange stalling")
    print("exiting", flush=True)
    """
)


def test_interpreter_exit_cancels_a_running_network_operation_and_leaves_no_process(
    candidate: Any, stalling: SshFixture, tmp_path: Path
) -> None:
    tests = str(Path(__file__).resolve().parent)
    begun = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-c", EXIT_CHILD, str(tmp_path), tests, stalling.url,
         str(stalling.identity), str(stalling.started)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    stdout, stderr = process.communicate(timeout=60)
    took = time.monotonic() - begun
    print(f"exit with a running network operation took {took:.2f} s")
    assert process.returncode == 0, stderr
    assert stdout.split() == ["exiting"]
    # The child's whole run, its start included, ends within the cleanup
    # bound, which an exit that waited the bound out again could not (5.7 s
    # before TR2.17; 0.9 to 2.3 s measured on 2026-10-02, on a heavily loaded
    # host).
    assert took < CLEANUP_BOUND
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    os.killpg(process.pid, signal.SIGKILL)
    raise AssertionError("a process the child started outlived it")


class StallServer:
    """Accepts TCP connections and never answers: an SSH setup that stalls."""

    def __init__(self) -> None:
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._held: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def accepted(self) -> int:
        return len(self._held)

    def url(self, user: str) -> str:
        return f"ssh://{user}@127.0.0.1:{self.port}/stalled.git"

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            self._held.append(connection)

    def __enter__(self) -> "StallServer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self._listener.close()
        for connection in self._held:
            connection.close()


class HttpsFixture:
    """A local HTTPS server under a temporary CA. By default it answers every
    request 401, as gwz-core's fixture does, with a fake gh that refuses. With
    `repository` it serves a bare repository with one commit over Git's smart
    HTTP protocol (`git http-backend`), anonymously. Its first `throttle` GET
    requests are answered 429 with `Retry-After: 2`, as a host that limits
    its clients does."""

    def __init__(self, root: Path, *, repository: bool = False, throttle: int = 0) -> None:
        root.mkdir(parents=True)
        self.root = root
        self.throttle = throttle
        self.throttled = 0

        def openssl(*args: str) -> None:
            subprocess.run(["openssl", *args], check=True, capture_output=True, cwd=root)

        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca-key.pem",
                "-out", "ca.pem", "-days", "2", "-subj", "/CN=GWZ Fixture CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", "key.pem",
                "-out", "request.pem", "-subj", "/CN=localhost")
        (root / "extensions").write_text(
            "subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        )
        openssl("x509", "-req", "-in", "request.pem", "-CA", "ca.pem", "-CAkey", "ca-key.pem",
                "-CAcreateserial", "-out", "cert.pem", "-days", "1", "-extfile", "extensions")
        self.ca = root / "ca.pem"
        self.requests = 0
        self.served: Path | None = None
        git = shutil.which("git")
        if repository:
            self.served = root / "served"
            self.served.mkdir()
            seed_bare_repository(root, self.served / "repo.git")
            (root / "empty-config").write_text("")
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            # The transport's HTTPS client reads a keep-alive response, as the native fixture serves it.
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                fixture.requests += 1
                if fixture.throttled < fixture.throttle:
                    fixture.throttled += 1
                    self.send_response(429)
                    self.send_header("Retry-After", "2")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if fixture.served is None:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.smart(b"")

            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                fixture.requests += 1
                self.smart(self.request_body())

            def request_body(self) -> bytes:
                """The request body, chunked as the transport's client sends an upload-pack request."""
                if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
                    return self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = bytearray()
                while True:
                    size = int(self.rfile.readline(128).split(b";", 1)[0], 16)
                    if size == 0:
                        self.rfile.readline(128)
                        return bytes(body)
                    body.extend(self.rfile.read(size))
                    self.rfile.readline(128)

            def smart(self, body: bytes) -> None:
                """One request through `git http-backend`, as native_https_fixture.py serves it."""
                assert fixture.served is not None and git is not None
                path, _, query = self.path.partition("?")
                environment = dict(os.environ)
                environment.update(
                    GIT_PROJECT_ROOT=str(fixture.served), GIT_HTTP_EXPORT_ALL="1", PATH_INFO=path,
                    QUERY_STRING=query, REQUEST_METHOD=self.command,
                    CONTENT_TYPE=self.headers.get("Content-Type", ""), CONTENT_LENGTH=str(len(body)),
                    REMOTE_USER="fixture", SERVER_PROTOCOL="HTTP/1.1", GIT_CONFIG_NOSYSTEM="1",
                    GIT_CONFIG_GLOBAL=str(fixture.root / "empty-config"),
                )
                response = subprocess.run(
                    [git, "http-backend"], input=body, env=environment, capture_output=True, timeout=30
                )
                head, separator, payload = response.stdout.partition(b"\r\n\r\n")
                if response.returncode or not separator:
                    self.send_response(500)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                fields = [line.decode("ascii").split(":", 1) for line in head.split(b"\r\n")]
                status = next(
                    (int(value.strip().split()[0]) for name, value in fields if name.lower() == "status"), 200
                )
                self.send_response(status)
                for name, value in fields:
                    if name.lower() not in ("status", "content-length"):
                        self.send_header(name, value.strip())
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root / "cert.pem", root / "key.pem")
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.port = self._server.server_address[1]
        self.url = f"https://127.0.0.1:{self.port}/repo.git"

    def fake_gh(self, secret: str) -> Path:
        directory = self.root / "bin"
        directory.mkdir()
        gh = directory / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" >> '{self.root / 'gh-calls'}'\n"
            "cat >/dev/null\n"
            f"echo 'gh: token {secret} was rejected' >&2\n"
            f"echo 'password={secret}'\n"
            "exit 1\n"
        )
        gh.chmod(0o755)
        return gh

    def gh_calls(self) -> list[str]:
        calls = self.root / "gh-calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def __enter__(self) -> "HttpsFixture":
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
