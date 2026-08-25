---
source_id: pydantic-v2-migration#schema-methods-renamed
title: "schema() is replaced by model_json_schema()"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [schema, schema_json, model_json_schema]
severity: medium
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [json-schema, api-rename]
---

`Model.schema()` becomes `Model.model_json_schema()` and `Model.schema_json()`
is dropped in favour of serialising that result yourself. The old names remain
as deprecated aliases.

The generated schema itself also changed, which matters more than the rename
for anything that consumes it. V2 targets JSON Schema 2020-12 rather than
draft 7, so definitions move from `#/definitions/...` to `#/$defs/...`, and an
`Optional[X]` field is described with `anyOf` rather than by omitting the type.
Anything asserting on the exact shape of a generated schema — an OpenAPI
snapshot test, a client generator — will see a diff even where the model is
unchanged.

The V1 form:

```python
schema = User.schema()
text = User.schema_json(indent=2)
```

The V2 form:

```python
import json

schema = User.model_json_schema()
text = json.dumps(User.model_json_schema(), indent=2)
```

Migration note: `model_json_schema(by_alias=True)` is the default, as it was in
V1, so aliased field names appear in the schema unless asked otherwise.
