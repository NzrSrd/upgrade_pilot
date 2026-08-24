"""Package-wide invariants of the domain models.

Per-model tests live next to their subject; these are the invariants that
must hold for *every* model in `upgradepilot.models`, including ones added
after this file was written.
"""

import importlib
import inspect
import pkgutil
import warnings
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import upgradepilot.models
from upgradepilot.models.base import HonestModel
from upgradepilot.models.enums import RiskCategory, RiskLevel, Severity, SourceType
from upgradepilot.models.evidence import DocEvidence, RepoEvidence, RiskFactor, SourceRef


def _walk_models(package_name: str) -> list[type[BaseModel]]:
    """Every pydantic model *defined* anywhere under `package_name`.

    Discovered by walking the package rather than listed by hand: a
    hand-written list is precisely the thing a new model can be forgotten
    from, which is the failure this guard exists to prevent.
    """
    package = importlib.import_module(package_name)
    found: dict[str, type[BaseModel]] = {}
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is HonestModel:
                continue  # the shared base, not a domain model
            if issubclass(obj, BaseModel) and obj.__module__ == info.name:
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return [found[key] for key in sorted(found)]


def _domain_models() -> list[type[BaseModel]]:
    """Every pydantic model *defined* in `upgradepilot.models`."""
    return _walk_models("upgradepilot.models")


def a_risk_factor() -> RiskFactor:
    return RiskFactor(
        id="rf-1",
        name="breaking_change_exposure",
        category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
        level=RiskLevel.HIGH,
        weight=0.4,
        detail="three high-confidence sites collide with documented changes",
        evidence=(RepoEvidence(file="src/models.py", line=12, snippet="@validator('email')"),),
    )


def test_the_walk_finds_the_models_it_is_meant_to_guard() -> None:
    """A discovery bug here would silently make every guard below vacuous."""
    names = {model.__name__ for model in _domain_models()}

    assert {"RiskFactor", "BreakingChange", "AppError", "RepoAnalysis", "DependencySpec"} <= names
    assert HonestModel not in _domain_models()  # the base itself, not a domain model
    assert len(names) >= 15


@pytest.mark.parametrize("model", _domain_models(), ids=lambda m: m.__name__)
def test_every_domain_model_is_built_on_the_honest_base(model: type[BaseModel]) -> None:
    """A model that subclasses BaseModel directly opts out of re-validated
    `model_copy` and of `frozen=True`. Forgetting the base must fail, or the
    central fix is one a future model can quietly skip."""
    assert issubclass(model, HonestModel), (
        f"{model.__module__}.{model.__name__} must subclass HonestModel, not BaseModel"
    )
    assert model.model_config.get("frozen") is True


def test_model_copy_rejects_an_empty_required_collection() -> None:
    """Vector 1: `update={"evidence": ()}` used to yield a RiskFactor with
    zero evidence that serialised cleanly -- CLAUDE.md rule 1's exact
    failure mode, reached after construction."""
    with pytest.raises(ValidationError) as excinfo:
        a_risk_factor().model_copy(update={"evidence": ()})

    errors = excinfo.value.errors()
    assert any(e["loc"] == ("evidence",) and e["type"] == "too_short" for e in errors)


def test_model_copy_rejects_an_empty_list_for_a_tuple_field() -> None:
    """Vector 2: `update={"evidence": []}` used to put a *list* back in a
    tuple field, restoring the `.clear()` hole the tuples exist to close."""
    with pytest.raises(ValidationError):
        a_risk_factor().model_copy(update={"evidence": []})


def test_model_copy_coerces_a_valid_list_back_to_a_tuple() -> None:
    """The other half of vector 2: a non-empty list is accepted, but it must
    come back out as a tuple, not stay a mutable list."""
    copied = a_risk_factor().model_copy(
        update={"evidence": [DocEvidence(source_id="s", chunk_id="c")]}
    )

    assert isinstance(copied.evidence, tuple)
    assert not hasattr(copied.evidence, "clear")


def test_model_copy_rejects_an_out_of_range_weight_and_an_unknown_level() -> None:
    """Vector 3: these were stored *and serialised*, with only a UserWarning.
    A risk factor level is the exact thing CLAUDE.md rule 19 says the LLM
    never produces, and this was the one route by which it could."""
    with pytest.raises(ValidationError) as excinfo:
        a_risk_factor().model_copy(update={"level": "catastrophic", "weight": 99.0})

    locations = {error["loc"] for error in excinfo.value.errors()}
    assert ("level",) in locations
    assert ("weight",) in locations


def test_model_copy_rejects_an_update_key_that_is_not_a_field() -> None:
    """Pydantic sets an unknown key as a phantom attribute; dropping it
    silently would be no better. A mistyped risk level that silently did not
    change is the lie this package exists to prevent."""
    with pytest.raises(ValueError, match="not fields"):
        a_risk_factor().model_copy(update={"levl": "low"})


