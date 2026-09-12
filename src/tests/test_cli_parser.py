from __future__ import annotations

import argparse

import pytest

from gwz import __version__
from gwz.cli import build_parser
from gwz.cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
    meta_kwargs,
    validate_args,
)
from gwz.protocol.generated import SyncBehavior, TransportOptions, UrlScheme

def test_ssh_identity_flags_preserve_remote_overrides_and_equals_in_paths() -> None:
    args = build_parser().parse_args(["--identity", "default=key", "push", "--remote-identity", "origin=work=key", "--remote-identity", "upstream=other-key"])
    validate_args(args)
    transport = meta_kwargs(args)["transport"]
    assert transport.default_identity == "default=key"
    assert [(entry.remote, entry.private_key_path) for entry in transport.remote_identities] == [("origin", "work=key"), ("upstream", "other-key")]
    with pytest.raises(SystemExit):
        build_parser().parse_args(["push", "--remote-identity", "missing-separator"])


@pytest.mark.parametrize(
    "argv,command",
    [
        (["status", "--combined"], "status"),
        (["status", "--porcelain"], "status"),
        (["ls", "--materialized-only"], "ls"),
        (["init", "https://example.invalid/repo.git"], "init"),
        (["materialize", "--snapshot", "snap_1"], "materialize"),
        (["merge", "feature/refactor", "--dry-run"], "merge"),
        (["add", "-A", "src/file.py"], "add"),
    ],
)
def test_current_commands_register_handlers(argv: list[str], command: str) -> None:
    args = build_parser().parse_args(argv)

    assert args.command == command
    assert callable(args.command_handler)


