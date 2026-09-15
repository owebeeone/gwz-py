"""Human rendering of push `noop` reasons, which core carries in `planned.message`
(D11), of the summary line for repositories a push did not check, and of the
errors on rows a push failed or refused."""

from __future__ import annotations

from typing import Any

from ..protocol.generated import PlannedAction
from .common import enum_label, is_response


def is_unchecked_reason(reason: str | None) -> bool:
    """Whether a `noop` reason comes from the last fetch or push rather than a read
    in this operation. The one place push reasons are matched."""
    return reason is not None and reason.endswith("as of the last fetch or push")


def unchecked_summary(response: Any) -> str | None:
    """One line counting the push rows that were not checked, when there are any."""
    count = sum(1 for _path, reason in _noop_reasons(response) if is_unchecked_reason(reason))
    if count == 0:
        return None
    noun, verb = ("repository", "was") if count == 1 else ("repositories", "were")
    return (
        f"{count} {noun} unchanged since the last fetch or push {verb} not checked "
        "for changes; --check-remotes to verify"
    )


def noop_reason_human_lines(response: Any) -> list[str]:
    """One line per push row that pushed nothing, with its reason, for `--verbose`."""
    return [f"{path}: {reason}" for path, reason in _noop_reasons(response)]


def error_human_lines(response: Any) -> list[str]:
    """One line per push row that failed or was refused, with core's message. A root
    refused for a missing dependency names the member, the commit and the remedies
    (push plan §3.6)."""
    if not is_response(response, "PushResponse", "push"):
        return []
    lines = []
    for member in getattr(getattr(response, "response", None), "members", None) or []:
        error = getattr(member, "error", None)
        if error is not None:
            lines.append(f"{member.member_path}: {enum_label(error.code)}: {error.message}")
    return lines


def _noop_reasons(response: Any) -> list[tuple[str, str]]:
    if not is_response(response, "PushResponse", "push"):
        return []
    reasons = []
    for member in getattr(getattr(response, "response", None), "members", None) or []:
        planned = getattr(member, "planned", None)
        if planned is not None and planned.action is PlannedAction.noop and planned.message:
            reasons.append((member.member_path, planned.message))
    return reasons
