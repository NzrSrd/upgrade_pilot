"""Pass 2: what a repository actually does with the dependency.

Pass 1 (`models_index.py`) finds which classes derive from it. This pass
walks every candidate module once more and, for each construct in spec
§7.1's grading table, emits a `UsageSite` graded by how sure the finding is.
Every site becomes a `RepoEvidence` citation in the final report, so a wrong
`line` is a wrong claim (CLAUDE.md rule 1) and a wrong `confidence` is a
wrong claim about how sure that claim is.

Structured as one `ast.NodeVisitor` with an explicit scope stack, never
`ast.walk`. `ast.walk` yields a nested `class Config` with no indication of
what it is nested inside -- and `NESTED_CONFIG` and `OPTIONAL_FIELD` are
both defined entirely by *being directly inside an indexed model*. The
stack holds one entry per enclosing class OR function scope
(`bool | None`: the model-membership flag for a class scope, `None` for a
function scope) precisely so that a `class Config:` nested inside a
*method* of a model is correctly not flagged -- if the stack only recorded
classes, `Config`'s nearest recorded ancestor would be the model two frames
up, and a method-nested `Config` (an unrelated class that happens to share
the pydantic v1 idiom's name) would wrongly read as HIGH confidence.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

from upgradepilot.models.enums import Confidence, UsageKind
from upgradepilot.models.repo import UsageSite
from upgradepilot.services.analysis.candidates import ParsedModule
from upgradepilot.services.analysis.imports import AliasMap
from upgradepilot.services.analysis.models_index import ModelClass, ModelIndex

TRACKED_METHODS = frozenset({"dict", "json", "parse_obj", "copy", "schema"})
"""Spec 7.1's medium/low method list. Generic names, all five of them --
which is the entire reason they are not graded high: `.dict()` alone tells
you nothing without knowing what it was called on."""

_OPTIONAL_SYMBOL = "Optional"
"""The corpus is keyed on this one symbol regardless of which of the three
equivalent spellings (`Optional[T]`, `Union[T, None]`, `T | None`) the
source actually uses -- see the parametrized spelling test."""


def _source_lines(source: str) -> list[str]:
    """`source` split the way CPython's tokenizer counts lines, so that
    `lines[node.lineno - 1]` is the line `ast` means.

    NOT `str.splitlines()`. That method breaks on eight characters the
    tokenizer does not treat as line breaks -- vertical tab `\\v`, form feed
    `\\f`, the three file/group/record separators `\\x1c\\x1d\\x1e`, NEL
    `\\x85`, and `U+2028`/`U+2029` (all eight verified against the pinned
    3.14.5 interpreter). Any one of them inside a string literal makes
    `splitlines()` produce one more entry than `ast` counted, and every
    citation after that point quotes the wrong line while still reporting the
    right number: a claim that looks precise and cannot be checked, which is
    the exact failure CLAUDE.md rule 1 exists to prevent.

    `\\r\\n` and a lone `\\r` ARE line breaks to the tokenizer (verified the
    same way), so they are normalised to `\\n` first rather than left for
    `split` to keep as trailing carriage returns inside a quoted snippet.

    Do not "simplify" this back to `splitlines()`, and do not use
    `splitlines()` anywhere else that an index is derived from an `ast` line
    number.
    """
    return source.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _iter_args(args: ast.arguments) -> Iterable[ast.arg]:
    """Every parameter that can carry an annotation, in a stable order.
    Order does not matter for correctness (each binds a distinct name into
    a dict), only completeness."""
    yield from args.posonlyargs
    yield from args.args
    if args.vararg is not None:
        yield args.vararg
    yield from args.kwonlyargs
    if args.kwarg is not None:
        yield args.kwarg


def _annotation_head_name(annotation: ast.expr) -> str | None:
    """The bare name at the head of a simple annotation: `Invoice` for
    `Invoice`, `Invoice` for `models.Invoice`. Deliberately narrow -- a
    subscripted or unioned annotation (`Optional[Invoice]`, `list[Invoice]`)
    returns None rather than guessing which contained name is the receiver's
    real type. `_receiver_is_model` is intentionally narrow for the same
    reason: every widening trades an honest LOW for a MEDIUM that might not
    be true."""
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Name):
        return annotation.id
    return None


def _decorator_symbol_and_root(decorator: ast.expr) -> tuple[str, str] | None:
    """For a decorator expression, the (reported symbol, name-to-resolve)
    pair, or None if its head is not a shape this pass can resolve.

    `@validator("id")` (a Call whose func is a bare Name) resolves to
    `("validator", "validator")` -- the name IS the symbol, and it is also
    what must resolve via the AliasMap to the dependency's root.

    `@dep.validator(...)` (a Call whose func is a dotted Attribute) resolves
    to `("validator", "dep")` -- the reported symbol is the dependency's own
    name (the attribute), never the local alias it was reached through; the
    alias is only used to find the root to resolve.
    """
    node = decorator
    while isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id, node.id
    if isinstance(node, ast.Attribute):
        base = node.value
        while isinstance(base, ast.Attribute):
            base = base.value
        if isinstance(base, ast.Name):
            return node.attr, base.id
    return None


def _contains_none_constant(node: ast.AST) -> bool:
    """Whether `node`'s subtree contains a literal `None` -- what
    distinguishes `Union[str, None]` and `str | None` (both True) from
    `Union[str, int]` (False). `Optional[str]` never reaches this check: it
    matches by NAME instead, because it contains no `Constant(None)` at all
    (verified when this plan was written)."""
    return any(isinstance(n, ast.Constant) and n.value is None for n in ast.walk(node))


class _UsageVisitor(ast.NodeVisitor):
    """One pass over one module's tree. `sites` accumulates in visitation
    order; `detect_usage` sorts by `(line, column)` before returning."""

    def __init__(
        self,
        *,
        module: ParsedModule,
        aliases: AliasMap,
        import_root: str,
        index: ModelIndex,
    ) -> None:
        self._module = module
        self._aliases = aliases
        self._import_root = import_root
        self._index = index
        self._lines = _source_lines(module.source)
        # Keyed by (name, line): the *line* disambiguates a nested class that
        # happens to share a name with a top-level indexed one from the
        # indexed class itself -- `ModelIndex` only ever indexes top-level
        # classes (see `models_index.py`'s `_TopLevelClassVisitor`), so a
        # nested class can never legitimately match this map.
        self._model_classes: dict[tuple[str, int], ModelClass] = {
            (entry.name, entry.line): entry
            for entry in index.classes
            if entry.dotted_module == module.dotted_module
        }
        # One entry per enclosing class-or-function scope. `True`/`False` for
        # a class scope (is it an indexed model); `None` for a function
        # scope. `NESTED_CONFIG` and `OPTIONAL_FIELD` both require the LAST
        # entry to be `True` -- immediately, not two levels up and not
        # through an intervening method.
        self._scope_stack: list[bool | None] = []
        # Local name -> annotation head name, for the CURRENT function scope
        # only. Saved and restored around each function (see `_visit_function`)
        # rather than cleared on exit: a single shared dict cleared at every
        # function exit would wipe an outer function's own parameter bindings
        # the moment an inner function (visited first, in source order, by a
        # depth-first NodeVisitor) returns.
        self._annotated_names: dict[str, str] = {}
        self.sites: list[UsageSite] = []
        self._emit_import_sites()

    def _emit(
        self, *, line: int, column: int, symbol: str, kind: UsageKind, confidence: Confidence
    ) -> None:
        self.sites.append(
            UsageSite(
                file=self._module.file,
                line=line,
                column=column,
                symbol=symbol,
                kind=kind,
                confidence=confidence,
                snippet=self._lines[line - 1],
            )
        )

    # -- imports ----------------------------------------------------------
    #
    # Not implemented as visit_Import/visit_ImportFrom overrides: the
    # per-name resolution (asname, dotted `import a.b.c`, a from-import's
    # bound name) is exactly what `AliasMap.from_module` already computed
    # correctly for Task 4, entry by entry. Recomputing it here would be a
    # second, divergence-prone copy of the same logic. `entries` already
    # excludes relative and star imports (their `origin` is None), so this
    # can filter on `origin` directly without re-deriving that exclusion.

    def _emit_import_sites(self) -> None:
        for entry in self._aliases.entries:
            if entry.origin is None:
                continue
            if entry.origin.split(".")[0] != self._import_root:
                continue
            self._emit(
                line=entry.line,
                column=entry.column,
                symbol=entry.local,
                kind=UsageKind.IMPORT,
                confidence=Confidence.LOW,
            )

    # -- classes: MODEL_DEFINITION, NESTED_CONFIG --------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast.NodeVisitor API
        model_class = self._model_classes.get((node.name, node.lineno))
        is_model = model_class is not None
        if model_class is not None:
            # `base_symbol` is the base AS WRITTEN (`models_index.py`): for a
            # class deriving directly from the dependency this IS the
            # dependency's own symbol (`BaseModel`); for a transitively
            # indexed class it is the first-party base's name instead. That
            # is deliberate -- a class whose own definition line names only a
            # first-party base does not itself contain the dependency's
            # symbol, and reporting one that is not on the line would be a
            # citation the reader cannot verify against the line it names.
            self._emit(
                line=node.lineno,
                column=node.col_offset,
                symbol=model_class.base_symbol,
                kind=UsageKind.MODEL_DEFINITION,
                confidence=Confidence.HIGH,
            )
        if node.name == "Config" and self._scope_stack and self._scope_stack[-1] is True:
            self._emit(
                line=node.lineno,
                column=node.col_offset,
                symbol="Config",
                kind=UsageKind.NESTED_CONFIG,
                confidence=Confidence.HIGH,
            )
        self._scope_stack.append(is_model)
        self.generic_visit(node)
        self._scope_stack.pop()

    # -- fields: OPTIONAL_FIELD --------------------------------------------

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if (
            self._scope_stack
            and self._scope_stack[-1] is True
            and node.value is None  # absent default; `= None` is Constant(None), not None
            and self._is_optional_annotation(node.annotation)
        ):
            self._emit(
                line=node.lineno,
                column=node.col_offset,
                symbol=_OPTIONAL_SYMBOL,
                kind=UsageKind.OPTIONAL_FIELD,
                confidence=Confidence.HIGH,
            )
        self.generic_visit(node)

    def _resolves_to(self, name: str, target: str) -> bool:
        """Whether `name` is (or resolves via the AliasMap to) `target`.

        Resolved through the AliasMap where possible (`from typing import
        Optional as Opt` still matches `Optional`), but the bare name is
        accepted even when it does not resolve at all: `typing` is not the
        tracked dependency, so there is no alias root to check it against,
        and a repository that shadows `Optional` with something unrelated is
        not a case worth adding complexity for.
        """
        origin = self._aliases.origin_of(name)
        if origin is not None:
            return origin.rsplit(".", 1)[-1] == target
        return name == target

    def _is_optional_annotation(self, annotation: ast.expr) -> bool:
        """True for `Optional[T]`, `Union[T, None]`, and `T | None`; false
        for anything else -- including `Union[T, U]` with no `None` member,
        a bare name, and a plain (non-optional) subscript such as
        `list[str]`, which is a Subscript that is optional in no sense."""
        if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
            head = annotation.value.id
            if self._resolves_to(head, "Optional"):
                return True
            if self._resolves_to(head, "Union"):
                return _contains_none_constant(annotation.slice)
            return False
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return _contains_none_constant(annotation)
        return False

    # -- functions: DECORATOR, and the annotated-name scope for calls ------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators are evaluated in the ENCLOSING scope, so they are
        # visited with the outer `_annotated_names` still in effect, before
        # the new scope below is pushed -- and visited exactly once, by this
        # explicit loop, rather than a second time through `generic_visit`
        # (which is why `decorator_list` is deliberately not one of the
        # fields walked at the bottom of this method).
        for decorator in node.decorator_list:
            resolved = _decorator_symbol_and_root(decorator)
            if resolved is None:
                continue
            symbol, root_name = resolved
            if self._aliases.root_of(root_name) != self._import_root:
                continue
            # A decorator expression's col_offset is the character AFTER the
            # `@` (verified when this plan was written); the citation must
            # point at the `@` itself. max(..., 0) is defensive only -- a
            # decorator always has an `@` before it.
            column = max(decorator.col_offset - 1, 0)
            self._emit(
                line=decorator.lineno,
                column=column,
                symbol=symbol,
                kind=UsageKind.DECORATOR,
                confidence=Confidence.HIGH,
            )

        outer_scope = self._annotated_names
        new_scope = dict(outer_scope)
        for arg in _iter_args(node.args):
            head = _annotation_head_name(arg.annotation) if arg.annotation is not None else None
            if head is not None:
                new_scope[arg.arg] = head
            else:
                # An unannotated parameter shadows any outer binding of the
                # same name -- inside this function, that name no longer
                # means what it meant one scope up.
                new_scope.pop(arg.arg, None)

        self._annotated_names = new_scope
        self._scope_stack.append(None)  # a function scope, never a model
        self.visit(node.args)
        for stmt in node.body:
            self.visit(stmt)
        if node.returns is not None:
            self.visit(node.returns)
        self._scope_stack.pop()
        self._annotated_names = outer_scope

    # -- calls: METHOD_CALL -------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr in TRACKED_METHODS:
            confidence = (
                Confidence.MEDIUM if self._receiver_is_model(node.func.value) else Confidence.LOW
            )
            # The citation must point at the method NAME -- `symbol` is
            # `dict`, not the receiver -- so both coordinates are derived
            # from the Attribute node's END position, not the Call node's
            # own start. `invoice.dict()`'s Call starts at column 0
            # (`invoice`); deriving from the Attribute's end instead lands on
            # the `d` of `dict`, matching what is actually reported.
            #
            # Both coordinates come from the SAME node deliberately: for a
            # multiline call --
            #     x = (invoice
            #          .dict())
            # -- `Call.lineno` is the line `invoice` starts on, while the
            # method name itself is one line further down. Pairing
            # `Call.lineno` with a column derived from the Attribute would
            # index into the wrong line's text entirely, and `snippet` must
            # equal `lines[line - 1]` (Task 10's own invariant).
            attr = node.func
            end_lineno = attr.end_lineno
            end_col_offset = attr.end_col_offset
            assert end_lineno is not None and end_col_offset is not None, (
                "ast.parse always sets end positions; only a hand-built AST "
                "node (never produced by this analyzer) would lack them"
            )
            self._emit(
                line=end_lineno,
                column=end_col_offset - len(attr.attr),
                symbol=attr.attr,
                kind=UsageKind.METHOD_CALL,
                confidence=confidence,
            )

    def _receiver_is_model(self, receiver: ast.expr) -> bool:
        """MEDIUM when the receiver is known to be a model, LOW otherwise.

        Deliberately narrow -- exactly two forms resolve, and nothing else:

          1. `Customer.parse_obj(...)` -- a Name bound by an import to a
             class in the ModelIndex.
          2. `invoice.dict()` -- a Name bound to a parameter whose annotation
             names a class in the ModelIndex.

        No dataflow, no return-type inference, no attribute chains. Every
        widening of this function trades a LOW that is honest for a MEDIUM
        that might not be, and MEDIUM is what the report presents as a
        likely break. The plan's Deviation 1 records the reasoning.

        A module receiver (`json.dict()`) is excluded by construction, not by
        a special case: `AliasMap.origin_of("json")` resolves to `"json"`
        for `import json`, and `"json"` never equals `f"{dotted_module}.
        {name}"` for any indexed class (that string always has an internal
        dot), so `is_model_class` returns False without needing to know
        `is_module` at all.
        """
        if not isinstance(receiver, ast.Name):
            return False
        origin = self._aliases.origin_of(receiver.id)
        if origin is not None and self._index.is_model_class(origin):
            return True
        annotated = self._annotated_names.get(receiver.id)
        return annotated is not None and annotated in self._index.names()


def detect_usage(
    module: ParsedModule, *, import_root: str, index: ModelIndex
) -> tuple[UsageSite, ...]:
    """Every usage site in `module`, graded per spec §7.1's table (as
    departed from by Deviation 1 -- see this module's and the brief's
    docstrings), sorted by `(line, column)`."""
    aliases = AliasMap.from_module(module.tree)
    visitor = _UsageVisitor(module=module, aliases=aliases, import_root=import_root, index=index)
    visitor.visit(module.tree)
    return tuple(sorted(visitor.sites, key=lambda site: (site.line, site.column)))
