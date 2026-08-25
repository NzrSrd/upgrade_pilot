---
source_id: pydantic-v2-migration#custom-types-core-schema
title: "Custom types implement __get_pydantic_core_schema__ instead of __get_validators__"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [__get_validators__, __modify_schema__, __get_pydantic_core_schema__]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [custom-types, breaking-removal]
---

A V1 custom type declared itself by yielding validator callables from
`__get_validators__`, and adjusted its JSON schema through `__modify_schema__`.
Neither hook is consulted by V2. They are replaced by
`__get_pydantic_core_schema__` and `__get_pydantic_json_schema__`.

This is a removal rather than a rename, and it is the most expensive item in a
typical V1 to V2 migration because there is no mechanical translation: the new
hook returns a `pydantic_core` schema describing how to validate the type,
which is a different model of the problem rather than a different spelling of
the same one.

The failure mode is quiet. An unmigrated custom type does not raise; its hooks
are simply never called, so the type validates as whatever its underlying
annotation says and the custom rules disappear.

The V1 form:

```python
class PostCode(str):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not RE.match(v):
            raise ValueError("bad postcode")
        return cls(v)
```

The V2 form, and usually the better one, is to stop writing a custom class and
use `Annotated` with a validator instead:

```python
from typing import Annotated
from pydantic import AfterValidator


def _check(v: str) -> str:
    if not RE.match(v):
        raise ValueError("bad postcode")
    return v


PostCode = Annotated[str, AfterValidator(_check)]
```

Migration note: because the old hooks fail silently, a custom type is worth
finding by search rather than by test — a suite that only exercises valid
inputs stays green while every custom rule has stopped running.
