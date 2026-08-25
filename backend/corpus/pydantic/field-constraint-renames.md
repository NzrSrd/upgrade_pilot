---
source_id: pydantic-v2-migration#field-constraint-renames
title: "min_items and max_items are renamed; unique_items is removed"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [min_items, max_items, unique_items, Field]
severity: medium
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [fields, constraints]
---

Collection constraints on `Field` were unified with the string constraints.
`min_items` becomes `min_length` and `max_items` becomes `max_length`.
`unique_items` was removed outright and has no replacement argument.

The removal is the one that needs a decision rather than an edit. V2's
recommendation is to model uniqueness in the type — use `set[str]` where the
semantics really are a set — and to write an explicit validator where order
matters and duplicates do not.

The V1 form:

```python
from pydantic import BaseModel, Field


class Basket(BaseModel):
    tags: list[str] = Field(min_items=1, max_items=10, unique_items=True)
```

The V2 form:

```python
from pydantic import BaseModel, Field


class Basket(BaseModel):
    tags: list[str] = Field(min_length=1, max_length=10)

    @field_validator("tags")
    @classmethod
    def tags_are_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("tags must be unique")
        return v
```

Migration note: swapping `list` for `set` changes the serialised output order
as well as its type, so it is a wire-format change and not only a validation
one.
