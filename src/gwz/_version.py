"""Resolve package version from installed metadata or git tags."""

from __future__ import annotations

from pathlib import Path


def _resolve() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("gwz")
    except PackageNotFoundError:
        try:
            return version("gwz-py")
        except PackageNotFoundError:
            pass
    try:
        from setuptools_scm import get_version

        here = Path(__file__).resolve()
        return get_version(root=str(here.parents[2]), relative_to=str(here))
    except (ImportError, LookupError):
        return "0.0.0"


__version__ = _resolve()
