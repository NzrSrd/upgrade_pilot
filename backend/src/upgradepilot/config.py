"""Application configuration. The only place environment variables are read."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="UP_",
        extra="ignore",
    )

    # OpenAI. An explicit alias bypasses env_prefix entirely, so this is read
    # from OPENAI_API_KEY and *not* from UP_OPENAI_API_KEY. Verified.
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    chat_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"

    # Local stores
    chroma_dir: Path = Path("./.chroma")
    checkpoint_db: Path = Path("./checkpoints.db")
    workspace_dir: Path = Path("./.workspaces")

    # Repository access guards.
    # NoDecode is required: pydantic-settings JSON-decodes complex-typed env
    # values *before* field validators run, so a comma-separated string would
    # raise SettingsError. NoDecode disables that decode and lets _split_csv
    # handle the value. Verified against pydantic-settings 2.15.0.
    allowed_local_roots: Annotated[tuple[Path, ...], NoDecode] = ()
    allowed_url_schemes: Annotated[frozenset[str], NoDecode] = frozenset({"https", "git"})
    max_repo_files: int = 5000
    max_repo_bytes: int = 50 * 1024 * 1024
    clone_depth: int = 100

    # Graph and run limits
    max_rag_iterations: int = 3
    max_concurrent_runs: int = 4

    # API
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ("http://localhost:5173",)

    @field_validator("allowed_local_roots", "allowed_url_schemes", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated strings from .env for collection fields."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
