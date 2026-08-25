"""Usage detection. Pure over a ParsedModule -- no workspace, no git.

Every row of the grading table (usage.py's module docstring, spec 7.1) has
a passing test and a negative counterpart here. Several tests exist
specifically to prove a *wrong* implementation would fail them -- their
docstrings say so; see `usage.py` for the corresponding "deliberately
narrow" reasoning each one pins down.
"""

import ast

import pytest

from upgradepilot.models.enums import Confidence, UsageKind
from upgradepilot.models.repo import UsageSite
from upgradepilot.services.analysis.candidates import ParsedModule
from upgradepilot.services.analysis.models_index import build_model_index
from upgradepilot.services.analysis.usage import TRACKED_METHODS, detect_usage

MODELS = """\
from typing import Optional

from pydantic import BaseModel, validator


class Customer(BaseModel):
    id: int
    nickname: Optional[str]
    note: Optional[str] = None

    class Config:
        orm_mode = True

    @validator("id")
    def check(cls, v):
        return v
"""


def _sites(source: str, *, extra: tuple[str, ...] = ()) -> tuple[UsageSite, ...]:
    module = ParsedModule(file="m.py", dotted_module="m", source=source, tree=ast.parse(source))
    others = tuple(
        ParsedModule(file=f"x{i}.py", dotted_module=f"x{i}", source=s, tree=ast.parse(s))
        for i, s in enumerate(extra)
    )
    index = build_model_index((module, *others), import_root="pydantic")
    return detect_usage(module, import_root="pydantic", index=index)


def _one(sites: tuple[UsageSite, ...], kind: UsageKind) -> UsageSite:
    matching = [s for s in sites if s.kind is kind]
    assert len(matching) == 1, f"expected exactly one {kind}, got {matching}"
    return matching[0]


def test_a_model_definition_is_high_confidence_at_the_class_line() -> None:
    site = _one(_sites(MODELS), UsageKind.MODEL_DEFINITION)
    assert (site.line, site.symbol, site.confidence) == (6, "BaseModel", Confidence.HIGH)
    assert MODELS.splitlines()[site.line - 1].startswith("class Customer(")


def test_a_nested_config_is_high_confidence_and_reports_the_Config_line() -> None:
    site = _one(_sites(MODELS), UsageKind.NESTED_CONFIG)
    assert (site.line, site.symbol, site.confidence) == (11, "Config", Confidence.HIGH)
    assert MODELS.splitlines()[site.line - 1].strip() == "class Config:"


def test_a_decorator_site_points_at_the_at_sign() -> None:
    """Verified when this plan was written: a decorator expression's
    `col_offset` is the character AFTER the `@` (5 for a decorator at indent
    4). The citation must point at the `@`, because that is where the reader's
    eye and their editor's column ruler both go.
    """
    site = _one(_sites(MODELS), UsageKind.DECORATOR)
    assert (site.line, site.symbol, site.confidence) == (14, "validator", Confidence.HIGH)
    line = MODELS.splitlines()[site.line - 1]
    assert line[site.column] == "@", f"column {site.column} is {line[site.column]!r}, not '@'"


def test_an_implicitly_optional_field_is_flagged_and_an_explicit_default_is_not() -> None:
    """The single most valuable finding this analyzer makes for the demo
    target: in v1 `nickname: Optional[str]` defaults to None, in v2 it is
    REQUIRED. `note: Optional[str] = None` is unaffected.

    Verified when this plan was written that these are distinguishable:
    absent default gives `AnnAssign.value is None`, explicit `= None` gives
    `Constant('None')`. A probe that calls `ast.unparse` on the value CANNOT
    tell them apart -- both render as the text `None`.

    NOTE on the line number: the brief's draft of this test asserted line 7.
    Verified against `ast.parse(MODELS)` directly (both by hand-counting
    `MODELS.splitlines()` and by printing each class-body statement's
    `lineno`): `id: int` is line 7 and `nickname: Optional[str]` is line 8.
    Asserting 7 here would require an implementation that reports the wrong
    line to pass -- exactly the CLAUDE.md rule 1 failure this whole task
    exists to prevent -- so this test uses the verified value, 8.
    """
    sites = _sites(MODELS)
    optional = [s for s in sites if s.kind is UsageKind.OPTIONAL_FIELD]
    assert [(s.line, s.symbol, s.confidence) for s in optional] == [
        (8, "Optional", Confidence.HIGH)
    ]
    assert 9 not in {s.line for s in optional}, "line 9 has an explicit default"


