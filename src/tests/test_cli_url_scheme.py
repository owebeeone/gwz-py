"""Rendering of a member's URL resolution, at parity with the Rust CLI's
`url_scheme_arg.rs` (its own tests live in gwz-cli/src/tests/g02)."""
from __future__ import annotations

import json

import pytest

from gwz.cli_render import render_response
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    MaterializeResponse,
    MemberResponse,
    MemberStatus,
    MemberUrlResolution,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
    TargetKind,
    TransportCredentialMethod,
    TransportObservation,
    TransportOperation,
    TransportSelectionSource,
    UrlScheme,
    UrlSchemeSource,
)

MANIFEST_URL = "git@github.com:o/r.git"
REWRITTEN_URL = "https://github.com/o/r.git"


def _resolution(
    scheme: UrlScheme, source: UrlSchemeSource, derived: bool
) -> MemberUrlResolution:
    return MemberUrlResolution(
        manifest_url=MANIFEST_URL,
        effective_url=REWRITTEN_URL if derived else MANIFEST_URL,
        scheme=scheme,
        source=source,
        derived=derived,
        host_known=True,
    )


def _member(url_resolution: MemberUrlResolution | None) -> MemberResponse:
    return MemberResponse(
        member_id="mem_app",
        member_path="repos/app",
        source_kind=SourceKind.git,
        status=MemberStatus.ok,
        error=None,
        planned=None,
        state=None,
        git_status=None,
        lock_match=None,
        target_kind=TargetKind.member,
        lock_difference_reasons=None,
        url_resolution=url_resolution,
    )


def _response(url_resolution: MemberUrlResolution | None) -> MaterializeResponse:
    return MaterializeResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_url_scheme",
                schema_version="gwz.protocol/v0",
                action=ActionKind.materialize,
                aggregate_status=AggregateStatus.ok,
                operation_id="op_url_scheme",
                message=None,
                attribution=None,
                transport=None,
            ),
            members=[_member(url_resolution)],
            errors=[],
        )
    )


def _member_json(url_resolution: MemberUrlResolution | None) -> dict[str, object]:
    document = json.loads(render_response(_response(url_resolution), json_mode=True))
    return document["response"]["members"][0]


def test_json_member_entries_carry_url_resolution() -> None:
    resolution = _resolution(UrlScheme.https, UrlSchemeSource.workspace, True)

    assert _member_json(resolution)["url_resolution"] == {
        "manifest_url": MANIFEST_URL,
        "effective_url": REWRITTEN_URL,
        "scheme": "https",
        "source": "workspace",
        "derived": True,
        "host_known": True,
    }
    # The wire spellings the Rust CLI emits, which cross-driver comparisons read.
    assert (
        '"url_resolution": {"derived": true, "effective_url": "https://github.com/o/r.git", '
        '"host_known": true, "manifest_url": "git@github.com:o/r.git", '
        '"scheme": "https", "source": "workspace"}'
    ) in render_response(_response(resolution), json_mode=True)


def test_json_member_entries_report_no_resolution_as_null() -> None:
    assert _member_json(None)["url_resolution"] is None


def test_human_output_credits_the_flag_for_a_requested_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GWZ_URL_SCHEME", raising=False)
    response = _response(_resolution(UrlScheme.https, UrlSchemeSource.request, True))

    rendered = render_response(response)

    assert rendered == "ok\nurl scheme: https (from --url-scheme)"
    assert " -> " not in rendered


def test_human_output_credits_the_variable_when_it_holds_the_applied_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GWZ_URL_SCHEME", "  HTTPS  ")
    response = _response(_resolution(UrlScheme.https, UrlSchemeSource.request, True))

    assert "url scheme: https (from GWZ_URL_SCHEME)" in render_response(response)

    monkeypatch.setenv("GWZ_URL_SCHEME", "ssh")
    assert "url scheme: https (from --url-scheme)" in render_response(response)


def test_human_output_names_the_workspace_record_and_omits_the_manifest_default() -> None:
    remembered = _response(_resolution(UrlScheme.ssh, UrlSchemeSource.workspace, False))
    assert "url scheme: ssh (from .gwz/url-scheme.yml)" in render_response(remembered)

    plain = _response(_resolution(UrlScheme.manifest, UrlSchemeSource.default, False))
    assert "url scheme:" not in render_response(plain)
    assert "url scheme:" not in render_response(_response(None))


def test_human_output_names_a_default_sourced_scheme_as_default() -> None:
    # Core reports `default` for a scheme nobody asked for; only a non-manifest
    # one is worth a line at all.
    response = _response(_resolution(UrlScheme.ssh, UrlSchemeSource.default, False))

    assert "url scheme: ssh (default)" in render_response(response)


def test_verbose_lists_only_the_members_whose_clone_url_was_rewritten() -> None:
    derived = _response(_resolution(UrlScheme.https, UrlSchemeSource.request, True))
    verbose = render_response(derived, show_transport=True)
    assert f"repos/app: {MANIFEST_URL} -> {REWRITTEN_URL}" in verbose

    unchanged = _response(_resolution(UrlScheme.ssh, UrlSchemeSource.workspace, False))
    assert " -> " not in render_response(unchanged, show_transport=True)
    assert " -> " not in render_response(derived)


def test_verbose_rewrites_precede_the_transport_diagnostics() -> None:
    response = _response(_resolution(UrlScheme.https, UrlSchemeSource.request, True))
    response.response.meta.transport = [
        TransportObservation(
            repository_path="repos/app",
            remote="origin",
            operation=TransportOperation.clone,
            credential_method=TransportCredentialMethod.agent,
            selection_source=TransportSelectionSource.ambient,
            credential_offered=True,
            authenticated=True,
            public_key_fingerprint=None,
        )
    ]

    lines = render_response(response, show_transport=True).splitlines()

    assert lines.index(f"repos/app: {MANIFEST_URL} -> {REWRITTEN_URL}") < next(
        index for index, line in enumerate(lines) if "credential=agent" in line
    )
