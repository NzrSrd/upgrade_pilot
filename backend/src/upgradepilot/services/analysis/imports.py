"""Local name -> dotted origin, for one module.

Module scope only. A name imported inside a function body is recorded like
any other -- Python's own scoping would shadow it, and modelling that
correctly needs a scope tree this phase does not build. The consequence is
over-binding in a rare case, which can only produce a finding at the import's
own line, and that line is real.

`from __future__ import annotations` is recorded like any other from-import
(binding `annotations` -> `__future__.annotations`) rather than special-cased
away. This is harmless: `__future__` can never be a dependency root a caller
would query `has_star_import_from` or `root_of` for.
"""

from __future__ import annotations

import ast
from typing import Self

from pydantic import Field

from upgradepilot.models.base import HonestModel
from upgradepilot.models.evidence import NonBlankStr


class AliasEntry(HonestModel):
    local: NonBlankStr
    origin: str | None = None
    """Absolute dotted path the local name refers to, or None when it cannot
    be known statically (a relative import, or a star import's contents)."""
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    is_module: bool
    """Which STATEMENT FORM bound this name: True for `import x`, False for
    `from x import y`. Not "the bound name denotes a module" -- `from .
    import models` binds a module and records False, because the form is
    what is being recorded, not what the name turns out to be.

    Read by no production code today, and the earlier claim here that "Task
    7 needs the distinction" was false: `usage.py`'s `_receiver_is_model`
    deliberately does not consult it, because a module receiver is normally
    excluded by the SHAPE of its origin instead (see that docstring).
    Recorded like `is_relative` -- an honest note of what the parser saw --
    rather than deleted, for one specific reason: `_receiver_is_model` has a
    known residue where the shape argument does not hold (a package whose
    `__init__.py` defines a class named exactly like one of its submodules),
    and the fix for it needs precisely this field, reached through a new
    `AliasMap` accessor rather than through `origin_of`, which returns a
    string and loses which entry won. Deleting it now would be churn against
    a recorded follow-up.

    Do not consume it as a proxy for module-ness without first handling the
    relative-import case above."""
    is_relative: bool = False
    is_star: bool = False
    star_module: str | None = None
    """The dotted module named by a star import, e.g. `"pydantic"` for
    `from pydantic import *`. Populated only when `is_star` is True. Kept
    separate from `origin` (which stays None for a star entry, per the
    class-level contract) because a star import's bound names cannot be
    enumerated statically -- this field exists solely so
    `AliasMap.has_star_import_from` has something to compare against."""


class AliasMap(HonestModel):
    entries: tuple[AliasEntry, ...] = ()

    @classmethod
    def from_module(cls, tree: ast.Module) -> Self:
        """Walk `tree` with `ast.walk` -- not just `tree.body` -- so an
        import inside a `try:`/`except ImportError:` block (the common real
        case for optional or version-gated dependencies) is recorded too.

        One `AliasEntry` per bound name, per the rules below. Order in
        `entries` is walk order (breadth-first), NOT source order -- see
        `origin_of` for why that distinction matters.
        """
        entries: list[AliasEntry] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    entries.append(
                        AliasEntry(
                            local=local,
                            origin=alias.name,
                            line=node.lineno,
                            column=node.col_offset,
                            is_module=True,
                        )
                    )

            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    # `from . import x` / `from ..pkg import y`: resolving to
                    # an absolute dotted path needs to know where this file
                    # sits in the package tree, which this module deliberately
                    # does not take as an argument. Recorded, never guessed.
                    #
                    # This check runs BEFORE the star check below, so
                    # `from . import *` lands here too -- and it is checked
                    # for `alias.name == "*"` regardless, because a relative
                    # star import is BOTH things at once, not one or the
                    # other: it is relative (the origin cannot be resolved
                    # without knowing where this file sits in the package
                    # tree) AND it is a star import (the bound names cannot
                    # be enumerated without importing the target). Recording
                    # only `is_relative` would hide it from a confidence
                    # reducer that Task 9 keys on `is_star`; a caller must
                    # see both facts to see the full picture. `star_module`
                    # stays None even so -- resolving the relative import to
                    # populate it would fabricate a dotted path the source
                    # never states (CLAUDE.md rule 1), and `has_star_import_
                    # from` correctly keeps returning False for it: we
                    # genuinely do not know which module was starred.
                    for alias in node.names:
                        local = alias.asname or alias.name
                        entries.append(
                            AliasEntry(
                                local=local,
                                origin=None,
                                line=node.lineno,
                                column=node.col_offset,
                                is_module=False,
                                is_relative=True,
                                is_star=alias.name == "*",
                            )
                        )
                    continue

                module = node.module
                assert module is not None, (
                    "ast only omits `module` on a from-import when level > 0 "
                    "(`from . import x`), and that shape is handled above; "
                    "`from <module> import ...` at level 0 always has one."
                )

                for alias in node.names:
                    if alias.name == "*":
                        # Binds names this module cannot enumerate without
                        # importing the target, which a static analyzer must
                        # not do. Recorded so Task 9 can raise it as a
                        # confidence reducer; binds nothing.
                        entries.append(
                            AliasEntry(
                                local="*",
                                origin=None,
                                line=node.lineno,
                                column=node.col_offset,
                                is_module=False,
                                is_star=True,
                                star_module=module,
                            )
                        )
                        continue

                    local = alias.asname or alias.name
                    entries.append(
                        AliasEntry(
                            local=local,
                            origin=f"{module}.{alias.name}",
                            line=node.lineno,
                            column=node.col_offset,
                            is_module=False,
                        )
                    )

        return cls(entries=tuple(entries))

    def origin_of(self, local_name: str) -> str | None:
        """The dotted path `local_name` currently refers to, or None.

        Resolved by `max((line, column))` among entries bound to
        `local_name` with a non-None origin -- NOT by taking the last entry
        in `entries`. `entries` is in `ast.walk` order, which is
        breadth-first: a `try:`/`except ImportError:` fallback import (a
        child node) is walked AFTER a later sibling that sits at module top
        level, even though the fallback appears EARLIER in the source. For

            try:
                from pydantic.v1 import BaseModel   # line 2
            except ImportError:
                pass
            from pydantic import BaseModel          # line 5

        `ast.walk` yields the line-5 entry first and the line-2 entry
        second. "Last entry in `entries` wins" would therefore return the
        line-2 (`pydantic.v1`) origin, where Python itself binds the line-5
        (`pydantic`) one -- the wrong answer, in exactly the shape that is
        the common real case. Ranking by source position instead of walk
        position gives the textually-last binding regardless of how
        `ast.walk` ordered the nodes, matching Python's own last-wins
        module-level semantics. Do not "simplify" this back to
        `entries[-1]` -- see `test_shadowing_resolves_by_source_position_not_
        walk_order` in the test module, which is written specifically to
        fail against that simplification.

        Entries with `origin is None` (relative and star imports) are
        skipped so one of them never shadows a real binding with a None.
        """
        candidates = [e for e in self.entries if e.local == local_name and e.origin is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda e: (e.line, e.column)).origin

    def root_of(self, local_name: str) -> str | None:
        """The top-level package of `origin_of(local_name)`, or None."""
        origin = self.origin_of(local_name)
        if origin is None:
            return None
        return origin.split(".")[0]

    def has_star_import_from(self, root: str) -> bool:
        """Whether some `from <root>... import *` was recorded, comparing
        by root package so `from pydantic.v1 import *` still answers True
        for `root="pydantic"`."""
        return any(
            entry.is_star
            and entry.star_module is not None
            and entry.star_module.split(".")[0] == root
            for entry in self.entries
        )
