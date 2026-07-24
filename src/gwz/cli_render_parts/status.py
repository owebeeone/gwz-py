from __future__ import annotations

from typing import Any

from .common import enum_label, push_blank


def render_status_porcelain(workspace_status: Any) -> str:
    changes = [
        porcelain_change(
            change.index_status,
            change.worktree_status,
            change.workspace_path,
        )
        for change in getattr(workspace_status, "root_file_changes", [])
    ]
    changes.extend(
        porcelain_change(
            change.index_status,
            change.worktree_status,
            change.workspace_path,
        )
        for change in getattr(workspace_status, "file_changes", [])
    )
    return "\n".join(changes)


def porcelain_change(index_status: str, worktree_status: str, path: str) -> str:
    return f"{format_status_pair(index_status, worktree_status)} {path}"


def render_status_response(response: Any, workspace_status: Any) -> str:
    lines: list[str] = []
    append_branch_summary(lines, workspace_status)
    changes = root_human_changes(workspace_status)
    changes.extend(member_human_changes(workspace_status))
    append_change_sections(lines, changes)
    append_unmaterialized_notice(lines, response)
    append_status_issues(lines, response)
    append_suppressed_dirty_summary(lines, response, workspace_status)
    if not lines:
        lines.append("nothing to commit, working tree clean")
    return "\n".join(lines)


def append_branch_summary(lines: list[str], workspace_status: Any) -> None:
    groups = [
        (group.label, list(group.member_paths))
        for group in getattr(workspace_status, "branch_groups", [])
    ]
    root_status = getattr(workspace_status, "root_status", None)

    if root_status is None:
        if not groups:
            lines.append("Workspace status")
        elif len(groups) == 1:
            lines.append(branch_group_sentence(groups[0][0]))
        else:
            append_branch_groups(lines, groups)
        return

    label = root_branch_label(root_status)
    if label is not None:
        add_branch_group_path(groups, label, ".")

    if not groups:
        lines.append("Workspace status")
    elif len(groups) == 1:
        lines.append(branch_group_sentence(groups[0][0]))
    else:
        append_branch_groups(lines, groups)

    if getattr(root_status, "unborn", False):
        lines.append("No commits yet")


def root_branch_label(root_status: Any) -> str | None:
    branch = getattr(root_status, "branch", None)
    if branch is not None:
        return branch
    if getattr(root_status, "detached", False):
        head = getattr(root_status, "head", None)
        return f"detached@{head[:12]}" if head else "detached"
    if getattr(root_status, "unborn", False):
        return "unborn"
    return None


def add_branch_group_path(
    groups: list[tuple[str, list[str]]],
    label: str,
    path: str,
) -> None:
    for index, (group_label, paths) in enumerate(groups):
        if group_label == label:
            groups.pop(index)
            groups.insert(0, (label, [path, *paths]))
            return
    groups.insert(0, (label, [path]))


def append_branch_groups(
    lines: list[str],
    groups: list[tuple[str, list[str]]],
) -> None:
    for label, paths in groups:
        lines.append(f"{', '.join(paths)} {branch_group_phrase(label)}")


def branch_group_sentence(label: str) -> str:
    phrase = branch_group_phrase(label)
    return f"{phrase[:1].upper()}{phrase[1:]}"


def branch_group_phrase(label: str) -> str:
    if label == "unborn":
        return "have no commits yet"
    if label == "detached":
        return "HEAD detached"
    if label.startswith("detached@"):
        return f"detached at {label.removeprefix('detached@')}"
    return f"on branch {label}"


def root_human_changes(workspace_status: Any) -> list[tuple[str, str, str]]:
    return [
        human_change(
            change.index_status,
            change.worktree_status,
            change.workspace_path,
        )
        for change in getattr(workspace_status, "root_file_changes", [])
    ]


def member_human_changes(workspace_status: Any) -> list[tuple[str, str, str]]:
    return [
        human_change(
            change.index_status,
            change.worktree_status,
            change.workspace_path,
        )
        for change in getattr(workspace_status, "file_changes", [])
    ]


def human_change(
    index_status: str,
    worktree_status: str,
    path: str,
) -> tuple[str, str, str]:
    if index_status == " " and worktree_status == "?":
        section = "untracked"
    elif index_status != " ":
        section = "staged"
    else:
        section = "unstaged"
    return section, format_status_pair(index_status, worktree_status), path


def format_status_pair(index_status: str, worktree_status: str) -> str:
    if index_status == " " and worktree_status == "?":
        return "??"
    return f"{index_status}{worktree_status}"


def append_change_sections(
    lines: list[str],
    changes: list[tuple[str, str, str]],
) -> None:
    append_change_section(lines, changes, "staged", "Changes to be committed:")
    append_change_section(
        lines,
        changes,
        "unstaged",
        "Changes not staged for commit:",
    )
    append_change_section(lines, changes, "untracked", "Untracked files:")


def append_change_section(
    lines: list[str],
    changes: list[tuple[str, str, str]],
    section: str,
    header: str,
) -> None:
    section_changes = [change for change in changes if change[0] == section]
    if not section_changes:
        return
    push_blank(lines)
    lines.append(header)
    lines.extend(f"  {status} {path}" for _, status, path in section_changes)


def append_unmaterialized_notice(lines: list[str], response: Any) -> None:
    envelope = getattr(response, "response", None)
    members = getattr(envelope, "members", [])
    unmaterialized = [
        member
        for member in members
        if getattr(getattr(member, "state", None), "materialized", True) is False
    ]
    if not unmaterialized:
        return
    push_blank(lines)
    lines.append(
        "Members not materialized (run `gwz materialize --lock` to complete the clone):"
    )
    lines.extend(f"  {member.member_path}" for member in unmaterialized)


def append_status_issues(lines: list[str], response: Any) -> None:
    envelope = getattr(response, "response", None)
    issues: list[str] = []
    for member in getattr(envelope, "members", []):
        if getattr(getattr(member, "state", None), "materialized", True) is False:
            continue
        status = getattr(member, "status", None)
        error = getattr(member, "error", None)
        if getattr(status, "name", status) != "ok" or error is not None:
            issue = f"{member.member_path}: {enum_label(status)}"
            if error is not None:
                issue += f" {enum_label(error.code)}: {error.message}"
            issues.append(issue)
    issues.extend(
        f"{enum_label(error.code)}: {error.message}"
        for error in getattr(envelope, "errors", [])
    )
    if not issues:
        return
    push_blank(lines)
    lines.append("Issues:")
    lines.extend(f"  {issue}" for issue in issues)


def append_suppressed_dirty_summary(
    lines: list[str],
    response: Any,
    workspace_status: Any,
) -> None:
    summary: list[str] = []
    root = getattr(workspace_status, "root_status", None)
    if (
        root is not None
        and getattr(root, "dirty", False)
        and not root_human_changes(workspace_status)
    ):
        summary.append(
            "  workspace root: "
            f"{root.staged} staged, {root.unstaged} unstaged, "
            f"{root.untracked} untracked"
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
            f"{status.staged} staged, {status.unstaged} unstaged, "
            f"{status.untracked} untracked"
        )

    if not summary:
        return
    push_blank(lines)
    lines.append("Uncommitted changes (file detail omitted; re-run without --no-files):")
    lines.extend(summary)
