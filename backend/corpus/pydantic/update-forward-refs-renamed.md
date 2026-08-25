---
source_id: pydantic-v2-migration#update-forward-refs-renamed
title: "update_forward_refs() is replaced by model_rebuild()"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [update_forward_refs, model_rebuild]
severity: low
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [forward-references, api-rename]
---

`Model.update_forward_refs()` — the call that resolves string annotations once
the referenced types exist — becomes `Model.model_rebuild()`. The old name
remains as a deprecated alias.

Most calls can simply be deleted rather than renamed. V2 resolves forward
references automatically in the common cases, including self-referencing
models, and `model_rebuild()` is needed only where a referenced type is defined
after the model and outside its module namespace.

The V1 form:

```python
class Node(BaseModel):
    children: list["Node"] = []


Node.update_forward_refs()
```

The V2 form:

```python
class Node(BaseModel):
    children: list["Node"] = []
```

Migration note: where a rebuild *is* still needed, the failure is a clear
`PydanticUndefinedAnnotation` at first use naming the unresolved type, so this
change does not fail silently.
