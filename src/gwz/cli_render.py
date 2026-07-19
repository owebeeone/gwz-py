from __future__ import annotations

import dataclasses
import json
import re
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


def operation_event_json(event: Any) -> dict[str, Any]:
    """Render the shared JSONL event shape used by the Rust CLI."""
    return {
        "kind": "event",
        "operation_id": event.operation_id,
        "request_id": event.request_id,
        "sequence": event.sequence,
        "timestamp_ms": event.timestamp_ms,
        "event_kind": _enum_label(event.kind),
        "severity": _enum_label(event.severity),
        "member_id": event.member_id,
        "member_path": event.member_path,
        "message": event.message,
        "member": _protocol_json(event.member),
        "error": _protocol_json(event.error),
        "attribution": _protocol_json(event.attribution),
        "progress": _protocol_json(event.progress),
        "target_kind": (
            _enum_label(event.target_kind) if event.target_kind is not None else None
        ),
        "merge_state": (
            _enum_label(event.merge_state) if event.merge_state is not None else None
        ),
        "merge_member": (
            _merge_repo_json(event.merge_member)
            if event.merge_member is not None else None
        ),
        "artifact_path": event.artifact_path,
    }


def _protocol_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return _enum_label(value)
    if dataclasses.is_dataclass(value):
        return {
            field.name: _protocol_json(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (list, tuple)):
        return [_protocol_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _protocol_json(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def render_response(
    response: Any,
    *,
    json_mode: bool = False,
    local_paths: bool = False,
    porcelain: bool = False,
) -> str:
    if json_mode:
        value = _merge_response_json(response) if _is_response(
            response, "MergeResponse", "merge"
        ) else response
        return json.dumps(value, default=json_default, sort_keys=True)

    snapshots = getattr(response, "snapshots", None)
    if snapshots is not None:
        return _render_snapshot_listing(snapshots)

    tags = getattr(response, "tags", None)
    if tags is not None:
        return _render_tag_listing(tags)

    if _is_response(response, "LsResponse", "ls"):
        return _render_member_listing(
            getattr(response, "members", None) or [],
            local_paths=local_paths,
        )

    workspace_git_status = getattr(response, "workspace_git_status", None)
    if workspace_git_status is not None:
        if porcelain:
            return _render_status_porcelain(workspace_git_status)
        return _render_status_response(response, workspace_git_status)

    if _is_response(response, "MergeResponse", "merge"):
        return _render_merge_response(response)

    repos = getattr(response, "repos", None)
    if repos is not None:
        return _render_branch_response(response, repos)

    bundles = getattr(response, "bundles", None)
    if bundles is not None:
        return _render_stash_response(response, bundles)

    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    value = getattr(meta, "message", None) or getattr(meta, "aggregate_status", response)
    if isinstance(value, Enum):
        return value.name
    return str(value)


def render_error(error: BaseException, *, json_mode: bool = False) -> str:
    if json_mode:
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
        return json.dumps(
            {
                "kind": "response",
                "meta": None,
                "members": [],
                "errors": [
                    {
                        "code": code,
                        "message": message,
                        "member_id": member_id,
                        "member_path": member_path,
                        "target_kind": (
                            _enum_label(target_kind) if target_kind is not None else None
                        ),
                        "detail": getattr(error, "detail", None),
                    }
                ],
                "workspace_git_status": None,
            },
            sort_keys=True,
        )
    return f"gwz: {error}"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _enum_label(value: Any) -> str:
    name = getattr(value, "name", str(value))
    return "".join(part.capitalize() for part in name.split("_"))


def _is_response(response: Any, class_name: str, action_name: str) -> bool:
    if type(response).__name__ == class_name:
        return True
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    return getattr(getattr(meta, "action", None), "name", None) == action_name


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


def _render_member_listing(members: list[Any], *, local_paths: bool) -> str:
    return "\n".join(member.path if local_paths else member.abspath for member in members)


def _render_status_porcelain(workspace_status: Any) -> str:
    changes = [
        _porcelain_change(change.index_status, change.worktree_status, change.workspace_path)
        for change in getattr(workspace_status, "root_file_changes", [])
    ]
    changes.extend(
        _porcelain_change(change.index_status, change.worktree_status, change.workspace_path)
        for change in getattr(workspace_status, "file_changes", [])
    )
    return "\n".join(changes)


def _porcelain_change(index_status: str, worktree_status: str, path: str) -> str:
    return f"{_format_status_pair(index_status, worktree_status)} {path}"


def _render_status_response(response: Any, workspace_status: Any) -> str:
    lines: list[str] = []
    _append_branch_summary(lines, workspace_status)
    changes = _root_human_changes(workspace_status)
    changes.extend(_member_human_changes(workspace_status))
    _append_change_sections(lines, changes)
    _append_unmaterialized_notice(lines, response)
    _append_status_issues(lines, response)
    _append_suppressed_dirty_summary(lines, response, workspace_status)
    if not lines:
        lines.append("nothing to commit, working tree clean")
    return "\n".join(lines)


def _append_branch_summary(lines: list[str], workspace_status: Any) -> None:
    groups = [
        (group.label, list(group.member_paths))
        for group in getattr(workspace_status, "branch_groups", [])
    ]
    root_status = getattr(workspace_status, "root_status", None)

    if root_status is None:
        if not groups:
            lines.append("Workspace status")
        elif len(groups) == 1:
            lines.append(_branch_group_sentence(groups[0][0]))
        else:
            _append_branch_groups(lines, groups)
        return

    label = _root_branch_label(root_status)
    if label is not None:
        _add_branch_group_path(groups, label, ".")

    if not groups:
        lines.append("Workspace status")
    elif len(groups) == 1:
        lines.append(_branch_group_sentence(groups[0][0]))
    else:
        _append_branch_groups(lines, groups)

    if getattr(root_status, "unborn", False):
        lines.append("No commits yet")


def _root_branch_label(root_status: Any) -> str | None:
    branch = getattr(root_status, "branch", None)
    if branch is not None:
        return branch
    if getattr(root_status, "detached", False):
        head = getattr(root_status, "head", None)
        return f"detached@{head[:12]}" if head else "detached"
    if getattr(root_status, "unborn", False):
        return "unborn"
    return None


def _add_branch_group_path(groups: list[tuple[str, list[str]]], label: str, path: str) -> None:
    for index, (group_label, paths) in enumerate(groups):
        if group_label == label:
            groups.pop(index)
            groups.insert(0, (label, [path, *paths]))
            return
    groups.insert(0, (label, [path]))


def _append_branch_groups(lines: list[str], groups: list[tuple[str, list[str]]]) -> None:
    for label, paths in groups:
        lines.append(f"{', '.join(paths)} {_branch_group_phrase(label)}")


def _branch_group_sentence(label: str) -> str:
    phrase = _branch_group_phrase(label)
    return f"{phrase[:1].upper()}{phrase[1:]}"


def _branch_group_phrase(label: str) -> str:
    if label == "unborn":
        return "have no commits yet"
    if label == "detached":
        return "HEAD detached"
    if label.startswith("detached@"):
        return f"detached at {label.removeprefix('detached@')}"
    return f"on branch {label}"


def _root_human_changes(workspace_status: Any) -> list[tuple[str, str, str]]:
    return [
        _human_change(change.index_status, change.worktree_status, change.workspace_path)
        for change in getattr(workspace_status, "root_file_changes", [])
    ]


def _member_human_changes(workspace_status: Any) -> list[tuple[str, str, str]]:
    return [
        _human_change(change.index_status, change.worktree_status, change.workspace_path)
        for change in getattr(workspace_status, "file_changes", [])
    ]


def _human_change(index_status: str, worktree_status: str, path: str) -> tuple[str, str, str]:
    if index_status == " " and worktree_status == "?":
        section = "untracked"
    elif index_status != " ":
        section = "staged"
    else:
        section = "unstaged"
    return section, _format_status_pair(index_status, worktree_status), path


def _format_status_pair(index_status: str, worktree_status: str) -> str:
    if index_status == " " and worktree_status == "?":
        return "??"
    return f"{index_status}{worktree_status}"


def _append_change_sections(lines: list[str], changes: list[tuple[str, str, str]]) -> None:
    _append_change_section(lines, changes, "staged", "Changes to be committed:")
    _append_change_section(lines, changes, "unstaged", "Changes not staged for commit:")
    _append_change_section(lines, changes, "untracked", "Untracked files:")


def _append_change_section(
    lines: list[str],
    changes: list[tuple[str, str, str]],
    section: str,
    header: str,
) -> None:
    section_changes = [change for change in changes if change[0] == section]
    if not section_changes:
        return
    _push_blank(lines)
    lines.append(header)
    lines.extend(f"  {status} {path}" for _, status, path in section_changes)


def _append_unmaterialized_notice(lines: list[str], response: Any) -> None:
    envelope = getattr(response, "response", None)
    members = getattr(envelope, "members", [])
    unmaterialized = [
        member
        for member in members
        if getattr(getattr(member, "state", None), "materialized", True) is False
    ]
    if not unmaterialized:
        return
    _push_blank(lines)
    lines.append(
        "Members not materialized (run `gwz materialize --lock` to complete the clone):"
    )
    lines.extend(f"  {member.member_path}" for member in unmaterialized)


def _append_status_issues(lines: list[str], response: Any) -> None:
    envelope = getattr(response, "response", None)
    issues: list[str] = []
    for member in getattr(envelope, "members", []):
        if getattr(getattr(member, "state", None), "materialized", True) is False:
            continue
        status = getattr(member, "status", None)
        error = getattr(member, "error", None)
        if getattr(status, "name", status) != "ok" or error is not None:
            issue = f"{member.member_path}: {_enum_label(status)}"
            if error is not None:
                issue += f" {_enum_label(error.code)}: {error.message}"
            issues.append(issue)
    issues.extend(
        f"{_enum_label(error.code)}: {error.message}"
        for error in getattr(envelope, "errors", [])
    )
    if not issues:
        return
    _push_blank(lines)
    lines.append("Issues:")
    lines.extend(f"  {issue}" for issue in issues)


def _append_suppressed_dirty_summary(
    lines: list[str],
    response: Any,
    workspace_status: Any,
) -> None:
    summary: list[str] = []
    root = getattr(workspace_status, "root_status", None)
    if (
        root is not None
        and getattr(root, "dirty", False)
        and not _root_human_changes(workspace_status)
    ):
        summary.append(
            "  workspace root: "
            f"{root.staged} staged, {root.unstaged} unstaged, {root.untracked} untracked"
        )

    envelope = getattr(response, "response", None)
    for member in getattr(envelope, "members", []):
        if getattr(getattr(member, "state", None), "materialized", True) is False:
            continue
        status = getattr(member, "git_status", None)
        if status is None or not getattr(status, "dirty", False):
            continue
        member_changes = [
            change
            for change in getattr(workspace_status, "file_changes", [])
            if change.member_id == member.member_id
        ]
        if member_changes:
            continue
        summary.append(
            f"  {member.member_path}: "
            f"{status.staged} staged, {status.unstaged} unstaged, {status.untracked} untracked"
        )

    if not summary:
        return
    _push_blank(lines)
    lines.append("Uncommitted changes (file detail omitted; re-run without --no-files):")
    lines.extend(summary)


def _render_branch_response(response: Any, repos: list[Any]) -> str:
    if all(_enum_name(repo.result) == "listed" for repo in repos):
        return _render_branch_listing_response(response, repos)

    lines = [_status_line(response)]
    for repo in repos:
        branch = repo.branch or repo.current_branch or "(detached)"
        line = f"{repo.member_id} {repo.member_path} {_enum_label(repo.result)} {branch}"
        if repo.head is not None:
            line += f" {repo.head}"
        if repo.source_ref is not None:
            line += f" from {repo.source_ref}"
        if repo.resulting_commit is not None:
            line += f" -> {repo.resulting_commit}"
        if repo.conflict_paths:
            line += f" conflicts: {','.join(repo.conflict_paths)}"
        lines.append(line)
    _append_errors(lines, response)
    return "\n".join(lines)


def _render_merge_response(response: Any) -> str:
    state = _enum_name(response.state).replace("_", "-")
    lines = ["action: merge", _status_line(response), f"state: {state}"]
    if state == "idle":
        lines.append("No coordinated merge is open.")
        return "\n".join(lines)

    lines.append(f"merge: {response.merge_id or 'unknown'} ({'open' if response.open else 'closed'})")
    lines.append(_merge_participant_counts(response.participant_counts))
    if response.publication_step is not None:
        lines.append(f"publication: {_enum_name(response.publication_step).replace('_', '-')}")
    lines.append("recovery: participant eligibility shown below")
    if response.operation_drift:
        lines.append("operation drift:")
        for drift in response.operation_drift:
            lines.append(
                f"  {_enum_name(drift.kind).replace('_', '-')}: {drift.message}"
            )
    if response.repos:
        lines.append("participants:")
    for repo in response.repos:
        state = _enum_name(repo.state).replace("_", "-")
        outcome = f"  {repo.path} ({repo.target_id})  {state}"
        if state == "planned" and repo.predicted is not None:
            prediction = {
                "up_to_date": "up-to-date",
                "fast_forward": "fast-forward",
                "true_merge": "merge commit",
                "unknown": "unknown",
            }[_enum_name(repo.predicted)]
            outcome += f" ({prediction})"
        lines.append(outcome)
        lines.append(f"    source: {repo.source_ref} @ {repo.source_commit}")
        lines.append(
            f"    recorded: branch {repo.target_branch}; before {repo.before_commit}; "
            f"result {repo.resulting_commit or '-'}"
        )
        lines.append(f"    live: commit {repo.live_commit or 'unknown'}")
        lines.append(
            "    recovery: continue "
            f"{_merge_eligibility_label(repo.continue_eligible)}; abort "
            f"{_merge_eligibility_label(repo.abort_eligible)}"
        )
        if repo.pending_action is not None:
            pending = repo.pending_action
            detail = f": {pending.message}" if pending.message else ""
            lines.append(
                "    pending action: "
                f"{_enum_name(pending.kind).replace('_', '-')} "
                f"({_enum_name(pending.state).replace('_', '-')}){detail}"
            )
        if repo.conflict_paths:
            lines.append(f"    conflicts: {', '.join(repo.conflict_paths)}")
        for drift in repo.drift:
            lines.append(
                f"    drift: {_enum_name(drift.kind).replace('_', '-')}: {drift.message}"
            )
        if repo.error is not None:
            lines.append(f"    error: {_enum_label(repo.error.code)}: {repo.error.message}")
    _append_errors(lines, response)
    return "\n".join(lines)


def _merge_participant_counts(counts: Any) -> str:
    values = (
        ("planned", counts.planned), ("up-to-date", counts.up_to_date),
        ("fast-forwarded", counts.fast_forwarded), ("merged", counts.merged),
        ("conflicted", counts.conflicted), ("failed", counts.failed),
        ("unattempted", counts.unattempted), ("continued", counts.continued),
        ("aborted", counts.aborted), ("rolled-back", counts.rolled_back),
    )
    details = "; ".join(f"{label} {count}" for label, count in values if count)
    return f"participants: total {counts.total}" + (f"; {details}" if details else "")


def _merge_eligibility_label(value: bool | None) -> str:
    return "eligible" if value is True else "blocked" if value is False else "unknown"


def _merge_response_json(response: Any) -> dict[str, Any]:
    envelope, counts = response.response, response.participant_counts
    meta = envelope.meta
    merge = {
        "merge_id": response.merge_id,
        "state": _enum_label(response.state),
        "open": response.open,
        "participant_counts": _json_fields(
            counts, "total", "planned", "up_to_date", "fast_forwarded", "merged",
            "conflicted", "failed", "unattempted", "continued", "aborted",
            "rolled_back",
        ),
        "repos": [_merge_repo_json(repo) for repo in response.repos],
        "operation_drift": [
            {"kind": _enum_label(drift.kind), "message": drift.message}
            for drift in response.operation_drift
        ],
        "preservation": (
            [_merge_preservation_json(entry) for entry in response.preservation]
            if response.preservation is not None
            else None
        ),
        "publication_step": (
            _enum_label(response.publication_step)
            if response.publication_step is not None
            else None
        ),
    }
    return {
        "kind": "response",
        "meta": {
            **_json_fields(meta, "request_id", "schema_version"),
            "action": _enum_label(meta.action),
            "aggregate_status": _enum_label(meta.aggregate_status),
            **_json_fields(meta, "operation_id", "message"),
        },
        "members": [json_default(member) for member in envelope.members],
        "errors": [_merge_error_json(error) for error in envelope.errors],
        "workspace_git_status": None,
        "branch_repos": None,
        "merge": merge,
        "stash_bundles": None,
    }


def _merge_repo_json(repo: Any) -> dict[str, Any]:
    value = _json_fields(
        repo, "target_id", "path", "source_ref", "source_commit", "target_branch",
        "before_commit", "resulting_commit", "live_commit", "prediction_complete",
        "conflict_paths", "continue_eligible", "abort_eligible",
    )
    value.update(target_kind=_enum_label(repo.target_kind), state=_enum_label(repo.state),
                 predicted=_enum_label(repo.predicted) if repo.predicted else None,
                 drift=[_merge_participant_drift_json(drift) for drift in repo.drift],
                 error=_merge_error_json(repo.error) if repo.error else None,
                 pending_action=(
                     {
                         "kind": _enum_label(repo.pending_action.kind),
                         "state": _enum_label(repo.pending_action.state),
                         "message": repo.pending_action.message,
                     }
                     if repo.pending_action is not None else None
                 ))
    return value


def _merge_participant_drift_json(drift: Any) -> dict[str, Any]:
    value = _json_fields(
        drift, "message", "expected_branch", "live_branch", "expected_head",
        "live_head", "expected_merge_head", "live_merge_head",
    )
    return {"kind": _enum_label(drift.kind), **value}


def _merge_preservation_json(entry: Any) -> dict[str, Any]:
    return _json_fields(
        entry, "target_id", "path", "backup_ref", "backup_commit", "stash_id",
        "stash_object_id",
    )


def _merge_error_json(error: Any) -> dict[str, Any]:
    return {
        **_json_fields(error, "message", "member_id", "member_path", "detail"),
        "code": _enum_label(error.code),
        "target_kind": (
            _enum_label(error.target_kind) if error.target_kind is not None else None
        ),
    }


def _json_fields(value: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(value, name) for name in names}


def _render_branch_listing_response(response: Any, repos: list[Any]) -> str:
    lines = _branch_listing_lines(repos)
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    aggregate = getattr(meta, "aggregate_status", None)
    if _enum_name(aggregate) != "ok":
        lines.insert(0, _status_line(response))
    _append_errors(lines, response)
    return "\n".join(lines)


def _branch_listing_lines(repos: list[Any]) -> list[str]:
    if not repos:
        return ["no branches"]

    short_name_counts = _branch_repo_short_name_counts(repos)
    groups: dict[tuple[str, bool], set[str]] = {}
    for repo in repos:
        branch = repo.branch or repo.current_branch or "(detached)"
        is_current = repo.current_branch == branch
        groups.setdefault((branch, is_current), set()).add(
            _branch_repo_label(repo, short_name_counts)
        )

    return [
        f"{'*' if is_current else ''}{branch}: {' '.join(sorted(labels))}"
        for (branch, is_current), labels in sorted(
            groups.items(), key=lambda item: (item[0][0], not item[0][1])
        )
    ]


def _branch_repo_short_name_counts(repos: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in {repo.member_path for repo in repos}:
        short_name = _member_short_name(path)
        counts[short_name] = counts.get(short_name, 0) + 1
    return counts


def _branch_repo_label(repo: Any, short_name_counts: dict[str, int]) -> str:
    short_name = _member_short_name(repo.member_path)
    return repo.member_path if short_name_counts.get(short_name, 0) > 1 else short_name


def _member_short_name(path: str) -> str:
    trimmed = path.rstrip("/\\")
    name = trimmed.replace("\\", "/").rsplit("/", 1)[-1]
    return name.removesuffix(".git")


def _enum_name(value: Any) -> str:
    return getattr(value, "name", str(value))


def _render_stash_response(response: Any, bundles: list[Any]) -> str:
    lines = [_status_line(response)]
    for bundle in bundles:
        members = len(bundle.members)
        lines.append(
            f"{bundle.stash_id} {bundle.created_at} "
            f"({members} member{_plural(members)})"
        )
    envelope = getattr(response, "response", None)
    for member in getattr(envelope, "members", []):
        lines.append(f"{member.member_id} {member.member_path} {_enum_label(member.status)}")
    _append_errors(lines, response)
    return "\n".join(lines)


def _status_line(response: Any) -> str:
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    return f"status: {_enum_label(getattr(meta, 'aggregate_status', None))}"


def _append_errors(lines: list[str], response: Any) -> None:
    envelope = getattr(response, "response", None)
    for error in getattr(envelope, "errors", []):
        lines.append(f"{_enum_label(error.code)}: {error.message}")


def _push_blank(lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")
