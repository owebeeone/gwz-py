"""A stand-in for the native ``ClientHost`` that fake native modules hand the bridge."""

from __future__ import annotations

from typing import Any


class ModuleHost:
    """Runs every ``call`` and ``submit`` on a fake module's own functions.

    A fake module runs no network operation, so there is nothing to cancel,
    and closing reports the contract's (0, false): no peer cleanup occurred.
    """

    def __init__(self, module: Any) -> None:
        self.module = module
        self.closes = 0
        self.cancels: list[str] = []

    def call(self, *args: Any) -> Any:
        return self.module.call(*args)

    def submit(self, *args: Any) -> Any:
        return self.module.submit(*args)

    def cancel_operation(self, operation_id: str) -> tuple[int, bool]:
        self.cancels.append(operation_id)
        error = RuntimeError(f"operation {operation_id} is not a waiting or running network operation of this client")
        error.code = "InvalidRequest"  # type: ignore[attr-defined]
        raise error

    def close(self) -> tuple[int, bool]:
        self.closes += 1
        return (0, False)


class FakeModule:
    """A fake native module whose host is a ``ModuleHost``."""

    def ClientHost(self) -> ModuleHost:  # noqa: N802 - the native class's name
        return ModuleHost(self)
