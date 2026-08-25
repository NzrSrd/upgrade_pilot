"""Pass 1 of the two-pass analyzer: which classes derive from the dependency.

See `models_index.py`'s module docstring for why the index has to be built
over the whole candidate set at once (fixed point, not single pass).
"""

import ast
from pathlib import Path

import pytest

from tests.fixtures.repo_builder import build_sample_repo
from upgradepilot.services.analysis.candidates import ParsedModule, select_candidates
from upgradepilot.services.analysis.models_index import build_model_index
from upgradepilot.services.repo.workspace import Workspace


def _module(path: str, source: str) -> ParsedModule:
    return ParsedModule(
        file=path, dotted_module=path[:-3].replace("/", "."), source=source, tree=ast.parse(source)
    )


def test_a_class_deriving_from_the_dependency_is_indexed() -> None:
    index = build_model_index(
        (_module("m.py", "from pydantic import BaseModel\nclass C(BaseModel):\n    x: int\n"),),
        import_root="pydantic",
    )
    assert index.names() == frozenset({"C"})
    entry = index.classes[0]
    assert (entry.file, entry.name, entry.line, entry.base_symbol) == ("m.py", "C", 2, "BaseModel")


@pytest.mark.parametrize(
    ("source", "expected_base_symbol"),
    [
        ("import pydantic\nclass C(pydantic.BaseModel):\n    x: int\n", "pydantic.BaseModel"),
        ("import pydantic as pyd\nclass C(pyd.BaseModel):\n    x: int\n", "pyd.BaseModel"),
        ("from pydantic import BaseModel as B\nclass C(B):\n    x: int\n", "B"),
        (
            "from pydantic.generics import GenericModel\nclass C(GenericModel):\n    x: int\n",
            "GenericModel",
        ),
    ],
)
def test_every_way_of_naming_the_base_is_indexed(source: str, expected_base_symbol: str) -> None:
    """Asserts `base_symbol` per spelling, not just `names()`. Task 7 reports
    `base_symbol` verbatim as `UsageSite.symbol` for a `MODEL_DEFINITION`
    site, so an implementation that always records the literal string
    `"BaseModel"` regardless of what the source actually wrote would still
    pass a test that checked only `names()` -- it never inspects the field
    whose correctness this test exists to pin."""
    index = build_model_index((_module("m.py", source),), import_root="pydantic")
    assert index.names() == frozenset({"C"})
    assert index.classes[0].base_symbol == expected_base_symbol


