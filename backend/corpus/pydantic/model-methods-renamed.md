---
source_id: pydantic-v2-migration#model-methods-renamed
title: "Instance methods gained a model_ prefix: dict, json, copy, construct"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [dict, json, copy, construct, BaseModel, model_dump, model_dump_json]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [serialization, api-rename]
---

`BaseModel`'s instance methods were renamed under a `model_` prefix so that
they cannot collide with a user's field names. `.dict()` becomes
`.model_dump()`, `.json()` becomes `.model_dump_json()`, `.copy()` becomes
`.model_copy()`, and `.construct()` becomes `.model_construct()`. The old names
remain as deprecated aliases in V2.

`.json()` is the one that changes behaviour rather than just its spelling. In
V1 it returned a JSON string produced by serialising the result of `.dict()`;
in V2 `.model_dump_json()` serialises directly in Rust, and
`.model_dump(mode="json")` is what returns a dict containing only
JSON-compatible types. Code that called `.dict()` and handed the result to
`json.dumps` will now fail on values V1 happened to convert on the way out.

The V1 form:

```python
payload = user.dict(exclude_none=True)
text = user.json()
duplicate = user.copy(update={"name": "other"})
```

The V2 form:

```python
payload = user.model_dump(exclude_none=True)
text = user.model_dump_json()
duplicate = user.model_copy(update={"name": "other"})
```

Migration note: `model_copy(update=...)` does **not** validate the update, in V2
exactly as in V1. A model built that way can hold values its own field
constraints would have rejected.
