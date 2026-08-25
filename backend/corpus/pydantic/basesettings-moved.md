---
source_id: pydantic-v2-migration#basesettings-moved
title: "BaseSettings moved out of pydantic into pydantic-settings"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [BaseSettings]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-25
tags: [settings, packaging, new-dependency]
---

`BaseSettings` is no longer part of `pydantic`. It lives in a separate
distribution, `pydantic-settings`, which must be added to the project's
dependencies before the import can be rewritten.

This is the one change in the set that a codemod cannot finish on its own,
because it requires a dependency to be added rather than a symbol to be
renamed. `from pydantic import BaseSettings` under V2 raises an error whose
message names `pydantic-settings`, so it fails loudly and early — at import
time, not at first use.

The V1 form:

```python
from pydantic import BaseSettings


class Settings(BaseSettings):
    api_key: str

    class Config:
        env_prefix = "APP_"
```

The V2 form:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")

    api_key: str
```

Migration note: settings models take `SettingsConfigDict`, not `ConfigDict`.
The former is a superset carrying the settings-only keys such as `env_prefix`
and `env_file`, and using the plain `ConfigDict` drops them silently.