def test_a_class_with_no_dependency_base_is_not_indexed() -> None:
    """`util.py`'s `Bag` in miniature. This is the whole reason the index
    exists: without it, `bag.dict()` and `invoice.dict()` are the same
    expression shape.

    `Box(dict)` is included alongside the bare `Bag` because `Bag` alone has
    no base at all, so a seed rule that dropped the `root_of(...) ==
    import_root` check entirely -- accepting ANY resolvable base -- would
    still pass this test: `class_def.bases` would be empty either way, and
    the check that broke would never even run. `dict` is a real, resolvable,
    non-dependency base, so it is what actually exercises that check."""
    index = build_model_index(
        (
            _module("m.py", "class Bag:\n    def dict(self): ...\n"),
            _module("n.py", "class Box(dict):\n    def dict(self): ...\n"),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset()


def test_a_class_defined_inside_a_function_is_not_indexed() -> None:
    """A class nested inside a function body is not reachable by any
    dotted `module.ClassName` path Task 7 could ever resolve a receiver to
    -- it lives only in the function's local scope at call time.

    Deliberately a class nested inside a FUNCTION, not inside another class:
    `ast.ClassDef.col_offset` is identical (4) for both shapes, so a wrong
    implementation that tried to filter on indentation, or one built by
    swapping in a flat `ast.walk` collector, could conceivably still reject
    a class-in-a-class by some other coincidence. Only the function case
    forces a real parent-stack check rather than an indentation or
    `ast.walk` proxy for one."""
    index = build_model_index(
        (
            _module(
                "m.py",
                "from pydantic import BaseModel\n"
                "def make():\n"
                "    class Inner(BaseModel):\n"
                "        x: int\n"
                "    return Inner\n",
            ),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset()


def test_a_subclass_of_an_indexed_model_is_itself_indexed() -> None:
    """Real projects define `class Base(BaseModel)` once and subclass it
    everywhere. Indexing only direct subclasses would grade every real model
    in such a project as low confidence."""
    index = build_model_index(
        (
            _module(
                "base.py",
                "from pydantic import BaseModel\nclass Base(BaseModel):\n    x: int\n",
            ),
            _module("app.py", "from base import Base\nclass Customer(Base):\n    y: int\n"),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset({"Base", "Customer"})
    customer = next(c for c in index.classes if c.name == "Customer")
    # The brief: for a transitively-indexed class, `base_symbol` is the
    # first-party base as written (`Base`), never the dependency's own base
    # name (`BaseModel`) -- Task 7 Step 4 relies on that distinction to
    # avoid over-reporting a transitive model as if it named the dependency
    # directly.
    assert customer.base_symbol == "Base"


def test_a_grandchild_of_an_indexed_model_is_indexed_across_multiple_passes() -> None:
    """`test_a_subclass_of_an_indexed_model_is_itself_indexed` above only
    needs ONE fixed-point pass beyond the seed pass -- `Base` is seeded
    directly, so `Customer(Base)` resolves in the very first fixed-point
    iteration regardless of module order. That means a broken
    implementation which runs the fixed-point body exactly once (no real
    loop) still passes that test undetected.

    A genuine three-level chain closes that gap, PROVIDED the modules are
    given in an order the single-pass body cannot resolve in one go: here,
    `Grandchild` is checked before `Child` has been added to the index
    within the same pass, so catching `Grandchild` requires the loop to run
    again after `Child` is added -- an actual fixed point, not one extra
    pass."""
    index = build_model_index(
        (
            _module(
                "grandchild.py",
                "from child import Child\nclass Grandchild(Child):\n    pass\n",
            ),
            _module("child.py", "from base import Base\nclass Child(Base):\n    pass\n"),
            _module(
                "base.py",
                "from pydantic import BaseModel\nclass Base(BaseModel):\n    x: int\n",
            ),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset({"Base", "Child", "Grandchild"})


def test_transitive_indexing_terminates_on_a_cycle() -> None:
    """`class A(B)` in one file and `class B(A)` in another is not valid
    Python at runtime, but it is perfectly parseable, and a user's repository
    is untrusted input. A fixed-point loop with no visited set hangs the
    analysis here, and a hang is not an error the run can report."""
    index = build_model_index(
        (
            _module("a.py", "from b import B\nclass A(B):\n    pass\n"),
            _module("b.py", "from a import A\nclass B(A):\n    pass\n"),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset()


def test_transitive_indexing_reaches_a_model_through_a_cycle() -> None:
    """Defence in depth, not a discriminating test: the reviewer confirmed
    termination is structural (the fixed-point loop is monotonic over a
    `visited` set bounded by a finite class count, so nothing about a cycle
    specifically threatens it, with or without a seeded model reachable from
    it). Included anyway to exercise a cycle that IS reachable from a seeded
    model, with multiple inheritance in the mix, since that is closer to
    the shape a real, larger repository could produce than the two cycle
    fixtures above."""
    index = build_model_index(
        (
            _module("a.py", "from pydantic import BaseModel\nclass A(BaseModel):\n    pass\n"),
            _module("b.py", "from a import A\nfrom c import C\nclass B(A, C):\n    pass\n"),
            _module("c.py", "from b import B\nclass C(B):\n    pass\n"),
        ),
        import_root="pydantic",
    )
    assert index.names() == frozenset({"A", "B", "C"})


def test_the_sample_repo_indexes_exactly_its_two_models(tmp_path: Path) -> None:
    workspace = Workspace(build_sample_repo(tmp_path))
    scan = select_candidates(workspace, import_root="pydantic")
    index = build_model_index(scan.modules, import_root="pydantic")
    assert index.names() == frozenset({"Customer", "Invoice"})


def test_is_model_class_resolves_a_first_party_import() -> None:
    index = build_model_index(
        (
            _module(
                "app/models.py",
                "from pydantic import BaseModel\nclass Customer(BaseModel):\n    x: int\n",
            ),
        ),
        import_root="pydantic",
    )
    assert index.is_model_class("app.models.Customer") is True
    assert index.is_model_class("app.models.Bag") is False


def test_is_model_class_distinguishes_same_bare_name_in_different_modules() -> None:
    """Two first-party classes can share a bare name across different
    modules -- one deriving from the dependency, one not. `is_model_class`
    must match the FULL dotted path, not just the trailing class name:
    collapsing to `dotted.rsplit(".", 1)[-1] in names()` would report
    `app.other.Customer` (no pydantic base at all) as a model too, purely
    because an unrelated `app.models.Customer` happens to share its bare
    name. Task 7 would then grade a method call on the unrelated class as a
    fabricated MEDIUM-confidence finding."""
    index = build_model_index(
        (
            _module(
                "app/models.py",
                "from pydantic import BaseModel\nclass Customer(BaseModel):\n    x: int\n",
            ),
            _module("app/other.py", "class Customer:\n    def dict(self): ...\n"),
        ),
        import_root="pydantic",
    )
    assert index.is_model_class("app.models.Customer") is True
    assert index.is_model_class("app.other.Customer") is False


def test_classes_are_sorted_by_file_then_line() -> None:
    """`ModelIndex.classes` must be order-stable regardless of the order
    modules are handed to `build_model_index`, so that the same repository
    produces the same report on every run. The modules below are passed in
    an order that does NOT match sorted `(file, line)` order -- `z.py`
    before `a.py`, deliberately -- so a `found.values()` iterated straight
    off a dict (module/insertion order) rather than sorted would put `Z`
    before `A`."""
    index = build_model_index(
        (
            _module("z.py", "from pydantic import BaseModel\nclass Z(BaseModel):\n    x: int\n"),
            _module("a.py", "from pydantic import BaseModel\nclass A(BaseModel):\n    x: int\n"),
        ),
        import_root="pydantic",
    )
    assert [(c.file, c.line) for c in index.classes] == [("a.py", 2), ("z.py", 2)]
