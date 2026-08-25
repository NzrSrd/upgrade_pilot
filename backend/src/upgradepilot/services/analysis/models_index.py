"""Pass 1: which classes in this repository derive from the dependency.

Task 7 grades a method call by what its receiver resolves to, and this is
what "resolves to a model" means. Built over the whole candidate set at once,
because a model defined in one file is subclassed in another.

Fixed-point, not single-pass: `class Base(BaseModel)` in one module and
`class Customer(Base)` in another is the ordinary shape of a real project,
and a single pass over an arbitrary file order finds one or the other
depending on which came first.
"""

from __future__ import annotations

import ast
from collections import Counter

from pydantic import Field

from upgradepilot.models.base import HonestModel
from upgradepilot.models.evidence import NonBlankStr, RepoRelativePath
from upgradepilot.services.analysis.candidates import ParsedModule
from upgradepilot.services.analysis.imports import AliasMap


class ModelClass(HonestModel):
    """One class, found by static analysis, that derives from the tracked
    dependency's base -- directly or transitively through a first-party
    ancestor."""

    file: RepoRelativePath
    dotted_module: NonBlankStr
    name: NonBlankStr
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    base_symbol: NonBlankStr
    """The base **as written** in the source (`BaseModel`, `pyd.BaseModel`,
    `Base`), not a resolved dotted path. Task 7 uses this verbatim as the
    `UsageSite.symbol` for a `MODEL_DEFINITION` site. For a transitively
    indexed class, this is the first-party base's name -- see Task 7 Step 4
    for why that, and not the dependency's own base name, is what gets
    reported as the symbol."""


class ModelIndex(HonestModel):
    """Every model class this repository defines, found across all
    candidate modules at once and sorted by `(file, line)` for a stable,
    reproducible order."""

    classes: tuple[ModelClass, ...] = ()

    def names(self) -> frozenset[str]:
        """Bare class names, e.g. `{"Customer", "Invoice"}`.

        Used by `expand_candidates` (Task 5), which byte-searches source for
        these names -- a byte search cannot use a dotted path."""
        return frozenset(entry.name for entry in self.classes)

    def is_model_class(self, dotted: str) -> bool:
        """Whether `dotted` (e.g. `app.models.Customer`) names an indexed
        class.

        Used by Task 7, which resolves a call's receiver to a dotted import
        path and asks whether that path names a model.

        The one lookup here that legitimately keys on `dotted_module` rather
        than `file`: `dotted` comes from an import statement, and an import
        names a module, never a file. `ParsedModule.dotted_module` is not
        injective (`src/app/models.py` and `app/models.py` both give
        `app.models`), so in a repository holding both, an import of
        `app.models.Invoice` cannot be attributed to one of them -- and True
        here means "some indexed class has that dotted path", which is the
        strongest true statement available. The cost is bounded to a
        CONFIDENCE grade: `_receiver_is_model` turns this into MEDIUM versus
        LOW and nothing else, so the file, line, column and symbol cited are
        unaffected either way. Do not add a `file` parameter here expecting
        it to sharpen the answer; the caller does not know which file the
        import resolved to either."""
        return any(f"{entry.dotted_module}.{entry.name}" == dotted for entry in self.classes)


class _TopLevelClassVisitor(ast.NodeVisitor):
    """Collects only classes defined at module top level.

    A class defined inside a function body is not importable by a dotted
    name, so it can never be a receiver's resolved type, and Task 7 has no
    use for it. `generic_visit` is deliberately never called on a
    `FunctionDef`/`AsyncFunctionDef`, which is what keeps a nested class out
    of the result without a separate depth check.
    """

    def __init__(self) -> None:
        self.classes: list[ast.ClassDef] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast.NodeVisitor API
        self.classes.append(node)
        # Do not descend into the class body: a class nested inside another
        # class is not reachable by a top-level dotted name either.

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        pass  # Skip: a class defined inside a function is not importable.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        pass  # Same reasoning as visit_FunctionDef.


def _leftmost_name(node: ast.expr) -> str | None:
    """For a base expression shaped as `ast.Name` or a dotted `ast.Attribute`
    chain (`pydantic.BaseModel`), return the leftmost name (`pydantic`).
    Any other shape (a call, a subscript such as `Generic[T]`) is not a base
    this pass can resolve statically, and returns None."""
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def _dotted_base_path(aliases: AliasMap, base: ast.expr) -> str | None:
    """Resolve a base expression to its full dotted path, e.g.
    `pyd.BaseModel` -> `pydantic.BaseModel`, or `Base` (bound by `from base
    import Base`) -> `base.Base`.

    Walks an `ast.Attribute` chain down to its leftmost `ast.Name`, resolves
    that name's full origin via the module's `AliasMap`, and reattaches any
    trailing attribute segments. Returns None for a shape this pass cannot
    resolve statically (a call, a subscript such as `Generic[T]`, or a name
    with no known origin)."""
    trailing: list[str] = []
    node = base
    while isinstance(node, ast.Attribute):
        trailing.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    origin = aliases.origin_of(node.id)
    if origin is None:
        return None
    trailing.reverse()
    return ".".join([origin, *trailing]) if trailing else origin


