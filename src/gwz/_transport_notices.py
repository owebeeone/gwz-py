"""Private presentation of transport setting diagnostics."""
from __future__ import annotations

import logging
import sys
import warnings
from contextlib import contextmanager
from collections.abc import Iterator


def native_notice(message: str) -> None:
    # One gwz package location lets the standard warning registry show each
    # source-specific text once, across call/submit and every Client.
    warnings.warn(message, UserWarning)


class _NoteHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        print(record.getMessage().replace("gwz: ", "gwz-py: note: ", 1), file=sys.stderr)


@contextmanager
def cli_notices() -> Iterator[None]:
    """Render Python warnings and gwz logging records for this CLI invocation."""
    logger = logging.getLogger("gwz")
    handlers, propagate, level = list(logger.handlers), logger.propagate, logger.level
    showwarning = warnings.showwarning

    def show(message, category, filename, lineno, file=None, line=None):
        if category is UserWarning and str(message).startswith("gwz: using libgit2's native transport"):
            print(str(message).replace("gwz: ", "gwz-py: note: ", 1), file=sys.stderr)
        else:
            showwarning(message, category, filename, lineno, file=file, line=line)

    logger.handlers = [_NoteHandler()]
    logger.propagate = False
    logger.setLevel(logging.WARNING)
    warnings.showwarning = show
    try:
        yield
    finally:
        logger.handlers = handlers
        logger.propagate = propagate
        logger.setLevel(level)
        warnings.showwarning = showwarning
