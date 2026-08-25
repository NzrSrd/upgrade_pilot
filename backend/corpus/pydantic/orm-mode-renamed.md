---
source_id: pydantic-v2-migration#orm-mode-renamed
title: "orm_mode becomes from_attributes and from_orm becomes model_validate"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [orm_mode, from_orm, from_attributes]
severity: medium
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [orm, configuration, api-rename]
---

Reading a model from an arbitrary object rather than a mapping was configured
in V1 with `Config.orm_mode = True` and performed with `Model.from_orm(obj)`.
In V2 the setting is `model_config = ConfigDict(from_attributes=True)` and the
call is `Model.model_validate(obj)`.

The rename is not only cosmetic: `from_attributes` can also be set per-field
via `Field`, and `model_validate` takes it as a keyword, so the behaviour is no
longer only a whole-model setting.

The V1 form:

```python
class UserOut(BaseModel):
    id: int
    name: str

    class Config:
        orm_mode = True


user = UserOut.from_orm(db_row)
```

The V2 form:

```python
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


user = UserOut.model_validate(db_row)
```

Migration note: calling `model_validate` on an ORM row *without*
`from_attributes` set raises a validation error saying the input should be a
valid dictionary — a message that does not obviously point at the missing
config, which is why this one costs debugging time out of proportion to its
size.