def test_global_options_build_client_meta_kwargs() -> None:
    args = build_parser().parse_args(
        [
            "--root",
            "/ws",
            "--all",
            "--target",
            "@root",
            "--member",
            "mem_app",
            "--no-target",
            "@default",
            "--no-member",
            "mem_docs",
            "--member-path",
            "repos/lib",
            "--no-member-path",
            "repos/old",
            "--dry-run",
            "--partial",
            "--force",
            "--sync",
            "reset",
            "--remote",
            "origin",
            "--jobs",
            "4",
            "--max-per-host",
            "2",
            "--progress-interval",
            "0",
            "--ssh-timeout",
            "0",
            "--json",
            "status",
            "--combined",
        ]
    )

    validate_args(args)

    assert args.root == "/ws"
    assert args.json is True
    assert args.ssh_timeout == 0
    assert meta_kwargs(args) == {
        "all_members": True,
        "targets": ["@root", "mem_app"],
        "exclude_targets": ["@default", "mem_docs", "repos/old"],
        "paths": ["repos/lib"],
        "dry_run": True,
        "partial": True,
        "destructive": True,
        "sync": SyncBehavior.reset,
        "remote": "origin",
        "concurrency": 4,
        "max_connections_per_host": 2,
        "progress_min_interval_ms": 0,
    }


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_version_flags_exit_success(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([flag])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"gwz-py {__version__}\n"


def test_jsonl_ssh_timeout_and_verbose_parse_as_globals() -> None:
    args = build_parser().parse_args(
        ["--jsonl", "status", "--ssh-timeout", "3", "--verbose"]
    )

    validate_args(args)
    assert args.jsonl is True
    assert args.json is False
    assert args.ssh_timeout == 3
    assert args.verbose is True
    assert meta_kwargs(args) == {}


def test_json_and_jsonl_are_mutually_exclusive() -> None:
    args = build_parser().parse_args(["--json", "--jsonl", "status"])

    with pytest.raises(CliUsageError, match="--json and --jsonl"):
        validate_args(args)


def test_all_accepts_specific_target_selection_and_exclusions() -> None:
    args = build_parser().parse_args(
        ["--all", "--member", "mem_app", "--no-target", "@root", "status"]
    )

    validate_args(args)
    assert meta_kwargs(args) == {
        "all_members": True,
        "targets": ["mem_app"],
        "exclude_targets": ["@root"],
    }


def test_global_options_are_accepted_after_subcommands() -> None:
    args = build_parser().parse_args(
        ["ls", "--all", "--target", "@root", "--no-target", "@default"]
    )

    validate_args(args)
    assert meta_kwargs(args) == {
        "all_members": True,
        "targets": ["@root"],
        "exclude_targets": ["@default"],
    }


def test_global_options_merge_before_and_after_subcommands() -> None:
    args = build_parser().parse_args(
        ["--target", "@root", "push", "--target", "mem_app", "--remote", "origin"]
    )

    validate_args(args)
    assert meta_kwargs(args) == {
        "targets": ["@root", "mem_app"],
        "remote": "origin",
    }


def test_global_options_are_accepted_after_nested_subcommands() -> None:
    args = build_parser().parse_args(["repo", "sync", "--target", "@root"])

    validate_args(args)
    assert meta_kwargs(args) == {"targets": ["@root"]}


def test_local_all_options_keep_command_specific_meaning() -> None:
    add_args = build_parser().parse_args(["add", "--target", "@root", "--all"])
    commit_args = build_parser().parse_args(
        ["commit", "-m", "message", "--target", "@root", "--all"]
    )

    assert add_args.all_members is False
    assert add_args.stage_all is True
    assert meta_kwargs(add_args) == {"targets": ["@root"]}
    assert commit_args.all_members is False
    assert commit_args.commit_all is True
    assert meta_kwargs(commit_args) == {"targets": ["@root"]}


def test_stage_is_not_a_public_top_level_command() -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert "stage" not in subparsers.choices
    with pytest.raises(SystemExit):
        parser.parse_args(["stage", "-A"])


def test_command_registry_allows_modules_to_attach_commands() -> None:
    async def handler(context: CommandContext) -> str:
        return str(context.args.flag)

    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--flag", action="store_true")

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    registry = CommandRegistry()
    registry.register("demo", help="Demo command", configure=configure, handler=handler)
    registry.attach_to(subparsers)

    args = parser.parse_args(["demo", "--flag"])

    assert args.command == "demo"
    assert args.flag is True
    assert args.command_handler is handler


def test_local_identity_configuration_parser() -> None:
    args = build_parser().parse_args(["auth", "identity", "origin", "--set", "key=one", "--member", "@root"])
    validate_args(args)
    assert args.key_path == "key=one"
    assert args.remote_name == "origin"
    assert meta_kwargs(args)["targets"] == ["@root"]
    with pytest.raises(SystemExit):
        build_parser().parse_args(["auth", "identity", "origin", "--set", "key", "--unset"])


@pytest.mark.parametrize(
    "argv",
    [
        ["clone", "--url-scheme", "ssh", "https://example.invalid/ws.git"],
        ["materialize", "--url-scheme", "ssh", "--lock"],
    ],
)
def test_url_scheme_flag_carries_the_scheme_on_clone_and_materialize(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GWZ_URL_SCHEME", raising=False)
    args = build_parser().parse_args(argv)

    validate_args(args)
    assert args.url_scheme == "ssh"
    assert meta_kwargs(args) == {
        "transport": TransportOptions(
            default_identity=None, remote_identities=[], url_scheme=UrlScheme.ssh
        )
    }


def test_url_scheme_rides_the_transport_options_an_identity_already_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GWZ_URL_SCHEME", raising=False)
    args = build_parser().parse_args(
        [
            "--identity",
            "key",
            "clone",
            "--remote-identity",
            "origin=other-key",
            "--url-scheme",
            "https",
            "https://example.invalid/ws.git",
        ]
    )

    validate_args(args)
    transport = meta_kwargs(args)["transport"]
    assert transport.default_identity == "key"
    assert [entry.remote for entry in transport.remote_identities] == ["origin"]
    assert transport.url_scheme is UrlScheme.https


def test_no_url_scheme_request_leaves_the_request_meta_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GWZ_URL_SCHEME", raising=False)
    clone = build_parser().parse_args(["clone", "https://example.invalid/ws.git"])
    identity = build_parser().parse_args(
        ["--identity", "key", "clone", "https://example.invalid/ws.git"]
    )

    assert clone.url_scheme is None
    assert meta_kwargs(clone) == {}
    assert meta_kwargs(identity)["transport"] == TransportOptions(
        default_identity="key", remote_identities=[], url_scheme=None
    )


def test_url_scheme_environment_fallback_is_trimmed_and_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GWZ_URL_SCHEME", "  HTTPS  ")
    args = build_parser().parse_args(["materialize", "--lock"])

    assert meta_kwargs(args)["transport"].url_scheme is UrlScheme.https

    monkeypatch.setenv("GWZ_URL_SCHEME", "   ")
    assert meta_kwargs(build_parser().parse_args(["materialize", "--lock"])) == {}


def test_url_scheme_flag_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GWZ_URL_SCHEME", "ssh")
    args = build_parser().parse_args(["materialize", "--lock", "--url-scheme", "manifest"])

    assert meta_kwargs(args)["transport"].url_scheme is UrlScheme.manifest


def test_url_scheme_environment_is_refused_before_any_workspace_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GWZ_URL_SCHEME", "auto")
    args = build_parser().parse_args(["clone", "https://example.invalid/ws.git"])

    with pytest.raises(CliUsageError) as caught:
        meta_kwargs(args)

    assert str(caught.value) == 'GWZ_URL_SCHEME must be manifest, ssh or https, not "auto"'
    # A command without the option never reads the variable, as in the Rust CLI.
    assert meta_kwargs(build_parser().parse_args(["status"])) == {}
