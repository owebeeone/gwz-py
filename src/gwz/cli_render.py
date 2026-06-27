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


def render_response(
    response: Any,
    *,
    json_mode: bool = False,
    local_paths: bool = False,
    porcelain: bool = False,
) -> str:
    if json_mode:
        return json.dumps(response, default=json_default, sort_keys=True)

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


def render_error(error: BaseException) -> str:
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
