---
source_id: pydantic-v2-migration#fields-attribute-renamed
title: "__fields__ and __fields_set__ are renamed to model_fields and model_fields_set"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [__fields__, __fields_set__, model_fields, model_fields_set]
severity: medium
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [introspection, api-rename]
---

Code that introspects a model — serialisers, form builders, admin interfaces,
test helpers — reaches for `Model.__fields__`. In V2 that becomes
`Model.model_fields`, and the per-instance `__fields_set__` becomes
`model_fields_set`.

The values changed as well as the names. `__fields__` in V1 held `ModelField`
objects; `model_fields` holds `FieldInfo` objects with a different surface.
Attributes such as `.outer_type_`, `.required` and `.field_info` do not exist
on the new object, so introspection code fails at the attribute access rather
than at the lookup — one line further along than where the rename is.

The V1 form:

```python
for name, field in User.__fields__.items():
    if field.required:
        print(name, field.outer_type_)
```

The V2 form:

```python
for name, field in User.model_fields.items():
    if field.is_required():
        print(name, field.annotation)
```

Migration note: `required` became the method `is_required()`, so the V1
expression `field.required` evaluates on V2 to a bound method — which is
truthy. An `if field.required:` carried across unchanged silently treats every
field as required.
