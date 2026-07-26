from __future__ import annotations

import dataclasses
from enum import Enum
from pathlib import Path
from typing import Any

from .common import enum_label


def json_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def operation_event_json(event: Any) -> dict[str, Any]:
    """Render the shared JSONL event shape used by the Rust CLI."""
    return {
        "kind": "event",
        "operation_id": event.operation_id,
        "request_id": event.request_id,
        "sequence": event.sequence,
        "timestamp_ms": event.timestamp_ms,
        "event_kind": enum_label(event.kind),
        "severity": enum_label(event.severity),
        "member_id": event.member_id,
        "member_path": event.member_path,
        "message": event.message,
        "member": protocol_json(event.member),
        "error": protocol_json(event.error),
        "attribution": protocol_json(event.attribution),
        "progress": protocol_json(event.progress),
        "target_kind": (
            enum_label(event.target_kind) if event.target_kind is not None else None
        ),
        "merge_state": (
            enum_label(event.merge_state) if event.merge_state is not None else None
        ),
        "merge_member": (
            merge_repo_json(event.merge_member)
            if event.merge_member is not None
            else None
        ),
        "artifact_path": event.artifact_path,
    }


def protocol_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return enum_label(value)
    if dataclasses.is_dataclass(value):
        return {
            field.name: protocol_json(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (list, tuple)):
        return [protocol_json(item) for item in value]
    if isinstance(value, dict):
        return {key: protocol_json(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def merge_response_json(response: Any) -> dict[str, Any]:
    envelope, counts = response.response, response.participant_counts
    meta = envelope.meta
    merge = {
        "merge_id": response.merge_id,
        "state": enum_label(response.state),
        "open": response.open,
        "participant_counts": json_fields(
            counts,
            "total",
            "planned",
            "up_to_date",
            "fast_forwarded",
            "merged",
            "conflicted",
            "failed",
            "unattempted",
            "continued",
            "aborted",
            "rolled_back",
        ),
        "repos": [merge_repo_json(repo) for repo in response.repos],
        "operation_drift": [
            {"kind": enum_label(drift.kind), "message": drift.message}
            for drift in response.operation_drift
        ],
        "preservation": (
            [merge_preservation_json(entry) for entry in response.preservation]
            if response.preservation is not None
            else None
        ),
        "publication_step": (
            enum_label(response.publication_step)
            if response.publication_step is not None
            else None
        ),
    }
    return {
        "kind": "response",
        "meta": {
            **json_fields(meta, "request_id", "schema_version"),
            "action": enum_label(meta.action),
            "aggregate_status": enum_label(meta.aggregate_status),
            **json_fields(meta, "operation_id", "message"),
        },
        "members": [json_default(member) for member in envelope.members],
        "errors": [merge_error_json(error) for error in envelope.errors],
        "workspace_git_status": None,
        "branch_repos": None,
        "merge": merge,
        "stash_bundles": None,
    }


def merge_repo_json(repo: Any) -> dict[str, Any]:
    value = json_fields(
        repo,
        "target_id",
        "path",
        "source_ref",
        "source_commit",
        "target_branch",
        "before_commit",
        "resulting_commit",
        "live_commit",
        "prediction_complete",
        "conflict_paths",
        "continue_eligible",
        "abort_eligible",
    )
    value.update(
        target_kind=enum_label(repo.target_kind),
        state=enum_label(repo.state),
        predicted=enum_label(repo.predicted) if repo.predicted else None,
        drift=[merge_participant_drift_json(drift) for drift in repo.drift],
        error=merge_error_json(repo.error) if repo.error else None,
        pending_action=(
            {
                "kind": enum_label(repo.pending_action.kind),
                "state": enum_label(repo.pending_action.state),
                "message": repo.pending_action.message,
            }
            if repo.pending_action is not None
            else None
        ),
    )
    return value


def merge_participant_drift_json(drift: Any) -> dict[str, Any]:
    value = json_fields(
        drift,
        "message",
        "expected_branch",
        "live_branch",
        "expected_head",
        "live_head",
        "expected_merge_head",
        "live_merge_head",
    )
    return {"kind": enum_label(drift.kind), **value}


def merge_preservation_json(entry: Any) -> dict[str, Any]:
    return json_fields(
        entry,
        "target_id",
        "path",
        "backup_ref",
        "backup_commit",
        "stash_id",
        "stash_object_id",
    )


def merge_error_json(error: Any) -> dict[str, Any]:
    return {
        **json_fields(error, "message", "member_id", "member_path", "detail"),
        "code": enum_label(error.code),
        "target_kind": (
            enum_label(error.target_kind) if error.target_kind is not None else None
        ),
    }


def json_fields(value: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(value, name) for name in names}
