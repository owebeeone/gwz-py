"""The SSH fixture's server is ready only when it is the fixture's own server that listens.

The fixture picks its port by binding and releasing one, so another server can take the port before this one binds
it (another test's, or any process's). The second ``sshd`` then exits, and a readiness check that only connects would
go on to talk to the first one: the fixture's ``known_hosts`` does not trust its host key. These rows put two fixtures
on one port on purpose, through the ``ports`` argument that names the ports to try.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from ssh_fixture import SSHD, SshFixture, free_port

pytestmark = pytest.mark.skipif(not SSHD.exists(), reason=f"the fixture needs {SSHD}")


def after(taken: int, times: int) -> Callable[[], int]:
    """The ports to try: `taken` for `times` attempts, as when another server won the race for it, then fresh ones."""
    left = [times]

    def next_port() -> int:
        if left[0] > 0:
            left[0] -= 1
            return taken
        return free_port()

    return next_port


def test_a_fixture_port_is_below_every_range_the_system_hands_out_and_is_free() -> None:
    """The system gives its own range (Linux from 32768, macOS and Windows from 49152) to any listener that asks for
    port 0 and to the source port of any connection, so a port picked there is exposed to all of them in the gap
    before the server binds it."""
    for _ in range(50):
        port = free_port()
        assert 12_000 <= port < 32_000, port
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))


def test_a_fixture_whose_port_another_server_took_moves_to_a_port_of_its_own(tmp_path: Path) -> None:
    first = SshFixture(tmp_path / "first")
    try:
        second = SshFixture(tmp_path / "second", ports=after(first.port, 1))
        try:
            assert second.port != first.port, "both fixtures claim one port"
            assert second._server.poll() is None, "the second fixture's server is not running"
            known_hosts = (second.home / ".ssh" / "known_hosts").read_text()
            assert known_hosts.startswith(f"[127.0.0.1]:{second.port} ")
        finally:
            second.close()
    finally:
        first.close()


def test_a_fixture_that_finds_no_port_to_serve_on_says_so(tmp_path: Path) -> None:
    first = SshFixture(tmp_path / "first")
    try:
        with pytest.raises(RuntimeError, match="the sshd did not start"):
            SshFixture(tmp_path / "second", ports=after(first.port, 1000))
    finally:
        first.close()
