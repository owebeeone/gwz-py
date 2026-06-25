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

    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    value = getattr(meta, "message", None) or getattr(meta, "aggregate_status", response)
    if isinstance(value, Enum):
        return value.name
    return str(value)


def render_error(error: BaseException) -> str:
    return f"gwz: {error}"
