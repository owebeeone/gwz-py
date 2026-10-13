"""Transport setting diagnostics use distinct Python warning/logging paths."""
from __future__ import annotations

import logging
import warnings

import pytest

from gwz._transport_notices import cli_notices, native_notice


NATIVE = "gwz: using libgit2's native transport (from GWZ_TRANSPORT=native)"
IGNORED = "gwz: ignoring gwz.transport in fixture"


def test_native_notice_has_one_package_location_and_obeys_error_filters() -> None:
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("default")
        native_notice(NATIVE)
        native_notice(NATIVE)
        assert len(seen) == 1
        assert seen[0].category is UserWarning
    with warnings.catch_warnings():
        warnings.filterwarnings("error", module="gwz")
        with pytest.raises(UserWarning, match="native transport"):
            native_notice(NATIVE)


def test_cli_renders_warning_and_ignored_record_as_notes_and_restores_hooks(capsys) -> None:
    logger = logging.getLogger("gwz")
    before = (list(logger.handlers), logger.propagate, logger.level, warnings.showwarning)
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        with cli_notices():
            native_notice(NATIVE)
            logger.warning(IGNORED)
        assert (list(logger.handlers), logger.propagate, logger.level, warnings.showwarning) == before
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines() == [
        NATIVE.replace("gwz: ", "gwz-py: note: ", 1),
        IGNORED.replace("gwz: ", "gwz-py: note: ", 1),
    ]


def test_cli_prints_a_transport_note_record_as_a_note_and_leaves_its_detail_lines_at_debug(capsys) -> None:
    # The transport's notes are `gwz` records at WARNING under `gwz: ` (Code P3-9); a detail line, such as a
    # followed redirect, is a DEBUG record, which the CLI's WARNING level leaves out.
    logger = logging.getLogger("gwz")
    with cli_notices():
        logger.warning("gwz: github.com:443 asked gwz to wait 2 s; waiting")
        logger.debug("gwz: github.com:443 redirected to https://github.com/x; following it")
    output = capsys.readouterr()
    assert output.err.splitlines() == ["gwz-py: note: github.com:443 asked gwz to wait 2 s; waiting"]
