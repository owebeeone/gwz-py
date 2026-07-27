from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_test_workflows_activate_a_real_virtualenv() -> None:
    for relative_path in (
        ".github/workflows/package-smoke.yml",
        ".github/workflows/publish.yml",
    ):
        workflow = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "Create Python virtual environment" in workflow
        assert 'root = pathlib.Path(".venv").resolve()' in workflow
        assert 'print(f"VIRTUAL_ENV={root}", file=env_file)' in workflow
        assert 'os.environ["GITHUB_PATH"]' in workflow
