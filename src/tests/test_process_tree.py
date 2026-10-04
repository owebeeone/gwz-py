"""The process-tree helper the interpreter-exit rows rely on, on every platform:
a POSIX session, or a Windows Job Object."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import pytest

from process_tree import ProcessTree

# Starts a grandchild that outlives this child, prints its pid, and exits. The
# grandchild holds none of the child's pipes, so the child's output ends with it.
LEAVES_A_GRANDCHILD = textwrap.dedent(
    """
    import subprocess, sys
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(grandchild.pid, flush=True)
    """
)


def test_a_child_that_starts_nothing_leaves_nothing() -> None:
    with ProcessTree() as tree:
        process = subprocess.Popen([sys.executable, "-c", "pass"], **tree.popen_options)
        tree.adopt(process)
        process.wait(timeout=60)
        assert not tree.outlived(process)


def test_a_grandchild_that_outlives_the_child_is_found_and_killed() -> None:
    with ProcessTree() as tree:
        process = subprocess.Popen(
            [sys.executable, "-c", LEAVES_A_GRANDCHILD],
            stdout=subprocess.PIPE,
            text=True,
            **tree.popen_options,
        )
        tree.adopt(process)
        stdout, _ = process.communicate(timeout=60)
        assert process.returncode == 0
        assert int(stdout.split()[0]) > 0
        assert tree.outlived(process)
        deadline = time.monotonic() + 5
        while tree.outlived(process):
            assert time.monotonic() < deadline, "the grandchild survived its kill"
            time.sleep(0.05)


@pytest.mark.skipif(sys.platform != "darwin", reason="only macOS refuses signals to a group of zombies")
def test_a_group_left_with_only_a_zombie_runs_nothing() -> None:
    with ProcessTree() as tree:
        process = subprocess.Popen([sys.executable, "-c", "pass"], **tree.popen_options)
        tree.adopt(process)
        # Wait for the exit without reaping it, through kqueue: the child stays
        # a zombie, the only member of its session's group.
        import select

        queue = select.kqueue()
        try:
            exit_event = select.kevent(
                process.pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD,
                fflags=select.KQ_NOTE_EXIT,
            )
            queue.control([exit_event], 0)
            assert queue.control(None, 1, 60), "the child did not exit"
        except ProcessLookupError:
            pass  # It had already exited.
        finally:
            queue.close()
        assert not tree.outlived(process)
        process.wait()
