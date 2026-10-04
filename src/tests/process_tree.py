"""A child process and everything it starts, confined so that a test can show
none of it outlives the child: a new session on POSIX, and on Windows a Job
Object, which every process the child starts joins as well."""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any


class ProcessTree:
    """Start the child with ``popen_options``, then ``adopt`` it at once."""

    def __init__(self) -> None:
        self.popen_options: dict[str, Any] = {}
        self._job: _Job | None = None
        if os.name == "nt":
            self._job = _Job()
        else:
            self.popen_options["start_new_session"] = True

    def __enter__(self) -> ProcessTree:
        return self

    def __exit__(self, *_: object) -> None:
        if self._job is not None:
            self._job.close()

    def adopt(self, process: subprocess.Popen[Any]) -> None:
        # The child is still starting its interpreter when Popen returns, so
        # it joins the job before it can start anything of its own.
        if self._job is not None:
            self._job.assign(process.pid)

    def outlived(self, process: subprocess.Popen[Any]) -> bool:
        """Whether anything the exited child started still runs. Whatever
        does is killed."""
        if self._job is not None:
            if self._job.active() == 0:
                return False
            self._job.terminate()
            return True
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        os.killpg(process.pid, signal.SIGKILL)
        return True


class _Job:
    """A Windows Job Object, through kernel32."""

    _ACCOUNTING = 1  # JobObjectBasicAccountingInformation
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._ctypes = ctypes
        self._kernel32 = kernel32
        self._accounting = Accounting
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, pid: int) -> None:
        process = self._kernel32.OpenProcess(
            self._PROCESS_SET_QUOTA | self._PROCESS_TERMINATE, False, pid
        )
        if not process:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(process)

    def active(self) -> int:
        information = self._accounting()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            self._ACCOUNTING,
            self._ctypes.byref(information),
            self._ctypes.sizeof(information),
            None,
        ):
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        return int(information.ActiveProcesses)

    def terminate(self) -> None:
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def close(self) -> None:
        self._kernel32.CloseHandle(self._handle)
