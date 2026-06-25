from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from .errors import GwzOperationError
from .protocol.generated import MaterializeTarget, MaterializeTargetKind, SourceUrl

SUCCESS_AGGREGATE_STATUS_NAMES = {"accepted", "ok", "noop"}


def request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def enum_value(enum_type: type[Any], value: Any) -> Any:
    if value is None or isinstance(value, enum_type):
        return value
    return enum_type[value]


def materialize_target(
    kind: str | MaterializeTargetKind,
    name: str | None = None,
    commit: str | None = None,
) -> MaterializeTarget:
    if isinstance(kind, str):
        kind = MaterializeTargetKind[kind]
    return MaterializeTarget(kind=kind, name=name, commit=commit)


def sources(sources_: Sequence[str | SourceUrl]) -> list[SourceUrl]:
    result: list[SourceUrl] = []
    for source in sources_:
        if isinstance(source, SourceUrl):
            result.append(source)
        else:
            result.append(SourceUrl(url=str(source), path=None, remote_name=None, branch=None))
    return result


def _status_name(aggregate: Any) -> str:
    return getattr(aggregate, "name", str(aggregate))


def _response_meta(response: Any) -> Any:
    envelope = getattr(response, "response", None)
    return getattr(envelope, "meta", None)


def _response_member_errors(response: Any) -> list[Any]:
    envelope = getattr(response, "response", None)
    errors = getattr(envelope, "errors", None)
    if errors is None:
        errors = getattr(response, "errors", None)
    if errors is None:
        return []
    return errors


def raise_for_response(response: Any) -> None:
    meta = _response_meta(response)
    aggregate = getattr(meta, "aggregate_status", None)
    if aggregate is None:
        aggregate = getattr(response, "aggregate_status", None)
    if aggregate is None:
        return
    name = _status_name(aggregate)
    if name in SUCCESS_AGGREGATE_STATUS_NAMES:
        return
    message = getattr(meta, "message", None) or f"gwz operation returned {name}"
    raise GwzOperationError(
        message=message,
        response=response,
        aggregate_status=aggregate,
        member_errors=_response_member_errors(response),
        operation_id=getattr(meta, "operation_id", None) or getattr(response, "operation_id", None),
        request_id=getattr(meta, "request_id", None) or getattr(response, "request_id", None),
    )
