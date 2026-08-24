import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests that make real network calls",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    # Additive only: this hook adds skips and never removes them, so a
    # live-marked test cannot reach the network without --live. A conftest.py
    # in a subdirectory must not strip the marker or that guarantee is gone.
    skip = pytest.mark.skip(reason="needs --live and a real LLM API key")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