@pytest.mark.parametrize("annotation", ["Optional[str]", "Union[str, None]", "str | None"])
def test_every_spelling_of_implicitly_optional_reports_the_same_corpus_symbol(
    annotation: str,
) -> None:
    """The corpus is keyed on `Optional` regardless of spelling (see the
    plan's Global Constraints). Three symbols for one concept would need
    three corpus documents saying the same thing, and a `$contains` query for
    one would miss the other two."""
    source = (
        "from typing import Optional, Union\n"
        "from pydantic import BaseModel\n"
        "class C(BaseModel):\n"
        f"    x: {annotation}\n"
    )
    site = _one(_sites(source), UsageKind.OPTIONAL_FIELD)
    assert (site.symbol, site.line) == ("Optional", 4)


@pytest.mark.parametrize("annotation", ["Union[str, int]", "str", "list[str]"])
def test_every_negative_spelling_is_not_flagged_optional(annotation: str) -> None:
    """RULING 15's negatives. `Union[str, int]` is a Union with no `None`
    member -- the sloppiest wrong rule ("Subscript named Union is optional")
    would flag it. `str` is a bare Name -- the sloppiest wrong rule
    ("annotation contains no default, so it's optional") would flag it. And
    `list[str]` is the one that actually catches a careless implementation:
    it IS a Subscript, exactly the same node type as `Optional[str]`, but it
    is not optional in any sense -- a rule keyed on node type alone (ignoring
    the subscripted name) would wrongly flag it."""
    source = (
        "from typing import Optional, Union\n"
        "from pydantic import BaseModel\n"
        "class C(BaseModel):\n"
        f"    x: {annotation}\n"
    )
    assert [s for s in _sites(source) if s.kind is UsageKind.OPTIONAL_FIELD] == []


def test_an_optional_field_outside_a_model_is_not_flagged() -> None:
    """`Optional[str]` in an ordinary class or a function signature has
    nothing to do with the dependency. Flagging it manufactures findings
    proportional to how much typing the repository uses."""
    source = (
        "from typing import Optional\n"
        "class Plain:\n"
        "    x: Optional[str]\n"
        "def f(y: Optional[int]) -> None: ...\n"
    )
    assert [s for s in _sites(source) if s.kind is UsageKind.OPTIONAL_FIELD] == []


def test_a_class_Config_outside_a_model_is_not_flagged() -> None:
    source = "class Plain:\n    class Config:\n        pass\n"
    assert [s for s in _sites(source) if s.kind is UsageKind.NESTED_CONFIG] == []


def test_only_a_directly_nested_Config_counts() -> None:
    """A `class Config:` nested two levels deep, or inside a method, is not
    the pydantic v1 idiom -- it is an unrelated class that happens to share
    the name.

    RULING 33: both shapes are in this ONE fixture, not just the two-levels-
    deep case. The natural implementation -- a `NodeVisitor` keeping a stack
    of enclosing CLASSES only -- gets the two-levels-deep case right (the
    stack still records `C` as the nearest class two frames up... no, three:
    it records `Inner` as nearest, which is not a model, so that case alone
    cannot distinguish the bug) but gets the method-nested case WRONG: a
    class-only stack never pushes the method `m`, so `Config`'s nearest
    recorded ancestor is `C` itself, which IS a model, and a HIGH-confidence
    site is wrongly emitted. Only the method case exercises that bug.
    """
    source = (
        "from pydantic import BaseModel\n"
        "class C(BaseModel):\n"
        "    class Inner:\n"
        "        class Config:\n"
        "            pass\n"
        "    def m(self):\n"
        "        class Config:\n"
        "            pass\n"
    )
    assert [s for s in _sites(source) if s.kind is UsageKind.NESTED_CONFIG] == []


