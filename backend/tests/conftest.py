import os
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests that make real network calls",
    )


POSTGRES_URL_ENV = "UP_TEST_POSTGRES_URL"
"""Where `postgres`-marked tests find a database to use.

An explicit variable rather than probing a default, so a skip is never
ambiguous: either it was set or it was not. Set it to a throwaway database --
these tests create and **drop** their own tables.

    UP_TEST_POSTGRES_URL=postgresql://localhost:5432/upgradepilot_test

CI supplies one from a service container, which is what keeps ADR-002 D6's
ownership guarantee covered by the real store rather than only by a fake. A
suite of fakes passing while the real path is broken is the reason rule 24
keeps a live test at all, and ownership is exactly the kind of guarantee that
would pass against an in-memory dict and fail against SQL.
"""

DEPLOYMENT_URL_ENV = "UP_CHECKPOINT_URL"
"""The *deployment's* database, which these tests must never be pointed at.

Read here only to refuse it. See `_the_same_database`.
"""


def _dsn_identity(dsn: str) -> tuple[str, str]:
    """The (host, database) a DSN names, lowercased, credentials discarded.

    Compared on identity rather than on the literal string because the same
    database has many spellings: a different role, a `?sslmode=` that is
    present or absent, a trailing slash. Two DSNs differing only in password
    are the same database and must compare equal, or this guard is trivially
    evaded by the exact kind of near-miss it exists to catch.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(dsn.strip())
    return ((parts.hostname or "").lower(), parts.path.lstrip("/").lower())


def _deployment_dsn() -> str | None:
    """`UP_CHECKPOINT_URL` as configured, from the environment or `.env`.

    Both sources, because the deployment DSN normally lives in `backend/.env`
    and never reaches `os.environ`, so an environment-only check would find
    nothing in the one arrangement this guard is for. Parsed by hand rather
    than by importing `Settings`, which validates and would raise here --
    turning a safety check into a collection error.
    """
    from_env = os.environ.get(DEPLOYMENT_URL_ENV, "").strip()
    if from_env:
        return from_env

    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        if line.startswith(f"{DEPLOYMENT_URL_ENV}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def _the_same_database(test_dsn: str, deployment_dsn: str | None) -> bool:
    """Whether the test database and the deployment database are one.

    **Why this exists.** `tests/api/test_run_ownership.py`'s cleanup fixture
    runs `DROP TABLE run_owners` after every test. Against the deployment's
    database that deletes the mapping from every `thread_id` to its owner --
    so ADR-002 D6's guarantee does not fail, it evaporates: every paused run
    becomes unclaimed, and `require_owner` then refuses its real owner. There
    is no error at the moment of loss and no way to reconstruct the table.

    Measured, not hypothesised: verifying the Neon fix meant pointing
    `UP_TEST_POSTGRES_URL` at the production DSN, and the run left checkpoint
    rows behind in it. Nothing warned. The residue was harmless and the next
    such run would not have been.
    """
    if not deployment_dsn:
        return False
    host, database = _dsn_identity(test_dsn)
    # A DSN with neither host nor database names nothing identifiable -- a
    # local socket connection, say -- and guessing "same" there would refuse
    # legitimate local runs.
    if not host and not database:
        return False
    return (host, database) == _dsn_identity(deployment_dsn)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Additive only: this hook adds skips and never removes them, so a marked
    # test cannot reach the network -- or a database -- without opting in. A
    # conftest.py in a subdirectory must not strip a marker or that guarantee
    # is gone.
    if not config.getoption("--live"):
        skip_live = pytest.mark.skip(reason="needs --live and a real LLM API key")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)

    test_dsn = os.environ.get(POSTGRES_URL_ENV, "").strip()
    if not test_dsn:
        skip_postgres = pytest.mark.skip(reason=f"needs {POSTGRES_URL_ENV} set to a database")
        for item in items:
            if "postgres" in item.keywords:
                item.add_marker(skip_postgres)
        return

    # Refused rather than skipped. A skip is how a destructive misconfiguration
    # goes unnoticed until someone wonders why the ownership tests stopped
    # running; this has to be loud, and it has to stop the run before any
    # fixture opens a connection.
    if _the_same_database(test_dsn, _deployment_dsn()):
        raise pytest.UsageError(
            f"{POSTGRES_URL_ENV} names the same database as {DEPLOYMENT_URL_ENV}. "
            "These tests DROP the run_owners table, which would delete the owner "
            "of every paused run in the deployment. Point it at a throwaway "
            "database -- a separate Neon branch, or local Postgres."
        )
