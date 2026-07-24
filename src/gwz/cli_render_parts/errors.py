from __future__ import annotations

import json
import re
from typing import Any

from .common import enum_label
from .machine import merge_error_json


def render_error(error: BaseException, *, json_mode: bool = False) -> str:
    if json_mode:
        operation_errors = getattr(error, "member_errors", None) or []
        if operation_errors:
            errors = [merge_error_json(item) for item in operation_errors]
        else:
            errors = [exception_error_json(error)]
        return json.dumps(
            {
                "kind": "response",
                "meta": None,
                "members": [],
                "errors": errors,
                "workspace_git_status": None,
            },
            sort_keys=True,
        )
    return f"gwz: {error}"


def exception_error_json(error: BaseException) -> dict[str, Any]:
    rendered_message = str(error)
    match = re.search(r"(?:^|: )([A-Z][A-Za-z0-9]+): (.*)$", rendered_message)
    code = getattr(error, "code", None) or (match.group(1) if match else None)
    message = getattr(error, "machine_message", None) or (
        match.group(2) if match else rendered_message
    )
    member_id = getattr(error, "member_id", None)
    member_path = getattr(error, "member_path", None)
    target_kind = getattr(error, "target_kind", None)
    if target_kind is None and (member_id is not None or member_path is not None):
        target_kind = "Member"
    return {
        "code": code,
        "message": message,
        "member_id": member_id,
        "member_path": member_path,
        "target_kind": enum_label(target_kind) if target_kind is not None else None,
        "detail": getattr(error, "detail", None),
    }
