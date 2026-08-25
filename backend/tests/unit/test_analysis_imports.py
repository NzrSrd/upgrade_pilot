"""Alias resolution. Pure over an ast.Module -- no files, no workspace."""

import ast

import pytest

from upgradepilot.services.analysis.imports import AliasMap


def _map(source: str) -> AliasMap:
    return AliasMap.from_module(ast.parse(source))


@pytest.mark.parametrize(
    ("source", "local", "origin", "root"),
    [
        ("import pydantic", "pydantic", "pydantic", "pydantic"),
        ("import pydantic as pyd", "pyd", "pydantic", "pydantic"),
        ("import pydantic.dataclasses", "pydantic", "pydantic.dataclasses", "pydantic"),
        ("import pydantic.dataclasses as pd", "pd", "pydantic.dataclasses", "pydantic"),
        ("from pydantic import BaseModel", "BaseModel", "pydantic.BaseModel", "pydantic"),
        ("from pydantic import BaseModel as B", "B", "pydantic.BaseModel", "pydantic"),
        ("from pydantic.v1 import validator", "validator", "pydantic.v1.validator", "pydantic"),
        ("from app.models import Customer", "Customer", "app.models.Customer", "app"),
        ("import os.path", "os", "os.path", "os"),
    ],
)
def test_every_import_spelling_resolves(source: str, local: str, origin: str, root: str) -> None:
    aliases = _map(source)
    assert aliases.origin_of(local) == origin
    assert aliases.root_of(local) == root


def test_import_dotted_without_as_binds_only_the_top_package() -> None:
    """`import pydantic.dataclasses` binds the name `pydantic`, NOT
    `pydantic.dataclasses`. Getting this backwards means every
    `pydantic.dataclasses.dataclass(...)` reference fails to resolve, because
    the lookup is on the bound name and the bound name is the short one.
    """
    aliases = _map("import pydantic.dataclasses")
    assert aliases.origin_of("pydantic") == "pydantic.dataclasses"
    assert aliases.origin_of("pydantic.dataclasses") is None


def test_a_name_that_was_never_imported_resolves_to_None() -> None:
    aliases = _map("import pydantic")
    assert aliases.origin_of("BaseModel") is None
    assert aliases.root_of("BaseModel") is None


def test_relative_imports_are_recorded_with_their_level_and_never_guessed() -> None:
    """`from . import models` and `from ..pkg import thing` cannot be
    resolved to an absolute dotted path without knowing where the file sits
    in the package tree, and this module deliberately does not take that
    argument.

    They are recorded with `is_relative=True` and a None origin rather than
    being resolved wrongly. `root_of` returns None, so a relative import can
    never be mistaken for an import of the dependency -- which is the safe
    direction: it costs a missed finding, not a fabricated one.
    """
    aliases = _map("from . import models\nfrom ..pkg import thing")
    assert aliases.origin_of("models") is None
    assert aliases.root_of("models") is None
    assert [e.local for e in aliases.entries if e.is_relative] == ["models", "thing"]


def test_star_imports_are_recorded_but_bind_no_name() -> None:
    """`from pydantic import *` binds names this module cannot enumerate
    without importing pydantic, which a static analyzer must not do. It is
    recorded so Task 9 can raise it as a confidence reducer, and binds
    nothing -- again failing toward a missed finding rather than a wrong one.
    """
    aliases = _map("from pydantic import *")
    assert aliases.origin_of("*") is None
    assert aliases.has_star_import_from("pydantic") is True


def test_has_star_import_from_is_false_for_a_root_that_was_not_starred() -> None:
    """A hardcoded `return True` (ignoring `root` entirely) would pass every
    test above -- none of them asserts a negative. This is that negative
    case: the same `from pydantic import *` fixture must not answer True for
    an unrelated root.
    """
    aliases = _map("from pydantic import *")
    assert aliases.has_star_import_from("typing") is False


def test_has_star_import_from_matches_the_first_dotted_segment_only() -> None:
    """`pydantic_settings` is a distinct top-level package from `pydantic` --
    sharing a string prefix must not make `from pydantic_settings import *`
    register as a star import of `pydantic`. A substring or prefix match
    would false-positive here; only an exact first-segment match may pass.
    """
    aliases = _map("from pydantic_settings import *")
    assert aliases.has_star_import_from("pydantic") is False
    assert aliases.has_star_import_from("pydantic_settings") is True


def test_relative_star_import_is_recorded_as_both_relative_and_star() -> None:
    """`from . import *` is two true facts at once, not one or the other:
    it is relative (the origin cannot be resolved without knowing where this
    file sits in the package tree) AND it is a star import (the bound names
    cannot be enumerated without importing the target). A Task 9 confidence
    reducer keyed on `is_star` alone must still see this shape.

    `star_module` stays None -- resolving the relative import to populate it
    would fabricate a dotted path the source never states, and
    `has_star_import_from` correctly keeps answering False: which module was
    starred is genuinely unknown here.
    """
    aliases = _map("from . import *")
    entry = next(e for e in aliases.entries if e.local == "*")
    assert entry.is_relative is True
    assert entry.is_star is True
    assert entry.star_module is None
    assert aliases.has_star_import_from("pydantic") is False


