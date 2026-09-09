import os

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
these tests create and truncate their own tables.

    UP_TEST_POSTGRES_URL=postgresql://localhost:5432/upgradepilot_test

CI supplies one from a service container, which is what keeps ADR-002 D6's
ownership guarantee covered by the real store rather than only by a fake. A
suite of fakes passing while the real path is broken is the reason rule 24
keeps a live test at all, and ownership is exactly the kind of guarantee that
would pass against an in-memory dict and fail against SQL.
"""


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

    if not os.environ.get(POSTGRES_URL_ENV, "").strip():
        skip_postgres = pytest.mark.skip(reason=f"needs {POSTGRES_URL_ENV} set to a database")
        for item in items:
            if "postgres" in item.keywords:
                item.add_marker(skip_postgres)
