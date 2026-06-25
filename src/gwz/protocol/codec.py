from __future__ import annotations

import dataclasses
import json
from enum import Enum
from functools import cache
from importlib import resources
from typing import Any

from taut.ir.load import schema_from_json


@cache
def schema():
    with resources.files("gwz.protocol.generated").joinpath("gwz.ir.json").open(
        "r",
        encoding="utf-8",
    ) as handle:
        return schema_from_json(json.load(handle))


def to_wire(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: to_wire(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, list):
        return [to_wire(item) for item in value]
    if isinstance(value, tuple):
        return [to_wire(item) for item in value]
    if isinstance(value, dict):
        return {to_wire(key): to_wire(item) for key, item in value.items()}
    return value
