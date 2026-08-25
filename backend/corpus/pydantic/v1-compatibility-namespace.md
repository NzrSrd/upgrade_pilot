---
source_id: pydantic-v2-migration#v1-compatibility-namespace
title: "pydantic.v1 ships inside V2 for incremental migration"
source_type: compat_note
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [v1, BaseModel]
severity: low
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [compatibility, incremental-migration]
---

Installing V2 does not force every model in a codebase to move at once. V2
vendors the V1 implementation under `pydantic.v1`, so a module can keep its V1
models unchanged while the rest of the project moves.

```python
from pydantic import v1 as pydantic_v1


class LegacyPayload(pydantic_v1.BaseModel):
    class Config:
        orm_mode = True
```

The constraint that decides whether this helps: V1 and V2 models do not
interoperate. A V2 model cannot have a V1 model as a field type, and passing
one where the other is expected fails validation. The boundary between the two
worlds therefore has to fall somewhere with a clean data interface — a module,
a service edge — rather than in the middle of a model graph.

Migration note: this is a staging tool, not a destination. Code left on
`pydantic.v1` gets no V2 performance benefit and no upstream fixes, and it
carries the cost of a second mental model for anyone reading it.
