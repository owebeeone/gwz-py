import json

import pytest

from gwz.cli import build_parser
from gwz.cli_render import render_response
from test_cli_merge import merge_response


def test_merge_help_exposes_lifecycle_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["merge", "--help"])
    help_text = capsys.readouterr().out
    assert "--continue" in help_text
    assert "--abort" in help_text
    assert "--status" in help_text
    assert "--preserve" in help_text
    assert "--gc" in help_text


def test_merge_rendering_reports_only_remaining_post_gc_stash_evidence() -> None:
    response = merge_response()
    response.preservation[0].backup_ref = None
    response.preservation[0].backup_commit = None

    human = render_response(response)
    assert "remaining preservation artifacts:" in human
    assert "backup ref:" not in human
    assert "stash: stash-parity-1 @ stashobj123" in human
    machine = json.loads(render_response(response, json_mode=True))["merge"]
    assert machine["preservation"][0]["backup_ref"] is None
    assert machine["preservation"][0]["backup_commit"] is None
