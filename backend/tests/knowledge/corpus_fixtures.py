"""The shared sample corpus document.

Lives here rather than in a test module because more than one test file
builds documents from it, and importing a fixture out of a sibling test
module couples the two files' collection order to each other.
"""

FRONTMATTER = """\
---
source_id: pydantic-v2-migration#validator-renamed
title: "@validator replaced by @field_validator"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [validator, root_validator]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-24
tags: [validators, api-rename]
---

Pydantic v2 renames the validator decorators.

The v1 form is `@validator("field")`; the v2 form is `@field_validator("field")`.
"""


def document(**overrides: str) -> str:
    """The sample above with frontmatter lines replaced or removed.

    A value of `None` is spelled as the sentinel `"<<drop>>"` so the helper
    stays `str`-typed under strict mypy.
    """
    front, _, body = FRONTMATTER.partition("\n---\n\n")
    lines = front.splitlines()[1:]
    kept: list[str] = []
    for line in lines:
        key = line.split(":", 1)[0]
        if key in overrides:
            replacement = overrides[key]
            if replacement != "<<drop>>":
                kept.append(f"{key}: {replacement}")
        else:
            kept.append(line)
    for key, value in overrides.items():
        if value != "<<drop>>" and not any(line.startswith(f"{key}:") for line in kept):
            kept.append(f"{key}: {value}")
    return "---\n" + "\n".join(kept) + "\n---\n\n" + body
