---
source_id: pydantic-v2-migration#optional-no-longer-implies-default
title: "Optional[X] no longer implies a default of None"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [Optional]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [fields, required-optional, silent-behaviour-change]
---

In V1, annotating a field `Optional[str]` with no default made it optional and
gave it an implicit default of `None`. In V2 it does not: `Optional[str]` says
only that `None` is an allowed *value*, and the field is required unless a
default is written out.

This is the change most likely to pass review unnoticed, because the code does
not change shape — the same annotation means something different. A model that
V1 accepted with the field absent now raises `ValidationError` for a missing
field, and the failure appears at the first request carrying that shape rather
than at import or at startup.

The V1 form, in which `nickname` is optional:

```python
from typing import Optional
from pydantic import BaseModel


class User(BaseModel):
    name: str
    nickname: Optional[str]
```

The V2 form, if the field is meant to stay optional:

```python
from typing import Optional
from pydantic import BaseModel


class User(BaseModel):
    name: str
    nickname: Optional[str] = None
```

Migration note: the fix is to add `= None`, and the risk is deciding which
fields were *meant* to be optional. An annotation alone no longer records that
intent, so a field that was required-in-spirit and `Optional`-in-annotation is
indistinguishable from one that was genuinely optional. Reviewing these by hand
is worth more than a codemod that adds `= None` everywhere.
