"""`gwz fetch`'s human report, matching the Rust CLI line for line.

One line per repository in envelope order: the repository's id and path, what
its remote-tracking ref did, and the ref it tracks with `+ahead -behind`
(gwz-cli dev-docs/GwzFetchPlan.md §3.3).
"""

from __future__ import annotations

from typing import Any

from .common import enum_label, enum_name, status_line

__all__ = ["render_fetch_response"]


def _short(commit: str) -> str:
    """Abbreviate an object id the way git renders one: seven characters."""

    return commit[:7]


def _movement(repo: Any) -> str:
    result = enum_name(repo.result)
    if result == "updated":
        before, after = repo.before, repo.after
        if before and after:
            return f"{_short(before)}..{_short(after)}"
        if after:
            # A tracking ref that did not exist before this fetch: there is no
            # left-hand side to show, so say where it now points.
            return f"new {_short(after)}"
        return "updated"
    if result == "unchanged":
        return "no change"
    if result == "no_upstream":
        return "no upstream"
    if result == "planned":
        # `--dry-run` only: the repository was not contacted, so the row says
        # what would happen rather than what did.
        remote = getattr(repo, "remote", None)
        return f"would contact {remote}" if remote else "would contact"
    return "failed"


def render_fetch_response(response: Any, repos: list[Any]) -> str:
    lines = [status_line(response)]
    envelope = getattr(response, "response", None)
    members = getattr(envelope, "members", None) or []
    errors = {
        member.member_id: member.error
        for member in members
        if getattr(member, "error", None) is not None
    }
    movements = [_movement(repo) for repo in repos]
    id_width = max((len(repo.member_id) for repo in repos), default=0)
    path_width = max((len(repo.member_path) for repo in repos), default=0)
    movement_width = max((len(movement) for movement in movements), default=0)
    for repo, movement in zip(repos, movements):
        line = (
            f"{repo.member_id:<{id_width}}  "
            f"{repo.member_path:<{path_width}}  "
            f"{movement:<{movement_width}}"
        )
        if repo.upstream is not None and repo.ahead is not None and repo.behind is not None:
            tracked = repo.upstream
            prefix = "refs/remotes/"
            if tracked.startswith(prefix):
                tracked = tracked[len(prefix) :]
            line += f"  ({tracked}, +{repo.ahead} -{repo.behind})"
        error = errors.get(repo.member_id)
        if enum_name(repo.result) == "failed" and error is not None:
            line += f"  {enum_label(error.code)}: {error.message}"
        # The movement column is padded so the tracking column lines up; a row
        # with nothing after it must not carry that padding off the end.
        lines.append(line.rstrip())
    for error in getattr(envelope, "errors", None) or []:
        lines.append(f"{enum_label(error.code)}: {error.message}")
    return "\n".join(lines)
