---
source_id: pydantic-v2-migration#parsing-methods-renamed
title: "parse_obj and parse_raw are replaced by model_validate and model_validate_json"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [parse_obj, parse_raw, parse_file, model_validate, model_validate_json]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [parsing, api-rename]
---

The class-level constructors were renamed. `Model.parse_obj(data)` becomes
`Model.model_validate(data)` and `Model.parse_raw(text)` becomes
`Model.model_validate_json(text)`. Both old names survive as deprecated
aliases.

`parse_file` has no direct replacement. It was removed rather than renamed, on
the grounds that reading a file is the caller's job: the V2 equivalent is to
read the text and pass it to `model_validate_json`.

`parse_raw` in V1 also accepted non-JSON content types via a `content_type`
argument and could be pointed at pickle. None of that survives; V2's
`model_validate_json` parses JSON only.

The V1 form:

```python
user = User.parse_obj(payload)
user = User.parse_raw(body)
user = User.parse_file("user.json")
```

The V2 form:

```python
user = User.model_validate(payload)
user = User.model_validate_json(body)
user = User.model_validate_json(Path("user.json").read_text())
```

Migration note: `model_validate_json` is significantly faster than parsing to a
dict first and validating that, so the mechanical translation of `parse_raw` is
also the fast one.
