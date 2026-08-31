"""Rust-compatible rendering for commit-log protocol records."""

from __future__ import annotations

import json

from ..errors import GwzBridgeError
from ..protocol.generated import (
    LogDegradation,
    LogDegradationReason,
    LogEntry,
    LogMergeKind,
    LogOutputRecord,
    LogOutputRecordKind,
)


_DEGRADATION_LABELS = {
    LogDegradationReason.repository_unreadable: "repository unreadable",
    LogDegradationReason.repository_missing: "repository missing",
    LogDegradationReason.unborn: "unborn history",
    LogDegradationReason.revision_unresolved: "revision unresolved",
    LogDegradationReason.snapshot_entry_missing: "snapshot entry missing",
    LogDegradationReason.lock_entry_missing: "lock entry missing",
    LogDegradationReason.unsupported_source_kind: "unsupported source kind",
}
_DEGRADATION_TOKENS = {
    LogDegradationReason.repository_unreadable: "repository_unreadable",
    LogDegradationReason.repository_missing: "repository_missing",
    LogDegradationReason.unborn: "unborn",
    LogDegradationReason.revision_unresolved: "revision_unresolved",
    LogDegradationReason.snapshot_entry_missing: "snapshot_entry_missing",
    LogDegradationReason.lock_entry_missing: "lock_entry_missing",
    LogDegradationReason.unsupported_source_kind: "unsupported_source_kind",
}


def log_color_enabled(color: str, stdout_is_tty: bool) -> bool:
    if color == "always":
        return True
    if color == "never":
        return False
    return stdout_is_tty


def render_log_entry(entry: LogEntry, *, full: bool, color: bool) -> str:
    return _render_full_entry(entry, color) if full else _render_compact_entry(entry, color)


def render_log_degradation(record: LogDegradation, *, color: bool) -> str:
    path = _sanitize_inline(record.member_path or record.member_id)
    reason = _DEGRADATION_LABELS[record.reason]
    if record.operand is not None:
        reason += f" for '{_sanitize_inline(record.operand)}'"
    if record.message:
        reason += f" — {_sanitize_inline(record.message)}"
    label = _colorize("gwz log: degraded", "33", color)
    return f"{label} {path}: {reason}"


def render_log_record_json(record: LogOutputRecord) -> str:
    return json.dumps(
        _log_record_json(record),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _log_record_json(record: LogOutputRecord) -> dict[str, object]:
    if (
        record.kind is LogOutputRecordKind.entry
        and record.entry is not None
        and record.degradation is None
    ):
        return _entry_json(record.entry)
    if (
        record.kind is LogOutputRecordKind.degradation
        and record.entry is None
        and record.degradation is not None
    ):
        return _degradation_json(record.degradation)
    label = "entry" if record.kind is LogOutputRecordKind.entry else "degradation"
    raise _invalid_record(f"commit-log {label} record has inconsistent payload arms")


def _render_compact_entry(entry: LogEntry, color: bool) -> str:
    date = _format_date(
        entry.committer_timestamp_seconds,
        entry.committer.timezone_offset_minutes,
    )
    members = _compact_member_set(entry)
    commit = entry.members[0].commit[:12] if entry.members else "????????????"
    subject = _sanitize_inline(entry.subject)
    return " ".join(
        [
            _colorize(date, "2", color),
            _colorize(members, "36", color),
            _colorize(_sanitize_inline(commit), "33", color),
            subject,
        ]
    )


def _compact_member_set(entry: LogEntry) -> str:
    if len(entry.members) == 1:
        return _sanitize_inline(entry.members[0].member_path)
    if len(entry.members) <= 3:
        paths = ", ".join(_sanitize_inline(member.member_path) for member in entry.members)
        return f"[{paths}]"
    non_root = sum(member.member_id != "@root" for member in entry.members)
    if non_root < len(entry.members):
        return f"[root+{non_root}]"
    return f"[{len(entry.members)} members]"


def _render_full_entry(entry: LogEntry, color: bool) -> str:
    representative = _sanitize_inline(entry.members[0].commit) if entry.members else "unknown"
    rows = [
        (
            _sanitize_inline(member.member_id),
            _sanitize_inline(member.member_path),
            _sanitize_inline(member.commit),
        )
        for member in entry.members
    ]
    id_width = max([2, *(len(member_id) for member_id, _, _ in rows)])
    path_width = max([4, *(len(path) for _, path, _ in rows)])
    output = [
        _colorize(f"commit {representative}", "33", color),
        _colorize("Members:", "36", color),
        f"    {'ID':<{id_width}}  {'PATH':<{path_width}}  COMMIT",
    ]
    output.extend(
        f"    {member_id:<{id_width}}  {path:<{path_width}}  {commit}"
        for member_id, path, commit in rows
    )
    output.extend(
        [
            "Author: "
            f"{_sanitize_inline(entry.author.name)} "
            f"<{_sanitize_inline(entry.author.email)}>",
            "Date:   "
            + _format_date(
                entry.author_timestamp_seconds,
                entry.author.timezone_offset_minutes,
            ),
            "",
        ]
    )
    message = _sanitize_inline(entry.subject)
    if entry.body is not None:
        message += "\n" + _sanitize_multiline(entry.body)
    output.extend(f"    {line}" for line in message.split("\n"))
    return "\n".join(output)


def _format_date(seconds: int, offset_minutes: int | None) -> str:
    offset = offset_minutes or 0
    local_seconds = seconds + offset * 60
    days, seconds_in_day = divmod(local_seconds, 86_400)
    year, month, day = _civil_from_days(days)
    hour, remainder = divmod(seconds_in_day, 3_600)
    minute, second = divmod(remainder, 60)
    sign = "-" if offset < 0 else "+"
    absolute_offset = abs(offset)
    offset_hour, offset_minute = divmod(absolute_offset, 60)
    return (
        f"{_format_year(year)}-{month:02d}-{day:02d} "
        f"{hour:02d}:{minute:02d}:{second:02d} "
        f"{sign}{offset_hour:02d}{offset_minute:02d}"
    )


def _civil_from_days(days_since_epoch: int) -> tuple[int, int, int]:
    shifted = days_since_epoch + 719_468
    era = shifted // 146_097
    day_of_era = shifted - era * 146_097
    year_of_era = (
        day_of_era
        - day_of_era // 1_460
        + day_of_era // 36_524
        - day_of_era // 146_096
    ) // 365
    year = year_of_era + era * 400
    day_of_year = day_of_era - (
        365 * year_of_era + year_of_era // 4 - year_of_era // 100
    )
    month_prime = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * month_prime + 2) // 5 + 1
    month = month_prime + (3 if month_prime < 10 else -9)
    year += int(month <= 2)
    return year, month, day


