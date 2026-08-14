from __future__ import annotations

from typing import Any

from .common import append_errors, enum_label, enum_name, status_line


def render_merge_response(response: Any) -> str:
    state = enum_name(response.state).replace("_", "-")
    lines = ["action: merge", status_line(response), f"state: {state}"]
    if state == "idle":
        lines.append("No coordinated merge is open.")
        return "\n".join(lines)

    lines.append(
        f"merge: {response.merge_id or 'unknown'} "
        f"({'open' if response.open else 'closed'})"
    )
    if response.record is not None:
        record = response.record
        lines.append(
            f"record: {enum_name(record.source_version).replace('_', '-')} "
            f"({'archived' if record.archived else 'open'})"
        )
        if record.terminal_outcome is not None:
            lines.append(
                "terminal outcome: "
                f"{enum_name(record.terminal_outcome).replace('_', '-')}"
            )
        if record.acceptance is not None:
            lines.append(
                f"acceptance: {enum_name(record.acceptance.kind).replace('_', '-')}"
            )
            if record.acceptance.missing_gaps:
                lines.append(
                    "acceptance gaps: "
                    + ", ".join(
                        enum_name(gap).replace("_", "-")
                        for gap in record.acceptance.missing_gaps
                    )
                )
        if record.recovery is not None:
            recovery = record.recovery
            lines.append(
                "record recovery: "
                f"{enum_name(recovery.base_phase).replace('_', '-')} from "
                f"{enum_name(recovery.origin_state).replace('_', '-')}; resume "
                f"{enum_name(recovery.resume_action).replace('_', '-')}"
            )
    lines.append(merge_participant_counts(response.participant_counts))
    if response.publication_step is not None:
        lines.append(
            f"publication: {enum_name(response.publication_step).replace('_', '-')}"
        )
    if response.open:
        lines.extend(
            [
                "recovery commands:",
                "  inspect:  gwz-py merge --status",
                "  continue: gwz-py merge --continue",
                "  abort:    gwz-py merge --abort",
                "  preserve: gwz-py merge --abort --preserve",
            ]
        )
    if response.preservation:
        lines.append("remaining preservation artifacts:")
        for entry in response.preservation:
            lines.append(f"  {entry.path} ({entry.target_id})")
            if entry.backup_ref is not None and entry.backup_commit is not None:
                lines.append(
                    f"    backup ref: {entry.backup_ref} @ {entry.backup_commit}"
                )
            if entry.stash_id is not None and entry.stash_object_id is not None:
                lines.append(f"    stash: {entry.stash_id} @ {entry.stash_object_id}")
    if response.operation_drift:
        lines.append("operation drift:")
        for drift in response.operation_drift:
            lines.append(
                f"  {enum_name(drift.kind).replace('_', '-')}: {drift.message}"
            )
    if response.repos:
        lines.append("participants:")
    for repo in response.repos:
        state = enum_name(repo.state).replace("_", "-")
        outcome = f"  {repo.path} ({repo.target_id})  {state}"
        if state == "planned" and repo.predicted is not None:
            prediction = {
                "up_to_date": "up-to-date",
                "fast_forward": "fast-forward",
                "true_merge": "merge commit",
                "unknown": "unknown",
            }[enum_name(repo.predicted)]
            outcome += f" ({prediction})"
        lines.append(outcome)
        lines.append(f"    source: {repo.source_ref} @ {repo.source_commit}")
        lines.append(
            f"    recorded: branch {repo.target_branch}; "
            f"before {repo.before_commit}; result {repo.resulting_commit or '-'}"
        )
        lines.append(f"    live: commit {repo.live_commit or 'unknown'}")
        lines.append(
            "    recovery: continue "
            f"{merge_eligibility_label(repo.continue_eligible)}; abort "
            f"{merge_eligibility_label(repo.abort_eligible)}"
        )
        if repo.pending_action is not None:
            pending = repo.pending_action
            detail = f": {pending.message}" if pending.message else ""
            lines.append(
                "    pending action: "
                f"{enum_name(pending.kind).replace('_', '-')} "
                f"({enum_name(pending.state).replace('_', '-')}){detail}"
            )
        if repo.conflict_paths:
            lines.append(f"    conflicts: {', '.join(repo.conflict_paths)}")
        for drift in repo.drift:
            lines.append(
                f"    drift: {enum_name(drift.kind).replace('_', '-')}: "
                f"{drift.message}"
            )
        if repo.error is not None:
            lines.append(
                f"    error: {enum_label(repo.error.code)}: {repo.error.message}"
            )
    append_errors(lines, response)
    return "\n".join(lines)


def merge_participant_counts(counts: Any) -> str:
    values = (
        ("planned", counts.planned),
        ("up-to-date", counts.up_to_date),
        ("fast-forwarded", counts.fast_forwarded),
        ("merged", counts.merged),
        ("conflicted", counts.conflicted),
        ("failed", counts.failed),
        ("unattempted", counts.unattempted),
        ("continued", counts.continued),
        ("aborted", counts.aborted),
        ("rolled-back", counts.rolled_back),
    )
    details = "; ".join(f"{label} {count}" for label, count in values if count)
    return f"participants: total {counts.total}" + (
        f"; {details}" if details else ""
    )


def merge_eligibility_label(value: bool | None) -> str:
    return "eligible" if value is True else "blocked" if value is False else "unknown"
