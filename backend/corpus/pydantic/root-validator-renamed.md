---
source_id: pydantic-v2-migration#root-validator-renamed
title: "@root_validator is superseded by @model_validator"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [root_validator, model_validator]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [validators, api-rename]
---

`@root_validator` — the whole-model validator — is replaced by
`@model_validator`, which takes an explicit `mode`. The V1 `pre=True` becomes
`mode="before"` and receives the raw input; the default `pre=False` becomes
`mode="after"` and receives the constructed model instance rather than a dict
of values.

That difference in what the function receives is the part that breaks silently
rather than loudly. A V1 `pre=False` root validator indexes `values["x"]`; the
V2 `mode="after"` equivalent is handed a model and must use `self.x`, so code
carried across unchanged raises `TypeError` at runtime rather than at import.

In V2 a bare `@root_validator` also refuses to be used without an explicit
argument: `@root_validator(skip_on_failure=True)` is required for the
after-style form.

The V1 form:

```python
from pydantic import BaseModel, root_validator


class Range(BaseModel):
    low: int
    high: int

    @root_validator
    def check_order(cls, values):
        if values["low"] > values["high"]:
            raise ValueError("low must not exceed high")
        return values
```

The V2 form:

```python
from pydantic import BaseModel, model_validator


class Range(BaseModel):
    low: int
    high: int

    @model_validator(mode="after")
    def check_order(self) -> "Range":
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        return self
```

Migration note: an `mode="after"` validator returns `self`, not a dict.
