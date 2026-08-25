---
source_id: internal#adr-incremental-migration
title: "ADR: migrate to Pydantic v2 module by module, behind a boundary"
source_type: adr
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: []
severity: medium
url_or_reference: internal/adr-incremental-migration.md
created_at: 2026-08-25
tags: [internal-guidance, sequencing, incremental-migration]
---

**Status:** accepted. **Applies to:** any service upgrading Pydantic across the
1 to 2 major boundary.

**Context.** A Pydantic major upgrade touches every model in a codebase at
once, because the library sits at the edge of most request and persistence
paths. A single change that rewrites every model is large, hard to review, and
impossible to roll back partially — the property that makes it risky is not
the number of edits but that they must all land together.

**Decision.** Migrate module by module, using the `pydantic.v1` compatibility
namespace to hold the not-yet-migrated modules on the old behaviour, and choose
the boundary between the two worlds deliberately.

**The constraint that shapes the sequence.** V1 and V2 models do not
interoperate: a V2 model cannot take a V1 model as a field type. So the
boundary must fall where data crosses as plain dicts or primitives — a service
edge, a queue, a module with a narrow function-call interface — and never in
the middle of a model graph. Sequencing therefore follows the model dependency
graph from the leaves inward, not the file listing.

**Consequences.** The upgrade lands as several reviewable changes instead of
one unreviewable one, and each can ship independently. The cost is a period
during which two versions of the same library's semantics are live in one
process, which is genuinely confusing to read; that period should be bounded by
a date agreed up front rather than left open.

**When this does not apply.** A codebase with fewer than roughly twenty models,
or one with no custom types and no `__get_validators__` hooks, is usually
cheaper to migrate in a single change. The staging cost is real and is only
worth paying when the single change would be too large to review.
