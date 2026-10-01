"""Disposable loopback servers for the transport rows (gwz-py
dev-docs/GwzPyPerOperationTransportDesign.md §3).

``SshFixture`` follows gwz-core's ``tests/transport_ssh`` fixture: the
system's ``/usr/sbin/sshd`` with temporary host and client keys, a bare
repository with one commit, and a home whose ``.ssh/known_hosts`` trusts the
server. With ``stall``, every exec writes its PID and then holds its channel
open without writing, as gwz-core's stalling fixture does. A counting proxy in
front of it counts the connections operations open. No user's agent, key or
trust store is read.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

SSHD = Path("/usr/sbin/sshd")


def run(*command: str | Path, cwd: Path | None = None) -> str:
    return subprocess.run(
        [str(part) for part in command],
        check=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_listening(port: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError(f"nothing listens on 127.0.0.1:{port}")


class CountingProxy:
    """Forwards each connection to `target` and counts them."""

    def __init__(self, target: int) -> None:
        self.target = target
        self.accepted = 0
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._closed:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            self.accepted += 1
            server = socket.create_connection(("127.0.0.1", self.target))
            for source, sink in ((client, server), (server, client)):
                threading.Thread(target=self._pipe, args=(source, sink), daemon=True).start()

    @staticmethod
    def _pipe(source: socket.socket, sink: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for end in (source, sink):
                try:
                    end.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        self._closed = True
        self._listener.close()


class SshFixture:
    def __init__(self, root: Path, *, stall: bool = False, count: bool = False) -> None:
        if not SSHD.exists():
            raise RuntimeError(f"the transport rows need {SSHD}")
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        host_key = root / "host_ed25519"
        self.identity = root / "client_ed25519"
        for key in (host_key, self.identity):
            run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key)
        self.repository = root / "repository.git"
        self._seed_repository()
        self.started = root / "stalled"
        public = self.identity.with_suffix(".pub").read_text()
        authorized = root / "authorized_keys"
        if stall:
            script = root / "stall.sh"
            script.write_text(f"#!/bin/sh\necho $$ >> '{self.started}'\nexec cat >/dev/null\n")
            script.chmod(0o755)
            authorized.write_text(f'command="{script}" {public}')
        else:
            authorized.write_text(public)
        self.port = free_port()
        config = root / "sshd_config"
        config.write_text(
            f"Port {self.port}\nListenAddress 127.0.0.1\nHostKey {host_key}\n"
            f"AuthorizedKeysFile {authorized}\nPidFile none\nPasswordAuthentication no\n"
            "KbdInteractiveAuthentication no\nChallengeResponseAuthentication no\nUsePAM no\n"
            "PermitRootLogin yes\nPubkeyAuthentication yes\nStrictModes no\nLogLevel ERROR\n"
        )
        run(SSHD, "-t", "-f", config)
        self._server = subprocess.Popen(
            [str(SSHD), "-D", "-e", "-f", str(config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_listening(self.port)
        self.proxy = CountingProxy(self.port) if count else None
        connect_port = self.proxy.port if self.proxy else self.port
        self.user = run("id", "-un")
        self.url = f"ssh://{self.user}@127.0.0.1:{connect_port}{self.repository}"
        self.home = root / "home"
        (self.home / ".ssh").mkdir(parents=True)
        host_public = host_key.with_suffix(".pub").read_text().strip()
        (self.home / ".ssh" / "known_hosts").write_text(
            f"[127.0.0.1]:{connect_port} {host_public}\n"
        )

    def _seed_repository(self) -> None:
        run("git", "init", "-q", "--bare", "--initial-branch=main", self.repository)
        seed = self.root / "seed"
        run("git", "init", "-q", "--initial-branch=main", seed)
        run("git", "-c", "user.name=GWZ Test", "-c", "user.email=gwz@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "seed", cwd=seed)
        run("git", "push", "-q", str(self.repository), "main", cwd=seed)

    def stalled_pids(self) -> list[int]:
        """The PIDs of the execs a stalling fixture holds."""
        try:
            return [int(line) for line in self.started.read_text().split()]
        except FileNotFoundError:
            return []

    def close(self) -> None:
        if self.proxy:
            self.proxy.close()
        self._server.terminate()
        try:
            self._server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._server.kill()
        for pid in self.stalled_pids():
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
