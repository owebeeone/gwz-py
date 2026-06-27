from __future__ import annotations

import dataclasses
import json
from enum import Enum
from pathlib import Path
from typing import Any


def json_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def render_response(response: Any, *, json_mode: bool = False) -> str:
    if json_mode:
        return json.dumps(response, default=json_default, sort_keys=True)

    snapshots = getattr(response, "snapshots", None)
    if snapshots is not None:
        return _render_snapshot_listing(snapshots)

    tags = getattr(response, "tags", None)
    if tags is not None:
        return _render_tag_listing(tags)

    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    value = getattr(meta, "message", None) or getattr(meta, "aggregate_status", response)
    if isinstance(value, Enum):
        return value.name
    return str(value)


def render_error(error: BaseException) -> str:
    return f"gwz: {error}"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _render_snapshot_listing(snapshots: list[Any]) -> str:
    if not snapshots:
        return "no snapshots"
    lines = [f"{len(snapshots)} snapshot{_plural(len(snapshots))}:"]
    for snapshot in snapshots:
        lines.append(
            f"  {snapshot.name}\t{snapshot.created_at}\t{snapshot.created_by}"
            f"\t({snapshot.members} member{_plural(snapshot.members)})"
        )
    return "\n".join(lines)


def _render_tag_listing(tags: list[Any]) -> str:
    if not tags:
        return "no tags"
    lines = [f"{len(tags)} tag{_plural(len(tags))}:"]
    for tag in tags:
        lines.append(f"  {tag.name}\t({tag.members} member{_plural(tag.members)})")
    return "\n".join(lines)