def test_a_nested_non_model_class_is_not_mistaken_for_a_same_named_indexed_one() -> None:
    """RULING 46. `usage.py`'s `_model_classes` is keyed by `(name, line)`,
    not `name` alone, exactly so this cannot happen: a module-level `class
    Customer(BaseModel)` and an unrelated nested `class Customer:` (inside a
    function, so it can never itself be indexed) share a NAME but not a
    LINE. Keying by name alone would let the nested class inherit the
    top-level one's `MODEL_DEFINITION` site -- a HIGH-confidence citation
    for `BaseModel` on a line (`    class Customer:`) that does not derive
    from it, or from anything -- a direct CLAUDE.md rule 1 violation at the
    HIGH tier."""
    source = (
        "from pydantic import BaseModel\n"
        "class Customer(BaseModel):\n"
        "    x: int\n"
        "def make():\n"
        "    class Customer:\n"
        "        pass\n"
        "    return Customer\n"
    )
    sites = _sites(source)
    definitions = [s for s in sites if s.kind is UsageKind.MODEL_DEFINITION]
    assert [s.line for s in definitions] == [2]


CONSUMER = """\
from app.models import Customer, Invoice


def serialize(invoice: Invoice) -> str:
    return invoice.dict()


def load(raw: dict) -> Customer:
    return Customer.parse_obj(raw)


def summarise(anything):
    return anything.dict()
"""

MODELS_MODULE = (
    "from pydantic import BaseModel\n"
    "class Customer(BaseModel):\n    x: int\n"
    "class Invoice(BaseModel):\n    y: int\n"
)


def _consumer_sites() -> tuple[UsageSite, ...]:
    consumer = ParsedModule(
        file="c.py", dotted_module="c", source=CONSUMER, tree=ast.parse(CONSUMER)
    )
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    index = build_model_index((consumer, models), import_root="pydantic")
    return detect_usage(consumer, import_root="pydantic", index=index)


def test_a_call_on_a_parameter_annotated_with_a_model_is_medium() -> None:
    site = next(s for s in _consumer_sites() if s.line == 5)
    assert (site.kind, site.symbol, site.confidence) == (
        UsageKind.METHOD_CALL,
        "dict",
        Confidence.MEDIUM,
    )


def test_a_call_on_an_imported_model_class_is_medium() -> None:
    site = next(s for s in _consumer_sites() if s.line == 9)
    assert (site.symbol, site.confidence) == ("parse_obj", Confidence.MEDIUM)


def test_a_call_on_an_unresolvable_receiver_is_low() -> None:
    """The tier that separates a real finding from a coincidence of naming.
    `anything` has no annotation, so nothing connects `.dict()` here to the
    dependency beyond the method's name -- and the method's name is `dict`."""
    site = next(s for s in _consumer_sites() if s.line == 13)
    assert (site.symbol, site.confidence) == ("dict", Confidence.LOW)


def test_the_two_tiers_are_actually_different_in_this_fixture() -> None:
    """Guards against a grader that returns one constant. Both tiers must be
    present in the same module, or neither assertion above is discriminating."""
    tiers = {s.confidence for s in _consumer_sites() if s.kind is UsageKind.METHOD_CALL}
    assert tiers == {Confidence.MEDIUM, Confidence.LOW}


def test_a_call_on_a_module_is_low_not_medium() -> None:
    """RULING 34 (renamed from `..._is_never_a_method_call_site`, whose name
    and docstring claimed no site is produced while its own assertion
    required exactly one at LOW). The grading table has exactly two
    METHOD_CALL rows -- resolves to an indexed model -> MEDIUM, does not
    resolve -> LOW -- and a module receiver does not resolve to a model:
    `json.dict()` still IS a method-call SHAPE (an Attribute call on a
    tracked name), it just does not reach MEDIUM. No third grading tier is
    introduced for "receiver is a module" -- it is simply one more case that
    fails to resolve, same as any other unannotated name."""
    source = "import json\nfrom app.models import Customer\nx = json.dict()\n"
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    index = build_model_index((module, models), import_root="pydantic")
    sites = detect_usage(module, import_root="pydantic", index=index)
    method_sites = [s for s in sites if s.kind is UsageKind.METHOD_CALL]
    assert [s.confidence for s in method_sites] == [Confidence.LOW]