def test_is_module_records_the_statement_form_not_whether_the_name_is_a_module() -> None:
    """`is_module` says which statement bound the name, NOT whether the name
    turns out to denote a module -- and the two genuinely differ: `from .
    import models` binds a module and records False.

    Its field docstring used to claim "Task 7 needs the distinction", which
    was false (nothing reads it), and the ledger's orphan audit recorded it
    as consumed by `usage.py`, which was also false. This test pins the
    meaning the corrected docstring states, so a future consumer that reads
    it as a module-ness flag is contradicted by a test rather than by prose:
    the third assertion below is the one that fails under that reading.
    """
    assert _map("import pydantic").entries[0].is_module is True
    assert _map("from pydantic import BaseModel").entries[0].is_module is False
    assert _map("from . import models").entries[0].is_module is False


def test_a_later_import_shadows_an_earlier_one() -> None:
    """Real files rebind names, usually in a `try/except ImportError` block.
    Python's own semantics are last-wins at module level; anything else
    reports a line the reader can check against and disagrees with."""
    aliases = _map("from pydantic import BaseModel\nfrom typing import BaseModel")
    assert aliases.origin_of("BaseModel") == "typing.BaseModel"


def test_shadowing_resolves_by_source_position_not_walk_order() -> None:
    """The shadowing test above uses two top-level sibling imports, where
    `ast.walk` order and source order happen to coincide -- it passes against
    both a correct implementation and one that (incorrectly) takes the last
    entry in `entries` walk order.

    This is the shape that tells them apart: a `try/except ImportError`
    fallback import is a CHILD node, so `ast.walk` (breadth-first) yields the
    later top-level `from pydantic import BaseModel` (line 5) BEFORE the
    earlier nested `from pydantic.v1 import BaseModel` (line 2). Python
    itself binds the line-5 import last. `origin_of` must agree with Python,
    not with walk order.
    """
    aliases = _map(
        "try:\n"
        "    from pydantic.v1 import BaseModel\n"
        "except ImportError:\n"
        "    pass\n"
        "from pydantic import BaseModel\n"
    )
    assert aliases.origin_of("BaseModel") == "pydantic.BaseModel"


def test_entries_carry_the_line_and_column_of_the_import() -> None:
    """These become UsageSite citations in Task 7, so they must point at the
    import statement itself."""
    aliases = _map("import json\nimport pydantic\n")
    entry = next(e for e in aliases.entries if e.local == "pydantic")
    assert (entry.line, entry.column) == (2, 0)


# -- I5: `ast.walk`, not `tree.body` ----------------------------------------


def test_an_import_reachable_only_inside_a_try_block_is_still_recorded() -> None:
    """`from_module` walks with `ast.walk` rather than iterating `tree.body`,
    and `imports.py` calls the version-gated `try:`/`except ImportError:`
    import "the common real case" -- `origin_of`'s whole argument rests on
    it. Replacing `ast.walk` with `tree.body` nevertheless left all 675 tests
    green, because the fixture that was supposed to guard it
    (`test_shadowing_resolves_by_source_position_not_walk_order`) has a
    top-level sibling import forcing the same answer.

    Here the try block holds the ONLY import in the module, so a body-only
    walk finds nothing at all and every downstream site in such a file
    disappears.
    """
    aliases = _map(
        "try:\n"
        "    from pydantic.v1 import BaseModel\n"
        "except ImportError:  # pragma: no cover\n"
        "    BaseModel = object\n"
    )
    assert aliases.origin_of("BaseModel") == "pydantic.v1.BaseModel"
    assert aliases.root_of("BaseModel") == "pydantic"
    assert [e.line for e in aliases.entries if e.origin is not None] == [2]


def test_a_star_import_nested_in_a_try_block_is_still_recorded() -> None:
    """The same gap for `has_star_import_from`, which feeds a confidence
    reducer: an optional-dependency star import is exactly the shape that
    hides usage, and a body-only walk would report False for it."""
    aliases = _map("try:\n    from pydantic import *\nexcept ImportError:\n    pass\n")
    assert aliases.has_star_import_from("pydantic") is True


# -- U8/X8/X2 family: a shared prefix is not a shared package ---------------


def test_a_package_whose_name_merely_starts_with_the_root_is_a_different_root() -> None:
    """`root_of` compares the first dotted SEGMENT for equality, so
    `pydantic_settings` correctly does not read as `pydantic`. Nothing bound
    that: a `startswith` mutation survived the whole suite. A refactor to
    prefix matching would make every `pydantic_settings` import a false
    finding against `pydantic` -- the same false-positive class
    `is_test_path`'s docstring warns about, and a CLAUDE.md rule 1 defect.
    """
    aliases = _map("import pydantic_settings\nfrom pydantic_settings import BaseSettings\n")
    assert aliases.root_of("pydantic_settings") == "pydantic_settings"
    assert aliases.root_of("BaseSettings") == "pydantic_settings"
    assert aliases.has_star_import_from("pydantic") is False


def test_a_star_import_from_a_prefix_named_package_is_not_the_dependency() -> None:
    """`has_star_import_from` compares by root segment for the same reason.
    `from pydantic_settings import *` must not raise the reducer that says
    usage of PYDANTIC may be under-reported."""
    aliases = _map("from pydantic_settings import *\n")
    assert aliases.has_star_import_from("pydantic_settings") is True
    assert aliases.has_star_import_from("pydantic") is False
