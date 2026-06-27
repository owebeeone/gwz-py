from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .cli_shared import CliUsageError, CommandContext, CommandRegistry
from .errors import GwzBridgeError
from .protocol.generated import (
    ActionKind,
    AggregateStatus,
    CloneWorkspaceResponse,
    ExecMode,
    ExecResponse,
    ExecResult,
    EventKind,
    MemberEntry,
    OperationEvent,
    OperationResult,
    ResponseEnvelope,
    ResponseMeta,
)


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "forall",
        help="Run a command in each member",
        configure=configure_forall,
        handler=handle_forall,
    )
    registry.register(
        "clone",
        help="Clone a workspace and materialize its members",
        configure=configure_clone,
        handler=handle_clone,
    )


def configure_forall(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", dest="command_string", help="Run a shell command string")
    parser.add_argument("--no-banner", action="store_true", help="Suppress per-member banners")
    parser.add_argument("tokens", nargs=argparse.REMAINDER, help="[projects...] -- <cmd> or [projects...] -c <string>")


async def handle_forall(context: CommandContext) -> ExecResponse:
    if context.args.json:
        raise CliUsageError("forall does not support --json")
    projects, mode, command = _forall_invocation(context.args.tokens, context.args.command_string)
    ls_meta = dict(context.meta)
    if projects:
        ls_meta["targets"] = [*ls_meta.get("targets", ()), *projects]
    listed = await context.client.ls(include_unmaterialized=False, **ls_meta)
    members = listed.members or []
    results = _run_forall(
        members=members,
        mode=mode,
        command=command,
        continue_on_fail=bool(context.meta.get("partial")),
        no_banner=context.args.no_banner,
        root=_workspace_root(context),
    )
    aggregate_status = (
        AggregateStatus.ok if all(_result_ok(result) for result in results) else AggregateStatus.failed
    )
    message = "ok" if aggregate_status is AggregateStatus.ok else "one or more member commands failed"
    meta = context.client.meta(**context.meta)
    return ExecResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id=meta.request_id,
                schema_version=meta.schema_version,
                action=ActionKind.forall,
                aggregate_status=aggregate_status,
                operation_id=None,
                message=message,
                attribution=meta.attribution,
            ),
            members=[],
            errors=[],
        ),
        results=results,
    )


def configure_clone(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("url", help="Git URL of the workspace root repository")
    parser.add_argument("directory", nargs="?", help="Target directory for the cloned workspace")


async def handle_clone(context: CommandContext) -> Any:
    if context.meta.get("dry_run"):
        raise CliUsageError("--dry-run is not supported for clone")
    target = context.args.directory or _repo_name_from_url(context.args.url)
    if context.args.json:
        return await context.client.clone_workspace(context.args.url, target, **context.meta)

    operation_id = None
    async for event in context.client.clone_workspace_stream(context.args.url, target, **context.meta):
        operation_id = event.operation_id
        _render_clone_event(event)
    if operation_id is None:
        raise GwzBridgeError("clone stream completed without an operation event")
    result = await context.client.operation_result(operation_id)
    return _clone_response_from_result(result)


def _repo_name_from_url(url: str) -> str:
    trimmed = url.rstrip("/\\")
    segment = next(
        (part for part in reversed(trimmed.replace("\\", "/").replace(":", "/").split("/")) if part),
        trimmed,
    )
    name = segment.removesuffix(".git")
    if not name:
        raise CliUsageError("source URL does not include a repository name")
    return name


def _render_clone_event(event: OperationEvent) -> None:
    if event.kind is EventKind.operation_started or event.kind is EventKind.operation_finished:
        return
    path = event.member_path or event.member_id or "workspace"
    if event.kind is EventKind.member_started:
        print(f"{path}: started", file=sys.stderr)
    elif event.kind is EventKind.member_finished:
        print(f"{path}: finished", file=sys.stderr)
    elif event.kind is EventKind.member_progress and event.progress is not None:
        progress = event.progress
        parts = [path, progress.phase.name.replace("_", "-")]
        if progress.total_objects:
            parts.append(f"{progress.received_objects or 0}/{progress.total_objects} objects")
        if progress.received_bytes:
            parts.append(f"{progress.received_bytes} bytes")
        print(": ".join((parts[0], " ".join(parts[1:]))), file=sys.stderr)


def _clone_response_from_result(result: OperationResult) -> CloneWorkspaceResponse:
    return CloneWorkspaceResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id=result.request_id,
                schema_version="gwz.protocol/v0",
                action=result.action,
                aggregate_status=result.aggregate_status,
                operation_id=result.operation_id,
                message=None,
                attribution=result.attribution,
            ),
            members=result.members,
            errors=result.errors,
        )
    )


