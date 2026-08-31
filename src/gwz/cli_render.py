"""Stable rendering facade for the Python CLI."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from .cli_render_parts.common import is_response
from .cli_render_parts.errors import render_error
from .cli_render_parts.listings import (
    render_branch_response,
    render_member_listing,
    render_snapshot_listing,
    render_stash_response,
    render_tag_listing,
)
from .cli_render_parts.log import (
    log_color_enabled,
    render_log_degradation,
    render_log_entry,
    render_log_record_json,
)
from .cli_render_parts.machine import (
    json_default,
    merge_response_json,
    operation_event_json,
)
from .cli_render_parts.merge import render_merge_response
from .cli_render_parts.status import render_status_porcelain, render_status_response

__all__ = [
    "json_default",
    "operation_event_json",
    "log_color_enabled",
    "render_error",
    "render_log_degradation",
    "render_log_entry",
    "render_log_record_json",
    "render_response",
]


def render_response(
    response: Any,
    *,
    json_mode: bool = False,
    local_paths: bool = False,
    porcelain: bool = False,
) -> str:
    if json_mode:
        value = (
            merge_response_json(response)
            if is_response(response, "MergeResponse", "merge")
            else response
        )
        return json.dumps(value, default=json_default, sort_keys=True)

    snapshots = getattr(response, "snapshots", None)
    if snapshots is not None:
        return render_snapshot_listing(snapshots)

    tags = getattr(response, "tags", None)
    if tags is not None:
        return render_tag_listing(tags)

    if is_response(response, "LsResponse", "ls"):
        return render_member_listing(
            getattr(response, "members", None) or [],
            local_paths=local_paths,
        )

    workspace_git_status = getattr(response, "workspace_git_status", None)
    if workspace_git_status is not None:
        if porcelain:
            return render_status_porcelain(workspace_git_status)
        return render_status_response(response, workspace_git_status)

    if is_response(response, "MergeResponse", "merge"):
        return render_merge_response(response)

    repos = getattr(response, "repos", None)
    if repos is not None:
        return render_branch_response(response, repos)

    bundles = getattr(response, "bundles", None)
    if bundles is not None:
        return render_stash_response(response, bundles)

    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    value = getattr(meta, "message", None) or getattr(
        meta,
        "aggregate_status",
        response,
    )
    if isinstance(value, Enum):
        return value.name
    return str(value)