def colliding_dotted_modules(
    modules: tuple[ParsedModule, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every `dotted_module` shared by two or more `modules`, paired with
    the files that share it.

    Final fix round 2, item 3 -- the third variant of F2. `dotted_targets`
    below is keyed on the dotted name because an import names a module,
    never a file, and that key is genuinely ambiguous when two candidate
    modules answer to the same dotted name: a transitive base resolving to
    `app.models.Invoice` cannot say which of `src/app/models.py` and
    `app/models.py` it actually names. This function does not resolve
    that -- it only detects it, with a `Counter` over `dotted_module`, so
    `analyze_repository` can report the collision as a confidence reducer
    rather than silently attributing in favour of one. It changes nothing
    about `build_model_index`'s indexing logic below; whether to index
    both, index neither, or cap the confidence of an ambiguous attribution
    is a Phase 3 design decision (see `PLANNING.md`'s carry-in).

    Each entry is `(dotted_module, files)`, with `files` sorted; entries are
    sorted by `dotted_module`, so the result -- and the reducer text built
    from it -- is deterministic across runs.
    """
    counts = Counter(module.dotted_module for module in modules)
    colliding_names = sorted(dotted for dotted, count in counts.items() if count > 1)
    if not colliding_names:
        return ()
    by_dotted: dict[str, list[str]] = {name: [] for name in colliding_names}
    for module in modules:
        if module.dotted_module in by_dotted:
            by_dotted[module.dotted_module].append(module.file)
    return tuple((name, tuple(sorted(by_dotted[name]))) for name in colliding_names)


def build_model_index(modules: tuple[ParsedModule, ...], *, import_root: str) -> ModelIndex:
    """Find every class in `modules` that derives, directly or transitively,
    from `import_root`.

    Two stages:

    1. Seed -- a class is a model when some base resolves, via that module's
       `AliasMap`, to a dotted path whose root is `import_root`.
    2. Fixed point -- a class is a model when some base resolves to a
       `(dotted_module, name)` pair already in the index. Iterated with a
       `while True: ... if not added: break` loop, not recursion over bases:
       a user's repository is untrusted input, and `class A(B)` / `class
       B(A)` in two different files is perfectly parseable even though it is
       not valid Python at runtime. A recursive walk over bases would hang
       on that cycle; the fixed-point loop above terminates because the
       index only grows and the class count is finite.
    """
    # Per-module alias map and top-level ClassDefs, built once regardless of
    # how many fixed-point passes it takes to converge.
    per_module: list[tuple[ParsedModule, AliasMap, list[ast.ClassDef]]] = []
    for module in modules:
        aliases = AliasMap.from_module(module.tree)
        visitor = _TopLevelClassVisitor()
        visitor.visit(module.tree)
        per_module.append((module, aliases, visitor.classes))

    # found[(file, name)] -> (ParsedModule, ClassDef, base_symbol)
    #
    # Keyed on `file`, NOT on `dotted_module`: `_dotted_module` strips a
    # leading `src/`, so `src/app/models.py` and `app/models.py` BOTH map to
    # `app.models`. Keyed on the dotted name, whichever of two colliding
    # files the seed loop reached second was skipped as already-`visited`,
    # and a genuine model silently lost its HIGH-confidence
    # MODEL_DEFINITION site -- with no `skipped_files` entry and no reducer,
    # so the report claimed a completeness it did not have. `file` is the
    # repo-relative path and is unique by construction; `dotted_module` is
    # explicitly a best-effort guess (see `_dotted_module`'s docstring).
    found: dict[tuple[str, str], tuple[ParsedModule, ast.ClassDef, str]] = {}
    visited: set[tuple[str, str]] = set()

    # The `dotted_module.name` paths of everything indexed so far. Kept
    # separately from `visited` now that `visited` is file-keyed: this is
    # what a first-party base resolves against in the fixed point below, and
    # a base can only ever name a class by its dotted path, never by its
    # file. Two files sharing one `dotted_module` therefore contribute the
    # same entry here, which is the honest answer -- an import of
    # `app.models.Invoice` genuinely cannot be attributed to one of them.
    dotted_targets: set[str] = set()

    def _record(module: ParsedModule, class_def: ast.ClassDef, base: ast.expr) -> None:
        key = (module.file, class_def.name)
        found[key] = (module, class_def, ast.unparse(base))
        visited.add(key)
        dotted_targets.add(f"{module.dotted_module}.{class_def.name}")

    # Seed: direct dependency bases. A base's leftmost name resolved, via
    # this module's AliasMap, to a dotted path whose top-level package is
    # `import_root`.
    for module, aliases, class_defs in per_module:
        for class_def in class_defs:
            if (module.file, class_def.name) in visited:
                continue
            for base in class_def.bases:
                leftmost = _leftmost_name(base)
                if leftmost is not None and aliases.root_of(leftmost) == import_root:
                    _record(module, class_def, base)
                    break

    # Fixed point: transitive first-party bases. A base resolves, via this
    # module's AliasMap, to the exact dotted path (`dotted_module.name`) of a
    # class already in the index. Full dotted-path equality, not merely a
    # shared root package, so `class Customer(Base)` only indexes when its
    # `Base` resolves to the SAME already-indexed class, not to any
    # unrelated first-party symbol that happens to share a package root.
    while True:
        added = False
        for module, aliases, class_defs in per_module:
            for class_def in class_defs:
                if (module.file, class_def.name) in visited:
                    continue
                for base in class_def.bases:
                    resolved = _dotted_base_path(aliases, base)
                    if resolved is None:
                        continue
                    if resolved in dotted_targets:
                        _record(module, class_def, base)
                        added = True
                        break
        if not added:
            break

    classes = tuple(
        ModelClass(
            file=module.file,
            dotted_module=module.dotted_module,
            name=class_def.name,
            line=class_def.lineno,
            column=class_def.col_offset,
            base_symbol=base_symbol,
        )
        for module, class_def, base_symbol in found.values()
    )
    return ModelIndex(classes=tuple(sorted(classes, key=lambda c: (c.file, c.line))))
