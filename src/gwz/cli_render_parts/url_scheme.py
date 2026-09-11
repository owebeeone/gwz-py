"""Human rendering of a member's URL resolution, at parity with the Rust CLI."""

from __future__ import annotations

import os
from typing import Any

from ..cli_shared import URL_SCHEME_ENV, url_scheme_from_text
from ..protocol.generated import UrlScheme, UrlSchemeSource


def url_scheme_summary(members: Any) -> str | None:
    """One summary line when the operation applied a scheme other than `manifest`."""
    for member in members:
        resolution = getattr(member, "url_resolution", None)
        if resolution is not None and resolution.scheme is not UrlScheme.manifest:
            return f"url scheme: {resolution.scheme.name} ({_url_scheme_origin(resolution)})"
    return None


def url_resolution_human_lines(members: Any) -> list[str]:
    """One line per member whose clone URL was rewritten, for `--verbose`."""
    lines = []
    for member in members:
        resolution = getattr(member, "url_resolution", None)
        if resolution is not None and resolution.derived:
            lines.append(
                f"{member.member_path}: {resolution.manifest_url} -> {resolution.effective_url}"
            )
    return lines


def _url_scheme_origin(resolution: Any) -> str:
    """Where a request-sourced scheme came from, for the summary line. Core only
    knows "the request"; the CLI knows it read the flag or the variable. When the
    variable holds the applied scheme the variable is credited, which is also
    right when the flag repeated the same value."""
    if resolution.source is UrlSchemeSource.request:
        value = os.environ.get(URL_SCHEME_ENV)
        from_env = value is not None and url_scheme_from_text(value) is resolution.scheme
        return f"from {URL_SCHEME_ENV}" if from_env else "from --url-scheme"
    if resolution.source is UrlSchemeSource.workspace:
        return "from .gwz/url-scheme.yml"
    return "default"
