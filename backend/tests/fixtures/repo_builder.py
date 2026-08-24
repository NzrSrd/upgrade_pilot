"""Builds the sample repository into a temp directory with real git history.

The fixture tree is copied rather than used in place so tests never mutate
a checked-in directory, and so `broken.py.txt` can be renamed into a real
`.py` file that ruff and pytest in *this* repository never see.
"""

import shutil
import subprocess
from pathlib import Path

SAMPLE_REPO_DIR = Path(__file__).parent / "sample_repo"

# Documented expectations, asserted in test_fixture_repo.py and reused by
# Phase 2's analyzer tests.
EXPECTED_PYTHON_FILES = 6  # 4 in src/app (incl. __init__), broken.py, 1 test
EXPECTED_HIGH_CONFIDENCE_SYMBOLS = ("Config", "validator")
EXPECTED_MEDIUM_CONFIDENCE_SYMBOLS = ("copy", "dict", "parse_obj", "schema")
EXPECTED_UNPARSEABLE = "src/app/broken.py"
EXPECTED_DECLARED_SPECIFIER = ">=1.10,<2"
EXPECTED_PINNED_VERSION = "1.10.13"

# Mirrors the hardened invocation in `services/repo/clone.py`: system and
# global git config are pointed at /dev/null explicitly, rather than relying
# on an unset or redirected HOME to suppress them, so a developer's global
# `init.templateDir` or `core.hooksPath` can never reach a fixture build.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={**_GIT_ENV, "HOME": str(root)},
    )


def build_sample_repo(tmp_path: Path) -> Path:
    """Copy the fixture tree to `tmp_path` and give it two real commits.

    Two commits, not one, so churn signals are testable: the second touches
    only `models.py`, which is the file the analyzer should flag as both
    affected and recently changed.
    """
    root = tmp_path / "sample_repo"
    shutil.copytree(SAMPLE_REPO_DIR, root, ignore=shutil.ignore_patterns("__pycache__"))

    broken_source = root / "src" / "app" / "broken.py.txt"
    broken_source.rename(root / "src" / "app" / "broken.py")

    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial import")

    models = root / "src" / "app" / "models.py"
    models.write_text(models.read_text() + "\n\nMAX_INVOICES = 100\n")
    _git(root, "add", "src/app/models.py")
    _git(root, "commit", "-q", "-m", "add invoice cap")

    return root
