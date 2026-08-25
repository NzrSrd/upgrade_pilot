---
source_id: pydantic-v2-migration#validator-renamed
title: "@validator is superseded by @field_validator"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [validator, field_validator]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [validators, api-rename]
---

`@validator` is replaced by `@field_validator`. The V1 spelling still imports
in V2 and still runs, but it is deprecated and emits a deprecation warning, so
a codebase that leaves it in place keeps working while accumulating warnings
rather than failing loudly.

The rename is the visible half of the change. The signature changed too: a V1
validator could take `values` to see the fields already validated, and the V2
replacement takes a `ValidationInfo` object instead, reaching the same data
through `info.data`.

The V1 form:

```python
from pydantic import BaseModel, validator


class User(BaseModel):
    name: str
    email: str

    @validator("email")
    def email_matches_name(cls, v, values):
        if "name" in values and values["name"] not in v:
            raise ValueError("email must contain the name")
        return v
```

The V2 form:

```python
from pydantic import BaseModel, ValidationInfo, field_validator


class User(BaseModel):
    name: str
    email: str

    @field_validator("email")
    @classmethod
    def email_matches_name(cls, v: str, info: ValidationInfo) -> str:
        if "name" in info.data and info.data["name"] not in v:
            raise ValueError("email must contain the name")
        return v
```

Migration note: the `@classmethod` decorator is now written explicitly, and it
must sit *below* `@field_validator`. Ordering them the other way silently
produces a validator that is never registered.
