---
source_id: pydantic-v2-migration#extra-config-values
title: "The Extra enum is replaced by plain string values"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [Extra]
severity: low
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [configuration, api-rename]
---

V1 configured the handling of unexpected input fields with an `Extra` enum:
`Extra.ignore`, `Extra.allow`, `Extra.forbid`. V2 takes the string directly —
`extra="ignore"`, `extra="allow"`, `extra="forbid"` — and the enum is
deprecated.

The default is unchanged: extra fields are ignored unless configured otherwise.

The V1 form:

```python
from pydantic import BaseModel, Extra


class Payload(BaseModel):
    class Config:
        extra = Extra.forbid
```

The V2 form:

```python
from pydantic import BaseModel, ConfigDict


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

Migration note: `extra="forbid"` in V2 also applies to keyword arguments passed
to the constructor, not only to data passed through `model_validate`.