def _format_year(year: int) -> str:
    return f"-{abs(year):04d}" if year < 0 else f"{year:04d}"


def _sanitize_inline(value: str) -> str:
    return _sanitize(value, preserve_newlines=False)


def _sanitize_multiline(value: str) -> str:
    return _sanitize(value, preserve_newlines=True)


def _sanitize(value: str, *, preserve_newlines: bool) -> str:
    rendered = []
    for character in value:
        if character == "\n" and preserve_newlines:
            rendered.append(character)
        elif character == "\t":
            rendered.append(" ")
        elif ord(character) <= 0x1F:
            rendered.append("�")
        else:
            rendered.append(character)
    return "".join(rendered)


def _colorize(value: str, code: str, color: bool) -> str:
    return f"\x1b[{code}m{value}\x1b[0m" if color else value


def _entry_json(entry: LogEntry) -> dict[str, object]:
    value: dict[str, object] = {
        "author": _identity_json(
            entry.author.name,
            entry.author.email,
            entry.author_timestamp_seconds,
            entry.author.timezone_offset_minutes,
            "author",
        ),
        "committer": _identity_json(
            entry.committer.name,
            entry.committer.email,
            entry.committer_timestamp_seconds,
            entry.committer.timezone_offset_minutes,
            "committer",
        ),
        "members": [
            {
                "hash": member.commit,
                "member_id": member.member_id,
                "member_path": member.member_path,
                "parents": member.parents,
            }
            for member in entry.members
        ],
        "provenance": _provenance_token(entry),
        "record": "entry",
        "subject": entry.subject,
    }
    if entry.body is not None:
        value["body"] = entry.body
    if entry.lossy is True:
        value["lossy"] = True
    return value


def _identity_json(
    name: str,
    email: str,
    seconds: int,
    offset: int | None,
    label: str,
) -> dict[str, object]:
    if offset is None:
        raise _invalid_record(
            f"commit-log {label} identity has no recorded timezone offset"
        )
    return {
        "email": email,
        "name": name,
        "time": {"offset_min": offset, "time": seconds},
    }


def _provenance_token(entry: LogEntry) -> str:
    kind = entry.provenance.kind
    marker = entry.provenance.gwz_commit_id
    if kind is LogMergeKind.none and marker == "marker-invalid":
        return "marker-invalid"
    if kind is LogMergeKind.none and marker is None:
        return "none"
    if kind is LogMergeKind.heuristic and marker is None:
        return "heuristic"
    if kind is LogMergeKind.marker and marker is not None:
        return f"marker:{marker}"
    raise _invalid_record("commit-log entry has inconsistent merge provenance")


def _degradation_json(record: LogDegradation) -> dict[str, object]:
    return {
        "member_id": record.member_id,
        "member_path": record.member_path,
        "message": record.message,
        "operand": record.operand,
        "reason": _DEGRADATION_TOKENS[record.reason],
        "record": "degradation",
    }


def _invalid_record(message: str) -> GwzBridgeError:
    return GwzBridgeError(message, code="InternalError")
