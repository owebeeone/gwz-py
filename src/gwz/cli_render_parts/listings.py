from __future__ import annotations

from typing import Any

from .common import append_errors, enum_label, enum_name, plural, status_line


def render_snapshot_listing(snapshots: list[Any]) -> str:
    if not snapshots:
        return "no snapshots"
    lines = [f"{len(snapshots)} snapshot{plural(len(snapshots))}:"]
    for snapshot in snapshots:
        lines.append(
            f"  {snapshot.name}\t{snapshot.created_at}\t{snapshot.created_by}"
            f"\t({snapshot.members} member{plural(snapshot.members)})"
        )
    return "\n".join(lines)


def render_tag_listing(tags: list[Any]) -> str:
    if not tags:
        return "no tags"
    lines = [f"{len(tags)} tag{plural(len(tags))}:"]
    for tag in tags:
        lines.append(f"  {tag.name}\t({tag.members} member{plural(tag.members)})")
    return "\n".join(lines)


def render_member_listing(members: list[Any], *, local_paths: bool) -> str:
    return "\n".join(
        member.path if local_paths else member.abspath for member in members
    )


def render_branch_response(response: Any, repos: list[Any]) -> str:
    if all(enum_name(repo.result) == "listed" for repo in repos):
        return render_branch_listing_response(response, repos)

    lines = [status_line(response)]
    for repo in repos:
        branch = repo.branch or repo.current_branch or "(detached)"
        line = (
            f"{repo.member_id} {repo.member_path} "
            f"{enum_label(repo.result)} {branch}"
        )
        if repo.head is not None:
            line += f" {repo.head}"
        if repo.source_ref is not None:
            line += f" from {repo.source_ref}"
        if repo.resulting_commit is not None:
            line += f" -> {repo.resulting_commit}"
        if repo.conflict_paths:
            line += f" conflicts: {','.join(repo.conflict_paths)}"
        lines.append(line)
    append_errors(lines, response)
    return "\n".join(lines)


def render_branch_listing_response(response: Any, repos: list[Any]) -> str:
    lines = branch_listing_lines(repos)
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    aggregate = getattr(meta, "aggregate_status", None)
    if enum_name(aggregate) != "ok":
        lines.insert(0, status_line(response))
    append_errors(lines, response)
    return "\n".join(lines)


def branch_listing_lines(repos: list[Any]) -> list[str]:
    if not repos:
        return ["no branches"]

    short_name_counts = branch_repo_short_name_counts(repos)
    groups: dict[tuple[str, bool], set[str]] = {}
    for repo in repos:
        branch = repo.branch or repo.current_branch or "(detached)"
        is_current = repo.current_branch == branch
        groups.setdefault((branch, is_current), set()).add(
            branch_repo_label(repo, short_name_counts)
        )

    return [
        f"{'*' if is_current else ''}{branch}: {' '.join(sorted(labels))}"
        for (branch, is_current), labels in sorted(
            groups.items(),
            key=lambda item: (item[0][0], not item[0][1]),
        )
    ]


def branch_repo_short_name_counts(repos: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in {repo.member_path for repo in repos}:
        short_name = member_short_name(path)
        counts[short_name] = counts.get(short_name, 0) + 1
    return counts


def branch_repo_label(repo: Any, short_name_counts: dict[str, int]) -> str:
    short_name = member_short_name(repo.member_path)
    return (
        repo.member_path
        if short_name_counts.get(short_name, 0) > 1
        else short_name
    )


def member_short_name(path: str) -> str:
    trimmed = path.rstrip("/\\")
    name = trimmed.replace("\\", "/").rsplit("/", 1)[-1]
    return name.removesuffix(".git")


def render_stash_response(response: Any, bundles: list[Any]) -> str:
    lines = [status_line(response)]
    for bundle in bundles:
        members = len(bundle.members)
        lines.append(
            f"{bundle.stash_id} {bundle.created_at} "
            f"({members} member{plural(members)})"
        )
    envelope = getattr(response, "response", None)
    for member in getattr(envelope, "members", []):
        lines.append(
            f"{member.member_id} {member.member_path} "
            f"{enum_label(member.status)}"
        )
    append_errors(lines, response)
    return "\n".join(lines)
