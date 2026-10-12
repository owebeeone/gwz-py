"""Disposable loopback servers for the transport rows (gwz-py
dev-docs/GwzPyPerOperationTransportDesign.md §3).

``SshFixture`` follows gwz-core's ``tests/transport_ssh`` fixture: the
system's ``/usr/sbin/sshd`` with temporary host and client keys, a bare
repository with one commit, and a home whose ``.ssh/known_hosts`` trusts the
server. With ``stall``, every exec writes its PID and then holds its channel
open without writing, as gwz-core's stalling fixture does. A counting proxy in
front of it counts the connections operations open. No user's agent, key or
trust store is read.

On Windows (1.1.0 S4.5, plan step 5.3) the server is an ``sshd.exe`` run as a
foreground child, as gwz-core's ``fixture_host.rs`` runs it: the file
``GWZ_TEST_SSHD`` names, else the system's ``System32/OpenSSH/sshd.exe``, else
one on ``PATH``, with the ``ssh-keygen.exe`` beside it. Keys are 2048-bit RSA
in PEM form (libssh2 on Windows CNG has no ed25519). The host's OpenSSH default
shell must be a POSIX shell (a Git for Windows ``bash.exe``), and a
``ForceCommand`` puts Git's directories on its ``PATH`` and changes into the
fixture's directory. The server and its sessions end by the exact PID this
fixture started. The stalling server is Unix-only.
"""

from __future__ import annotations

import getpass
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

WINDOWS = os.name == "nt"
SSHD_ENV = "GWZ_TEST_SSHD"


def sshd_program() -> Path:
    """The OpenSSH server a fixture runs, and where `ssh-keygen` is."""
    if not WINDOWS:
        return Path("/usr/sbin/sshd")
    named = os.environ.get(SSHD_ENV)
    candidates = [Path(named)] if named else []
    candidates.append(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "sshd.exe")
    found = shutil.which("sshd.exe")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


SSHD = sshd_program()


def keygen_program() -> str | Path:
    return SSHD.with_name("ssh-keygen.exe") if WINDOWS else "ssh-keygen"


def posix_path(path: Path | str) -> str:
    """`path` as a POSIX shell on Windows spells it: `C:\\a b` as `/c/a b`."""
    text = str(path).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def sshd_value(path: Path | str) -> str:
    """`path` as one sshd_config value: forward slashes on Windows, quoted when it holds a space."""
    text = str(path).replace("\\", "/") if WINDOWS else str(path)
    return f'"{text}"' if " " in text else text


def git_directories() -> list[str]:
    """The directories Git runs from on Windows: the one that holds `git.exe`, and its exec path."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("the transport rows need git on PATH")
    exec_path = run("git", "--exec-path")
    return [str(Path(git).parent), exec_path]


def force_command(directory: Path) -> str:
    """The `ForceCommand` line for a POSIX default shell, as gwz-core's `posix_force_command` writes it."""
    path = ":".join(posix_path(entry) for entry in git_directories())
    return (
        f"ForceCommand cd '{posix_path(directory)}' && export PATH='{path}':\"$PATH\" && "
        'eval "$SSH_ORIGINAL_COMMAND"\n'
    )


def run(*command: str | Path, cwd: Path | None = None) -> str:
    return subprocess.run(
        [str(part) for part in command],
        check=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def seed_bare_repository(root: Path, repository: Path) -> None:
    """A bare repository at `repository` with one commit on main, made through `root`/seed."""
    run("git", "init", "-q", "--bare", "--initial-branch=main", repository)
    seed = root / "seed"
    run("git", "init", "-q", "--initial-branch=main", seed)
    run("git", "-c", "user.name=GWZ Test", "-c", "user.email=gwz@example.invalid",
        "commit", "-q", "--allow-empty", "-m", "seed", cwd=seed)
    run("git", "push", "-q", str(repository), "main", cwd=seed)


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
        if WINDOWS and stall:
            raise RuntimeError("the stalling server is Unix-only")
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        # The files keep their ed25519 names on Windows too, where the keys are RSA in PEM form.
        host_key = root / "host_ed25519"
        self.identity = root / "client_ed25519"
        for key in (host_key, self.identity):
            if WINDOWS:
                run(keygen_program(), "-q", "-t", "rsa", "-b", "2048", "-m", "PEM", "-N", "", "-f", key)
            else:
                run(keygen_program(), "-q", "-t", "ed25519", "-N", "", "-f", key)
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
            authorized.write_text(public.strip() + "\n", newline="\n")
        self.port = free_port()
        config = root / "sshd_config"
        if WINDOWS:
            config.write_text(
                f"Port {self.port}\nListenAddress 127.0.0.1\nHostKey {sshd_value(host_key)}\n"
                f"AuthorizedKeysFile {sshd_value(authorized)}\nPidFile none\nPasswordAuthentication no\n"
                "KbdInteractiveAuthentication no\nPubkeyAuthentication yes\nStrictModes no\nLogLevel ERROR\n"
                + force_command(root)
            )
        else:
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
        self.user = getpass.getuser() if WINDOWS else run("id", "-un")
        remote_path = posix_path(self.repository) if WINDOWS else self.repository
        self.url = f"ssh://{self.user}@127.0.0.1:{connect_port}{remote_path}"
        self.home = root / "home"
        (self.home / ".ssh").mkdir(parents=True)
        host_public = host_key.with_suffix(".pub").read_text().strip()
        (self.home / ".ssh" / "known_hosts").write_text(
            f"[127.0.0.1]:{connect_port} {host_public}\n"
        )

    def _seed_repository(self) -> None:
        seed_bare_repository(self.root, self.repository)

    def stalled_pids(self) -> list[int]:
        """The PIDs of the execs a stalling fixture holds."""
        try:
            return [int(line) for line in self.started.read_text().split()]
        except FileNotFoundError:
            return []

    def close(self) -> None:
        if self.proxy:
            self.proxy.close()
        if WINDOWS:
            # The server and the session processes under it, by the exact PID this fixture started.
            subprocess.run(
                ["taskkill", "/PID", str(self._server.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
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