def test_model_copy_still_works_for_a_valid_update() -> None:
    """The override must not turn `model_copy` into a no-op: the honest use
    of it has to keep working, or callers will reach for something worse."""
    factor = a_risk_factor()
    copied = factor.model_copy(update={"level": RiskLevel.LOW, "weight": 0.1})

    assert copied.level is RiskLevel.LOW
    assert copied.weight == pytest.approx(0.1)
    assert copied.evidence == factor.evidence
    assert factor.level is RiskLevel.HIGH  # the original is untouched


def test_model_copy_without_an_update_is_unchanged_pydantic_behaviour() -> None:
    factor = a_risk_factor()

    assert factor.model_copy() == factor
    assert factor.model_copy(deep=True) == factor
    assert factor.model_copy(deep=True).evidence[0] is not factor.evidence[0]


def test_model_copy_deep_with_an_update_shares_nothing_with_the_original() -> None:
    factor = a_risk_factor()
    copied = factor.model_copy(update={"weight": 0.2}, deep=True)

    assert copied.evidence[0] is not factor.evidence[0]
    assert copied.evidence[0] == factor.evidence[0]


def test_model_copy_re_validation_reaches_nested_string_constraints() -> None:
    """Not just the field being updated: the whole model is re-validated, so
    a nested citation of pure whitespace cannot be smuggled in either."""
    with pytest.raises(ValidationError):
        a_risk_factor().model_copy(update={"detail": "   "})

    with pytest.raises(ValidationError):
        SourceRef(
            source_id="s",
            title="t",
            source_type=SourceType.ADR,
            url_or_reference="ref",
            chunk_id="c",
            relevance=0.5,
        ).model_copy(update={"relevance": 1.4})


def test_model_construct_is_a_documented_bypass() -> None:
    """`model_construct` is NOT closed, on purpose -- see `models/base.py`.
    Pinned here so the hole is visible in the test suite rather than being a
    silent gap behind a docstring that reads like a total guarantee. If this
    test ever fails because the bypass was closed, that is an improvement:
    delete the test and update the docstring in `models/base.py`."""
    smuggled = RiskFactor.model_construct(
        id="rf-1",
        name="x",
        category=RiskCategory.BREAKING_CHANGE_EXPOSURE,
        level="catastrophic",  # type: ignore[arg-type]
        weight=99.0,
        detail="",
        evidence=(),
    )

    assert smuggled.evidence == ()
    assert smuggled.weight == 99.0
    # ...and `model_copy` on the result is still validated, so the bypass
    # does not propagate through the mechanism this file is about.
    with pytest.raises(ValidationError):
        smuggled.model_copy(update={"weight": 0.5})


def test_severity_and_risk_level_are_distinct_enums() -> None:
    """Guards the parametrised sweep above against a future refactor that
    collapses the enums and makes `Severity`-typed fields accept a level."""
    assert Severity.HIGH is not RiskLevel.HIGH


def test_every_source_file_compiles_without_a_syntax_warning() -> None:
    """No invalid escape sequence anywhere in the package.

    `config.py` shipped a non-raw docstring containing `\\Z`, which is an
    invalid escape sequence. It was silent in normal runs only because cached
    bytecode skips recompilation -- so nothing in the suite, the linter or the
    type checker saw it, and it surfaced only when a file was compiled fresh.
    CPython has announced this becomes a hard `SyntaxError`, which with a 3.14
    floor makes it a landmine rather than a nit.

    Compiles from source every time, with `SyntaxWarning` escalated, so the
    bytecode cache cannot hide a regression. Whole package rather than the one
    file that offended: the point is to stop the class coming back.
    """
    package_root = Path(upgradepilot.models.__file__).parent.parent
    sources = sorted(package_root.rglob("*.py"))
    assert len(sources) >= 15, f"only found {len(sources)} source files; the walk is wrong"

    offenders: list[str] = []
    for source in sources:
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            try:
                compile(source.read_text(), str(source), "exec")
            except (SyntaxWarning, SyntaxError) as exc:
                offenders.append(f"{source.relative_to(package_root)}: {exc}")

    assert offenders == [], "\n".join(offenders)


@pytest.mark.xfail(reason="no service models until Task 2", strict=True)
def test_every_service_model_is_an_honest_model() -> None:
    """This phase defines typed records inside `services/analysis/`
    (`Declaration`, `AliasMap`, `ModelIndex`, `ChurnIndex`, ...), which the
    walk above -- scoped to `upgradepilot.models` -- does not reach. Without
    this test they could be plain `BaseModel`s with none of the honesty
    invariants and nothing would notice.

    `xfail(strict=True)`: until Task 2 lands there are no models under
    `services` at all, so the non-vacuity guard below fails -- correctly.
    `strict=True` turns the marker itself into a failure the moment a service
    model exists, so Task 2 removing it cannot be forgotten.
    """
    found = _walk_models("upgradepilot.services")
    assert found, "the walk found no models under services -- it is not walking anything"
    for model in found:
        assert issubclass(model, HonestModel), (
            f"{model.__module__}.{model.__qualname__} is a BaseModel but not a "
            f"HonestModel: it is missing frozen=True and the re-validating model_copy"
        )