def test_a_bare_function_call_named_dict_is_not_a_site() -> None:
    """`dict(items)` is the builtin. Only an attribute access can be a method
    call, and `util.py`'s `def dict(self)` is a definition, not a call."""
    source = "from app.models import Customer\nx = dict(a=1)\n"
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    index = build_model_index((module, models), import_root="pydantic")
    assert [
        s
        for s in detect_usage(module, import_root="pydantic", index=index)
        if s.kind is UsageKind.METHOD_CALL
    ] == []


def test_tracked_methods_is_exactly_the_spec_list() -> None:
    """Spec 7.1 names five: .dict(), .json(), .parse_obj(), .copy(),
    .schema(). Equality, not containment: an extra name manufactures findings
    with no corpus document behind them, and a missing one silently drops a
    whole class of finding."""
    assert frozenset({"dict", "json", "parse_obj", "copy", "schema"}) == TRACKED_METHODS


def test_annotated_name_survives_a_nested_function_scope() -> None:
    """RULING 35. `_annotated_names` must be saved and restored per function
    scope, not populated on entry and cleared on exit through one shared
    dict. A `NodeVisitor` walks in source order: `inner` is visited (and
    exited) before `outer`'s own `return` statement. With a single dict
    cleared on exit, `inner`'s exit wipes `invoice` out of the dict before
    `outer`'s `invoice.dict()` is ever reached, wrongly downgrading it to
    LOW. The CONSUMER fixture above has no nested function, so every test
    above this one passes even against that broken version -- only this
    fixture exercises it."""
    source = (
        "from app.models import Invoice\n"
        "def outer(invoice: Invoice):\n"
        "    def inner(x):\n"
        "        return x.dict()\n"
        "    return invoice.dict()\n"
    )
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    index = build_model_index((module, models), import_root="pydantic")
    sites = detect_usage(module, import_root="pydantic", index=index)
    by_line = {s.line: s.confidence for s in sites if s.kind is UsageKind.METHOD_CALL}
    assert by_line[4] == Confidence.LOW, "inner's `x` is unannotated"
    assert by_line[5] == Confidence.MEDIUM, "outer's `invoice` binding must survive inner's exit"


def test_an_inner_functions_own_unannotated_parameter_shadows_the_outer_binding() -> None:
    """RULING 49. Coverage for behaviour that already exists (the
    `new_scope.pop(arg.arg, None)` branch in `_visit_function`), not a
    defect: an inner function's own parameter, even unannotated, shadows an
    outer annotated name of the same name for the DURATION of that inner
    scope -- exactly as real Python scoping works. `inner`'s own `invoice`
    has no annotation, so `inner`'s `invoice.dict()` must be LOW even though
    `outer`'s `invoice` (the same NAME, a different binding) is annotated
    `Invoice`; `outer`'s own call, once `inner` has exited, must still be
    MEDIUM."""
    source = (
        "from app.models import Invoice\n"
        "def outer(invoice: Invoice):\n"
        "    def inner(invoice):\n"
        "        return invoice.dict()\n"
        "    return invoice.dict()\n"
    )
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    index = build_model_index((module, models), import_root="pydantic")
    sites = detect_usage(module, import_root="pydantic", index=index)
    by_line = {s.line: s.confidence for s in sites if s.kind is UsageKind.METHOD_CALL}
    assert by_line[4] == Confidence.LOW, "inner's own `invoice` param is unannotated"
    assert by_line[5] == Confidence.MEDIUM, "outer's `invoice` is unaffected once inner exits"


