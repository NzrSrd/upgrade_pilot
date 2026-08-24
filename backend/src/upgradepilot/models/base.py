"""The base class every domain model in this package is built on.

The models in this package are the mechanism by which the product keeps its
central promise: that every claim traces to real evidence. An invariant that
only holds at construction time does not keep that promise, so the two
structural guarantees live here rather than being re-declared per model.

**1. Immutability.** `frozen=True` is set once, here, so a model added later
cannot forget it. It is only half the story: `frozen=True` blocks field
*assignment* but does nothing about mutation of a contained `list` (e.g.
`.clear()`), which would silently empty a "required" collection after
construction. That is why collection fields throughout this package are
declared as tuples. Both halves are needed; neither substitutes for the
other.

**2. `model_copy(update=...)` is re-validated.** Pydantic's `model_copy`
does not validate `update` — its own docstring says "the data is not
validated before creating the new model. You should trust this data." That
made every field constraint in this package unenforced after construction.
Reproduced against pydantic 2.13.4 on the stock implementation:

    rf.model_copy(update={"evidence": ()})    -> 0 evidence items, and it
                                                 serialised as evidence=()
    rf.model_copy(update={"evidence": []})    -> the container was a *list*
                                                 again, so .clear() worked
    rf.model_copy(update={"level": "catastrophic", "weight": 99.0})
                                              -> stored and serialised, with
                                                 only a UserWarning

The override below routes an `update` through `model_validate`, which is the
same full validation pass construction gets — one mechanism, not a
field-by-field patch, so a constraint added later is covered without being
remembered. CLAUDE.md rule 19 (the LLM never produces a file path, a line
number, or a risk factor level) depended on closing this: an LLM payload fed
to `model_copy` was the one route by which it could supply exactly those.

A base class is only unforgettable if forgetting it fails. It does:
`tests/unit/test_model_invariants.py` walks every module in this package,
finds every `pydantic.BaseModel` subclass defined in it, and asserts each one
derives from `HonestModel` and is frozen. A new model that subclasses
`BaseModel` directly turns that test red.

**Known and deliberate exception: `model_construct`.** It is pydantic's
documented "skip validation" escape hatch and is not overridden here.
`HonestModel.model_construct(...)` will still build an object with zero
evidence and an out-of-range weight. It is left open because it is honest
about itself — nobody reaches for a method named `model_construct` expecting
validation, whereas `model_copy` reads like a safe "same object, these
fields changed" helper and was silently not one. The guarantee stated above
is therefore: *validated construction and `model_copy` cannot produce a
model that violates its own constraints.* It is not "no code path can".
`test_model_construct_is_a_documented_bypass` pins that so the hole is
visible rather than silent.
"""

import copy
from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict


class HonestModel(BaseModel):
    """A frozen model whose constraints survive `model_copy(update=...)`."""

    model_config = ConfigDict(frozen=True)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Copy this model, re-validating `update` as construction would.

        Signature-compatible with `BaseModel.model_copy`; the difference is
        that an `update` which would violate a field constraint raises
        `ValidationError` instead of being stored.

        An `update` key that is not a field raises rather than being applied
        as a phantom attribute (pydantic's behaviour) or dropped. Both of
        those turn a typo — `{"levl": "high"}` — into a silent no-op, and a
        risk level that silently did not change is exactly the kind of lie
        this package exists to prevent. Computed fields are not updatable and
        land here too, which is correct: they are derived, so there is
        nothing to set.

        Note for a future model that declares a field alias: validation runs
        by alias, so an aliased field cannot be named in `update` unless the
        model also sets `populate_by_name=True`. That fails loudly with a
        `ValidationError` naming the field rather than silently skipping it.
        """
        if not update:
            # No update means nothing to validate, so defer to pydantic and
            # keep its exact copy semantics (including `deep`).
            return super().model_copy(deep=deep)

        unknown = sorted(set(update) - set(type(self).model_fields))
        if unknown:
            raise ValueError(
                f"{type(self).__name__}.model_copy() got update key(s) that are not "
                f"fields: {unknown}"
            )

        merged: dict[str, Any] = {name: getattr(self, name) for name in type(self).model_fields}
        merged.update(update)
        if deep:
            # Matches `deep=True` on the stock implementation: the new model
            # shares no nested object with this one. Done before validation
            # because pydantic does not revalidate model instances by
            # default, so it would pass them through by identity.
            merged = copy.deepcopy(merged)
        return type(self).model_validate(merged)
