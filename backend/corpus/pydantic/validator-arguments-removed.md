---
source_id: pydantic-v2-migration#validator-arguments-removed
title: "The each_item, always and allow_reuse validator arguments are gone"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [each_item, always, allow_reuse, validate_default]
severity: medium
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [validators, breaking-removal]
---

Three arguments that V1 validators took do not exist on `@field_validator`.

`each_item=True` — apply the validator to each element of a collection rather
than to the collection — is replaced by annotating the item type:
`list[Annotated[int, AfterValidator(check)]]`.

`always=True` — run the validator even when the field was not supplied and took
its default — is replaced by the model-level
`model_config = ConfigDict(validate_default=True)`.

`allow_reuse=True` existed to silence a V1 warning about reusing a validator
name. V2 does not warn, so the argument has nothing left to do.

The V1 form:

```python
@validator("scores", each_item=True, always=True, allow_reuse=True)
def positive(cls, v):
    if v < 0:
        raise ValueError("must be positive")
    return v
```

The V2 form:

```python
from typing import Annotated
from pydantic import AfterValidator, BaseModel, ConfigDict


def _positive(v: int) -> int:
    if v < 0:
        raise ValueError("must be positive")
    return v


class Scoreboard(BaseModel):
    model_config = ConfigDict(validate_default=True)

    scores: list[Annotated[int, AfterValidator(_positive)]] = []
```

Migration note: `each_item` and `always` change *when and to what* a rule
applies, so dropping them without replacement leaves a validator that still
exists, still passes review, and no longer runs on the values it was written
for.