def test_a_call_through_an_optional_model_annotation_is_low_not_medium() -> None:
    """RULING 48. An accepted precision/recall boundary, pinned so a future
    widening is a reviewed choice rather than a silent regression --
    mirrors how `test_an_optional_field_outside_a_model_is_not_flagged` pins
    the OPTIONAL_FIELD boundary.

    `_annotation_head_name` deliberately does not look inside a subscripted
    annotation to find a contained model name: `Optional[Invoice]` could
    just as easily have been `Union[Invoice, str]` or `list[Invoice]`, and
    guessing which subscripted name is "the real type" is exactly the kind
    of widening that trades an honest LOW for a MEDIUM that might not hold
    (the plan's Deviation 1 reasoning). So `x: Optional[Invoice]` followed
    by `x.dict()` grades LOW, not MEDIUM -- not a bug, a documented limit.
    """
    source = (
        "from typing import Optional\n"
        "from app.models import Invoice\n"
        "def f(x: Optional[Invoice]):\n"
        "    return x.dict()\n"
    )
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    index = build_model_index((module, models), import_root="pydantic")
    site = next(
        s
        for s in detect_usage(module, import_root="pydantic", index=index)
        if s.kind is UsageKind.METHOD_CALL
    )
    assert site.confidence == Confidence.LOW


def test_a_method_call_site_points_at_the_method_name_not_the_receiver() -> None:
    """RULING 47. The reported `symbol` is `dict`, so the citation's column
    must land on the `d` of `dict` -- not on `invoice`, where the raw `Call`
    node's own `col_offset` starts. Same principle as the DECORATOR site's
    `col_offset - 1` adjustment earlier in this file: point at the token the
    symbol actually names, not wherever the enclosing expression begins."""
    source = "from app.models import Invoice\ndef f(invoice: Invoice):\n    return invoice.dict()\n"
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    index = build_model_index((module, models), import_root="pydantic")
    site = next(
        s
        for s in detect_usage(module, import_root="pydantic", index=index)
        if s.kind is UsageKind.METHOD_CALL
    )
    assert site.line == 3
    line = source.splitlines()[site.line - 1]
    assert line[site.column : site.column + len("dict")] == "dict"


def test_a_multiline_method_call_cites_the_line_the_method_name_is_actually_on() -> None:
    """RULING 47's multiline proof. `Call.lineno` is the line the RECEIVER
    starts on; the method name can be a line further down. Deriving `line`
    and `column` from different nodes (the Call for one, the Attribute for
    the other) would produce a citation whose column indexes into the WRONG
    line's text -- and `snippet == lines[line - 1]` is this task's own
    invariant (see `test_every_site_carries_the_source_line_it_cites`)."""
    source = (
        "from app.models import Invoice\n"
        "def f(invoice: Invoice):\n"
        "    x = (invoice\n"
        "         .dict())\n"
        "    return x\n"
    )
    models = ParsedModule(
        file="app/models.py",
        dotted_module="app.models",
        source=MODELS_MODULE,
        tree=ast.parse(MODELS_MODULE),
    )
    module = ParsedModule(file="c.py", dotted_module="c", source=source, tree=ast.parse(source))
    index = build_model_index((module, models), import_root="pydantic")
    site = next(
        s
        for s in detect_usage(module, import_root="pydantic", index=index)
        if s.kind is UsageKind.METHOD_CALL
    )
    assert site.line == 4, "the method name `.dict()` is on line 4, not line 3 where `invoice` is"
    line = source.splitlines()[site.line - 1]
    assert line[site.column : site.column + len("dict")] == "dict"
    assert site.snippet == line


def test_import_pydantic_and_from_pydantic_import_are_low_confidence_import_sites() -> None:
    """RULING 25. The IMPORT row of the grading table had no test in the
    brief -- `UsageKind.IMPORT` appeared only in the table, the reasoning
    paragraph, and the implementation instruction, never in an assertion. An
    implementation that emits no IMPORT site at all would pass every other
    specified test in this file."""
    source = "import pydantic\nfrom pydantic import BaseModel\n"
    module = ParsedModule(file="m.py", dotted_module="m", source=source, tree=ast.parse(source))
    index = build_model_index((module,), import_root="pydantic")
    sites = detect_usage(module, import_root="pydantic", index=index)
    imports = {(s.line, s.symbol, s.confidence) for s in sites if s.kind is UsageKind.IMPORT}
    assert imports == {
        (1, "pydantic", Confidence.LOW),
        (2, "BaseModel", Confidence.LOW),
    }


