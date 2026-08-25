---
source_id: internal#upgrade-report-billing-service
title: "Upgrade report: what actually broke in the billing service"
source_type: upgrade_report
dependency: pydantic
from_version: "1.10"
to_version: "2.9"
to_version_major: 2
affected_symbols: []
severity: high
url_or_reference: internal/upgrade-report-billing-service.md
created_at: 2026-08-25
tags: [internal-guidance, worked-example, post-migration]
---

**Note on this document.** This is an authored worked example, written as
internal guidance for teams planning the same upgrade. It is not a report of a
production incident.

**Shape of the work.** Roughly 60 models across 14 modules. The codemod handled
about 90% of the edits. Test suite green after the codemod; three real defects
surfaced afterwards, and none of them were caught by that green suite.

**What broke, in the order it was found.**

1. **`Optional` fields that were never optional.** Eleven fields were annotated
   `Optional[str]` with no default and were required in practice — callers
   always supplied them. Under V2 they became genuinely required, which was
   correct and invisible: no test failed, because no test omitted them. The
   change surfaced against a partner integration that had been omitting one of
   them for months and relying on the implicit `None`.

2. **A custom `Money` type that stopped validating.** It declared
   `__get_validators__`, which V2 never calls. The class still existed, still
   annotated fields, and validated nothing — currency codes stopped being
   checked. The suite stayed green because it only ever passed valid money.
   Found by grepping for `__get_validators__`, not by testing.

3. **`field.required` read as truthy.** An internal form generator iterated
   `__fields__` and branched on `field.required`. After the rename to
   `model_fields`, `required` is the method `is_required()` — a bound method,
   which is truthy — so every field rendered as required.

**What we would do differently.** All three failures share one shape: the code
kept working and stopped being correct, and the test suite could not tell.
Before starting, list the places where V2 fails *silently* — the three above
are the whole list for most codebases — and grep for each one directly. That
search takes an afternoon and would have found all three before any of them
reached an integration.

**What went better than expected.** Every loud failure — `BaseSettings`
importing, `@root_validator` refusing its bare form — was fixed in minutes,
because the error message named the fix. The loud changes are not where the
cost of this migration lives.
