from __future__ import annotations

from typing import Any


def plural(count: int) -> str:
    return "" if count == 1 else "s"


def enum_label(value: Any) -> str:
    name = getattr(value, "name", str(value))
    return "".join(part.capitalize() for part in name.split("_"))


def enum_name(value: Any) -> str:
    return getattr(value, "name", str(value))


def is_response(response: Any, class_name: str, action_name: str) -> bool:
    if type(response).__name__ == class_name:
        return True
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    return getattr(getattr(meta, "action", None), "name", None) == action_name


def status_line(response: Any) -> str:
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    return f"status: {enum_label(getattr(meta, 'aggregate_status', None))}"


def append_errors(lines: list[str], response: Any) -> None:
    envelope = getattr(response, "response", None)
    for error in getattr(envelope, "errors", []):
        lines.append(f"{enum_label(error.code)}: {error.message}")


def push_blank(lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")
