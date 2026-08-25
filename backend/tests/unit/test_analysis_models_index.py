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
    "source",
    [
        "import pydantic\nclass C(pydantic.BaseModel):\n    x: int\n",
        "import pydantic as pyd\nclass C(pyd.BaseModel):\n    x: int\n",
        "from pydantic import BaseModel as B\nclass C(B):\n    x: int\n",
        "from pydantic.generics import GenericModel\nclass C(GenericModel):\n    x: int\n",
    ],
)
def test_every_way_of_naming_the_base_is_indexed(source: str) -> None:
    index = build_model_index((_module("m.py", source),), import_root="pydantic")
    assert index.names() == frozenset({"C"})


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
