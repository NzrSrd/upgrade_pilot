"""`FinalReport`'s derived fields, and the fact that they reach a client.

A `@property` on a Pydantic model is invisible to `model_dump()`. That makes
"is it computed correctly" and "can the frontend see it" two different
questions, and only the second one catches the failure this file exists for:
a rule that is implemented, tested, and silently absent from every response
the API sends. So each test here asserts the value *and* asserts it survives
serialisation.
"""

from datetime import UTC, datetime

import pytest

from upgradepilot.models.enums import (
    DependencyRole,
    ManifestKind,
    VersionConfidence,
)
from upgradepilot.models.inputs import DependencySpec, LocalRepoRef, UserConstraints
from upgradepilot.models.plan import FinalReport
from upgradepilot.models.repo import (
    DetectedVersion,
    Manifest,
    RepoAnalysis,
    SymbolInventory,
)
from upgradepilot.models.usage import UsageSummary


def _analysis(detected: str | None) -> RepoAnalysis:
    return RepoAnalysis(
        commit_sha=None,
        detected_version=(
            None
            if detected is None
            else DetectedVersion(
                value=detected,
                specifier=f"=={detected}",
                source_manifest=Manifest(path="requirements.txt", kind=ManifestKind.REQUIREMENTS),
                confidence=VersionConfidence.EXACT,
                role=DependencyRole.DIRECT,
            )
        ),
        total_python_files=0,
        analyzed_files=0,
        symbol_inventory=SymbolInventory.from_sites([]),
    )


def _report(stated: str, detected: str | None, *, analysed: bool = True) -> FinalReport:
    return FinalReport(
        thread_id="t-1",
        repo_ref=LocalRepoRef(path="/tmp/repo"),
        dependency=DependencySpec(name="pydantic", current_version=stated, target_version="2.9.2"),
        constraints=UserConstraints(),
        commit_sha=None,
        completed_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        repo_analysis=_analysis(detected) if analysed else None,
        usage=UsageSummary.from_calls([]),
    )


def test_a_stated_version_the_manifest_contradicts_is_reported_as_a_pair() -> None:
    """The user said 1.9.0; the manifest pins 1.10.13. Neither wins silently
    (spec 7.1) -- the report shows both and lets the reader decide, because
    overriding in either direction would make every version-dependent claim
    downstream rest on a guess the reader never saw."""
    report = _report(stated="1.9.0", detected="1.10.13")

    assert report.version_discrepancy == ("1.9.0", "1.10.13")


def test_agreement_is_not_a_discrepancy() -> None:
    report = _report(stated="1.10.13", detected="1.10.13")

    assert report.version_discrepancy is None


def test_no_detected_version_is_not_a_discrepancy() -> None:
    """Nothing was found to disagree with. Reporting the stated version
    against nothing would read as "we checked and you are wrong"."""
    report = _report(stated="1.9.0", detected=None)

    assert report.version_discrepancy is None


def test_no_analysis_at_all_is_not_a_discrepancy() -> None:
    """A run that failed before `analyze_repo` finished has no manifest
    evidence, and a report is still assembled from what it has."""
    report = _report(stated="1.9.0", detected=None, analysed=False)

    assert report.version_discrepancy is None


@pytest.mark.parametrize(
    ("stated", "detected", "expected"),
    [
        ("1.9.0", "1.10.13", ["1.9.0", "1.10.13"]),
        ("1.10.13", "1.10.13", None),
    ],
)
def test_the_discrepancy_reaches_a_json_client(
    stated: str, detected: str, expected: list[str] | None
) -> None:
    """The point of the `@computed_field`.

    `RepoAnalysis.version_discrepancy` is a method taking the stated version,
    so it is not serialised and no client could ever read it -- the one place
    the product knows the manifest contradicts the user was invisible to the
    report meant to say so. This test fails if it goes back to being a bare
    property.
    """
    payload = _report(stated=stated, detected=detected).model_dump(mode="json")

    assert "version_discrepancy" in payload
    assert payload["version_discrepancy"] == expected


def test_the_report_delegates_rather_than_reimplementing_the_comparison() -> None:
    """One implementation of the rule, including its strip of raw caller
    input. A pasted "  1.9.0  " must be reported against its own trimmed
    value, not as a discrepancy with itself."""
    report = _report(stated="  1.9.0  ", detected="1.10.13")

    assert report.version_discrepancy == ("1.9.0", "1.10.13")
    assert _report(stated="  1.10.13\n", detected="1.10.13").version_discrepancy is None
