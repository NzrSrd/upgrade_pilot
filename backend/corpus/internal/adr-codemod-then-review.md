---
source_id: internal#adr-codemod-then-review
title: "ADR: run the codemod first, then review the changes a codemod cannot make"
source_type: adr
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: []
severity: medium
url_or_reference: internal/adr-codemod-then-review.md
created_at: 2026-08-25
tags: [internal-guidance, tooling, review]
---

**Status:** accepted.

**Context.** Most of a Pydantic v1 to v2 migration is mechanical renaming —
`.dict()` to `.model_dump()`, `class Config` to `model_config`, `@validator` to
`@field_validator`. Doing that by hand is slow and produces a diff nobody reads
carefully by the third file.

**Decision.** Run `bump-pydantic` (or an equivalent codemod) as the first
commit, with no manual edits mixed in. Then review, in separate commits, only
the changes the codemod provably cannot make.

**What the codemod cannot do**, and therefore what the review must cover:

1. **`Optional[X]` with no default.** The annotation is unchanged and its
   meaning is not, so the tool has no signal to act on. Every such field must
   be decided by a human: was it optional, or merely annotated that way?
2. **Custom types using `__get_validators__`.** There is no mechanical
   translation to `__get_pydantic_core_schema__`, and the old hook fails
   silently rather than raising.
3. **`BaseSettings`.** Requires adding the `pydantic-settings` dependency, not
   only rewriting an import.
4. **`unique_items`.** Removed with no replacement argument; the fix is a
   modelling decision.
5. **Validators that used `each_item` or `always`.** Dropping the argument
   leaves a validator that still exists and no longer runs on what it was
   written for.

**Consequences.** The first commit is enormous and boring, and can be reviewed
by reading the codemod's own documentation rather than the diff. The commits
after it are small and each carry a real decision. That split is the point:
the risk in this migration is concentrated in a handful of places, and mixing
them into a thousand-line rename hides them.