def test_an_import_of_an_unrelated_package_is_not_an_import_site() -> None:
    """Negative half of Ruling 25: `import json`'s root is not the tracked
    dependency, so it must not be reported at all -- an IMPORT site at any
    confidence would be a citation for a dependency the line does not
    mention."""
    source = "import json\n"
    module = ParsedModule(file="m.py", dotted_module="m", source=source, tree=ast.parse(source))
    index = build_model_index((module,), import_root="pydantic")
    sites = detect_usage(module, import_root="pydantic", index=index)
    assert [s for s in sites if s.kind is UsageKind.IMPORT] == []


def test_every_site_carries_the_source_line_it_cites() -> None:
    """`UsageSite.snippet` is quoted verbatim into the report. It must be the
    line `line` names, with its own indentation intact -- `snippet` is
    deliberately NOT NonBlankStr for exactly that reason (see models/repo.py).
    """
    source = MODELS
    for site in _sites(source):
        assert site.snippet == source.splitlines()[site.line - 1]


def test_every_site_points_inside_the_file_it_names() -> None:
    """A line number past the end of the file is a citation that cannot be
    resolved -- CLAUDE.md rule 1's failure with a plausible-looking number."""
    lines = MODELS.splitlines()
    for site in _sites(MODELS):
        assert 1 <= site.line <= len(lines)
        assert 0 <= site.column <= len(lines[site.line - 1])
        assert site.file == "m.py"


def test_sites_are_returned_in_source_order() -> None:
    sites = _sites(MODELS)
    assert [s.line for s in sites] == sorted(s.line for s in sites)


# -- F1: line indexing must agree with `ast`'s own line numbering ------------

_SPLITLINES_ONLY_BREAKS = ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]
"""The eight characters `str.splitlines` treats as line breaks and CPython's
tokenizer does not. Verified on the pinned 3.14.5 interpreter: each of these
inside a string literal parses fine, `ast` keeps counting the physical line it
sits on as ONE line, and `splitlines()` splits it into two -- so every index
after it is off by one."""


@pytest.mark.parametrize("separator", _SPLITLINES_ONLY_BREAKS)
def test_a_character_only_splitlines_calls_a_break_does_not_shift_the_snippet(
    separator: str,
) -> None:
    """CLAUDE.md rule 1. `line` comes from `ast`; `snippet` is read out of the
    source by that same number. If the two disagree about what a line is, the
    citation quotes a line the reader did not ask about while still naming the
    right number -- an unverifiable claim that looks precise.
    """
    source = (
        "from pydantic import BaseModel\n"
        f'X = "a{separator}b"\n'
        "\n"
        "\n"
        "class Invoice(BaseModel):\n"
        "    y: int\n"
    )
    site = _one(_sites(source), UsageKind.MODEL_DEFINITION)
    assert site.line == 5
    assert site.snippet == "class Invoice(BaseModel):"


def test_a_crlf_source_does_not_leave_a_carriage_return_in_the_snippet() -> None:
    """The other half of the fix: `split("\\n")` alone would keep the `\\r` of
    every CRLF line ending in the quoted snippet. `ast` counts `\\r\\n` and a
    lone `\\r` as line breaks (verified on 3.14.5), so both are normalised to
    `\\n` before splitting.
    """
    source = "from pydantic import BaseModel\r\n\r\nclass Invoice(BaseModel):\r\n    y: int\r\n"
    site = _one(_sites(source), UsageKind.MODEL_DEFINITION)
    assert site.line == 3
    assert site.snippet == "class Invoice(BaseModel):"


def test_a_lone_carriage_return_is_a_line_break_here_too() -> None:
    """A file whose only line endings are lone `\\r` (classic Mac). `ast`
    counts them as breaks; `splitlines()` happens to agree, so this one is
    NOT a de-sync -- it pins that the normalisation added for CRLF does not
    break the case that already worked."""
    source = "from pydantic import BaseModel\r\rclass Invoice(BaseModel):\r    y: int\r"
    site = _one(_sites(source), UsageKind.MODEL_DEFINITION)
    assert site.line == 3
    assert site.snippet == "class Invoice(BaseModel):"
