---
source_id: pydantic-v2-migration#config-class-replaced
title: "The nested class Config is replaced by model_config = ConfigDict(...)"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [Config, ConfigDict, model_config]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [configuration, api-rename]
---

Model configuration moves from a nested `class Config` to a `model_config`
class attribute assigned a `ConfigDict`. The nested class still works in V2 and
is deprecated.

Several individual settings were renamed at the same time, which is what makes
a mechanical search-and-replace of the container insufficient: `orm_mode`
became `from_attributes`, `allow_population_by_field_name` became
`populate_by_name`, and `anystr_strip_whitespace` became `str_strip_whitespace`.
A `class Config` translated to `ConfigDict` with its keys carried across
verbatim will therefore contain keys V2 does not recognise.

The V1 form:

```python
from pydantic import BaseModel


class User(BaseModel):
    name: str

    class Config:
        orm_mode = True
        allow_population_by_field_name = True
```

The V2 form:

```python
from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    name: str
```

Migration note: `model_config` is an ordinary class attribute, so a field named
`model_config` is no longer possible. More generally, V2 protects the
`model_` prefix, and a field whose name starts with it now collides with the
namespace.