def _forall_invocation(
    tokens: list[str],
    command_string: str | None = None,
) -> tuple[list[str], ExecMode, list[str]]:
    if command_string is not None:
        if "--" in tokens:
            raise CliUsageError("use either `-c <string>` or `-- <cmd>`, not both")
        return tokens, ExecMode.shell, [command_string]

    if "--" in tokens:
        index = tokens.index("--")
        command = tokens[index + 1 :]
        if command:
            return tokens[:index], ExecMode.argv, command
        raise CliUsageError("no command (use `-- <cmd>` or `-c <string>`)")

    if "-c" in tokens:
        index = tokens.index("-c")
        if index + 1 >= len(tokens):
            raise CliUsageError("-c requires a command string")
        if index + 2 < len(tokens):
            raise CliUsageError("-c accepts exactly one command string")
        return tokens[:index], ExecMode.shell, [tokens[index + 1]]
    raise CliUsageError("no command (use `-- <cmd>` or `-c <string>`)")


def _filter_members(members: list[MemberEntry], projects: list[str]) -> list[MemberEntry]:
    if not projects:
        return members
    selected: list[MemberEntry] = []
    for project in projects:
        member = next(
            (candidate for candidate in members if candidate.id == project or candidate.path == project),
            None,
        )
        if member is None:
            raise CliUsageError(f"unknown project '{project}' (not a member id or path)")
        if all(existing.id != member.id for existing in selected):
            selected.append(member)
    return selected


def _run_forall(
    *,
    members: list[MemberEntry],
    mode: ExecMode,
    command: list[str],
    continue_on_fail: bool,
    no_banner: bool,
    root: str,
) -> list[ExecResult]:
    results: list[ExecResult] = []
    for member in members:
        if not no_banner:
            print(f"=== {member.path} ===", file=sys.stderr)
        result = _run_one(member=member, mode=mode, command=command, root=root)
        results.append(result)
        if not continue_on_fail and not _result_ok(result):
            break
    return results


def _run_one(
    *,
    member: MemberEntry,
    mode: ExecMode,
    command: list[str],
    root: str,
) -> ExecResult:
    env = os.environ.copy()
    env.update(
        {
            "GWZ_MEMBER_ID": member.id,
            "GWZ_MEMBER_PATH": member.path,
            "GWZ_MEMBER_ABSPATH": member.abspath,
            "GWZ_ROOT": root,
            "GWZ_TARGET_KIND": "root" if getattr(member.target_kind, "name", None) == "root" else "member",
        }
    )
    cwd = member.abspath
    try:
        if mode is ExecMode.shell:
            completed = subprocess.run(
                command[0],
                cwd=cwd,
                env=env,
                shell=True,
                check=False,
            )
        else:
            argv = [part.replace("{@}", member.path) for part in command]
            if not argv:
                return _spawn_failure(member, "empty command")
            completed = subprocess.run(argv, cwd=cwd, env=env, check=False)
    except OSError as exc:
        return _spawn_failure(member, str(exc))

    return ExecResult(
        id=member.id,
        path=member.path,
        exit_code=completed.returncode if completed.returncode >= 0 else None,
        signal=-completed.returncode if completed.returncode < 0 else None,
        spawn_error=None,
    )


def _spawn_failure(member: MemberEntry, message: str) -> ExecResult:
    return ExecResult(
        id=member.id,
        path=member.path,
        exit_code=None,
        signal=None,
        spawn_error=message,
    )


def _result_ok(result: ExecResult) -> bool:
    return result.exit_code == 0 and result.signal is None and result.spawn_error is None


def _workspace_root(context: CommandContext) -> str:
    root = context.args.root or getattr(context.client, "root", None)
    return str(root) if root is not None else str(Path.cwd())
