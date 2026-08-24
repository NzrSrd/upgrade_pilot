"""Application configuration. The only place environment variables are read."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    AliasChoices,
    BeforeValidator,
    Field,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _reject_blank_text(value: object) -> object:
    """Reject a configuration value that is present but says nothing.

    Runs before coercion so the original string is still visible: once
    pydantic has built a `Path`, `""` and `"   "` are indistinguishable from
    a deliberate `"."`, and `Path("")` silently *is* `Path(".")`.

    This is the fifth appearance of the same defect in this project. An
    unset-but-exported variable -- `UP_WORKSPACE_DIR=` -- is not the same as
    an unset one: the loader sees `""`, `Path("")` normalises to `Path(".")`,
    and the process working directory becomes the configured location.
    Demonstrated for `workspace_dir`, whose consumer is `sweep_stale`: a
    directory `repo-users-important-work/` in the process CWD was matched as
    a stale workspace and removed with `rmtree`.

    The after-validators below would reject a blank value anyway, so what
    this adds is a truthful *message*. Without it the operator who exported
    an empty variable is told the value was `'.'`, which they never wrote and
    cannot find in their configuration. Saying "must not be blank" names the
    thing they can actually fix, so that is what the test asserts.
    """
    if isinstance(value, str) and not value.strip():
        raise ValueError("must not be blank; unset the variable instead of setting it empty")
    return value


def _reject_cwd_relative_path(value: Path) -> Path:
    """Reject a path that names the working directory, or climbs out of it.

    Rejects rather than normalises, deliberately. The operator's intent
    behind a blank or `.`-shaped value is unknown, and for `workspace_dir`
    the consequence of guessing wrong is `rmtree` on the wrong tree. This
    mirrors `services/repo/guards.py`, which refuses a non-absolute entry in
    `allowed_local_roots` for the same reason rather than filtering it out.

    What is rejected, and only this:

    - `Path("")` and `Path(".")`, both of which have no parts at all, so they
      *are* the working directory rather than a location inside it.
    - any path containing `..`, which resolves to somewhere the configured
      value does not name.
    - a component that is only whitespace, which is a legal directory name
      and never an intended one. Interior single spaces are untouched:
      `/Users/me/My Documents/work` stays valid.

    A relative path that names a real subdirectory -- the shipped
    `./.workspaces` default -- is accepted. It cannot become the CWD itself,
    which is the failure mode above; it can only be a directory underneath
    it, which is what the default has always meant. Requiring absolute paths
    here would instead mean no setting could have a working default at all.
    """
    if not value.parts:
        raise ValueError(
            f"must name a location, not the working directory itself (got {str(value)!r})"
        )
    if ".." in value.parts:
        raise ValueError(f"must not contain '..' (got {str(value)!r})")
    if any(not part.strip() for part in value.parts):
        raise ValueError(f"must not contain a blank path component (got {str(value)!r})")
    return value


def _require_absolute_root(value: Path) -> Path:
    """Reject a local-repository allowlist entry that is not an absolute,
    plainly-spelled path.

    `services/repo/guards.py` already refuses non-absolute roots at use time,
    and it must keep doing so: it is the security boundary, and it is
    reachable by programmatic construction that never passes through this
    file. This check is not a replacement for it but an earlier failure -- a
    misconfigured security allowlist should stop the process at startup
    rather than fail the first request that happens to exercise it. The
    setting now governs `file://` clones as well as local paths, so both
    doors depend on it.

    Absoluteness is checked first because it names the actionable fix for the
    common mistake. Then the *same* shape rules `StorePath` applies, by
    calling the one function rather than restating them: an allowlist root
    and a store location are both "a filesystem location an operator
    configured", so a rule that holds for one holds for the other.

    `..` is the case that was actually wrong. `allowed_local_roots` accepted
    `/tmp/a/../etc` while `workspace_dir` rejected it -- not exploitable,
    because `guards.py` resolves each root before comparing, but a configured
    policy that reads as one directory and means another is the same defect
    as an allowlist entry that silently matches nothing, and this branch has
    repeatedly shipped a rule applied to the instance that prompted it and
    not to its sibling. `test_both_path_setting_classes_share_their_shape_rules`
    is what stops the two drifting apart again.
    """
    if not value.is_absolute():
        raise ValueError(f"must be an absolute path (got {str(value)!r})")
    return _reject_cwd_relative_path(value)


def _reject_blank_element(value: str) -> str:
    """Reject a blank element of a collection setting.

    `_split_csv` filters blank parts, so this cannot be reached from an
    environment variable; it holds for programmatic construction, which is
    how every test builds a `Settings`. A blank element of an allowlist is
    never meaningful and can be actively wrong -- an empty string in
    `allowed_url_schemes` would be compared against `urlsplit()`'s empty
    scheme for an input that has no scheme at all.
    """
    if not value.strip():
        raise ValueError("must not be blank")
    return value


StorePath = Annotated[
    Path, BeforeValidator(_reject_blank_text), AfterValidator(_reject_cwd_relative_path)
]
"""A configured filesystem location: never blank, never the CWD, never `..`."""

AllowedRoot = Annotated[
    Path, BeforeValidator(_reject_blank_text), AfterValidator(_require_absolute_root)
]
"""An entry in the local-repository allowlist. Absolute, or rejected."""

_URL_SCHEME = re.compile(r"[a-z][a-z0-9+.\-]*\Z")
r"""RFC 3986 `scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )`, already
lowercased. `\Z` and not `$`, so a trailing newline is not quietly allowed."""


def _require_matchable_scheme(value: str) -> str:
    """Reject an allowlist entry that could never match a parsed scheme.

    `validate_clone_url` compares `urlsplit(...).scheme` against this
    allowlist, and `urlsplit` always lowercases the scheme it returns. An
    entry that is not a lowercase RFC 3986 scheme therefore matches nothing,
    ever -- so `UP_ALLOWED_URL_SCHEMES=HTTPS` refused every clone, and the
    refusal read "Repository URL scheme must be one of: HTTPS." against a
    URL whose `detail` said `scheme='https'`. The message named the operator's
    own value as the thing they were missing.

    Rejected here rather than lowercased. Of the three available behaviours,
    silently matching nothing is the worst; rewriting the entry is second
    worst, because then the effective policy is not the configured policy and
    a security allowlist is the last place that should be true; refusing to
    start and naming the fix is the only one where the operator learns what
    is wrong.

    The lowercase case is checked first so it gets the message that names its
    own fix, rather than falling into the general "not a valid scheme" arm.
    The shape check behind it closes the rest of the same class -- `https://`,
    `ht tps`, `ħttps` were each accepted here and then silently matched
    nothing, exactly as `HTTPS` did. No legitimate scheme is affected:
    `git+ssh`, `svn+ssh`, `view-source` and `h2c` are all valid under RFC
    3986 and all pass.
    """
    if value != value.lower():
        raise ValueError(
            f"must be lowercase (got {value!r}): schemes are compared against the scheme "
            f"urlsplit() parses out, which is always lowercased, so {value!r} would match "
            f"nothing and every clone would be refused -- use {value.lower()!r}"
        )
    if not _URL_SCHEME.match(value):
        raise ValueError(
            f"is not a URL scheme (got {value!r}): RFC 3986 allows a letter followed by "
            "letters, digits, '+', '-' and '.' -- name the scheme alone, e.g. 'https', "
            "not a prefix or a whole URL"
        )
    return value


NonBlankSetting = Annotated[str, AfterValidator(_reject_blank_element)]

_HTTP_BASE_URL = re.compile(r"https?://[^\s]+\Z")
r"""An absolute http(s) base URL with no whitespace.

`\Z` and not `$` so a trailing newline -- what a shell heredoc or a copied
`.env` line leaves behind -- is not quietly accepted into a URL the HTTP
client then fails on with a message about the host.
"""


def _require_http_base_url(value: str) -> str:
    """Reject a base URL the OpenAI client could not use.

    `openrouter.ai/api/v1`, with no scheme, is the shape an operator
    naturally types and the one that fails worst: the client treats it as a
    relative reference and the eventual error names a host nobody
    configured. Refused here, where the message can name the variable.
    """
    if not _HTTP_BASE_URL.match(value):
        raise ValueError(f"must be an absolute http(s) URL with no whitespace (got {value!r})")
    return value


BaseUrl = Annotated[
    str, BeforeValidator(_reject_blank_text), AfterValidator(_require_http_base_url)
]
"""An OpenAI-compatible API base URL. Absolute, or rejected at startup."""


UrlScheme = Annotated[NonBlankSetting, AfterValidator(_require_matchable_scheme)]
"""An entry in the clone-URL scheme allowlist. Lowercase and scheme-shaped,
or rejected at startup -- an entry that cannot match is a policy that
silently denies everything."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="UP_",
        # `extra="ignore"`, kept deliberately. `forbid` was measured against
        # pydantic-settings 2.15.0 rather than assumed, and it does not do
        # what it looks like it does here:
        #
        #   - A mistyped *environment variable* -- `UP_ALLOWED_LOCAL_ROTS` --
        #     is still a silent no-op under `forbid`. `EnvSettingsSource`
        #     only harvests the variables that match a declared field, so an
        #     unknown `UP_*` variable never becomes an extra input and there
        #     is nothing for `forbid` to reject. Verified both ways.
        #   - What `forbid` *does* reject is any key in the `.env` file that
        #     is not a field, including keys with no `UP_` prefix. A `.env`
        #     shared with any other tool on the machine -- an `AWS_REGION`, a
        #     second project's token -- makes `get_settings()` raise.
        #
        #     This clause once cited this repository's own `.env` and its
        #     `OPENROUTER_API_KEY` as the example. That stopped being true
        #     when `llm_api_key` began reading that variable, so the example
        #     is now the general case rather than a local fact that quietly
        #     expired. The measurement it supports is unchanged.
        #
        # So `forbid` costs a real, reproducible startup failure and buys
        # nothing against the failure mode it was proposed for. Catching a
        # typo'd env var needs a different mechanism (scanning `os.environ`
        # for unknown `UP_*` keys), which is a separate decision with its own
        # cost -- an injected `UP_*` variable from unrelated infrastructure
        # would then hard-fail startup. Not taken here.
        extra="ignore",
        # Required by `llm_api_key` / `llm_base_url`, which use
        # `validation_alias`. Without it a field carrying a validation_alias
        # can ONLY be populated by that alias -- `Settings(llm_api_key="sk-x")`
        # returns a Settings whose `llm_api_key` is None, silently, because
        # `extra="ignore"` swallows the keyword as an unknown extra rather
        # than raising. Every test in this repository builds Settings by
        # keyword, so the failure would have been a suite that tests an
        # unconfigured object while appearing to test a configured one.
        #
        # `validate_by_name`, not the older `populate_by_name`: both were
        # measured to work identically here, and the latter is deprecated in
        # pydantic 2.13.4.
        validate_by_name=True,
    )

    # The chat/embedding provider. Named for the ROLE, not for a vendor:
    # OpenRouter serves the same OpenAI-compatible API, and the fields that
    # select it should not read as if one vendor were assumed. Spec 12
    # assumption 4 is amended to match.
    #
    # `AliasChoices` bypasses `env_prefix` entirely -- measured against
    # pydantic-settings 2.15.0 rather than assumed:
    #
    #   OPENROUTER_API_KEY  >  OPENAI_API_KEY  >  UP_LLM_API_KEY
    #
    # All three are read, and that fixed order is what a machine carrying
    # more than one of them resolves by -- a shared shell profile exporting
    # `OPENAI_API_KEY` for other tools alongside this project's own key is
    # the ordinary case, and an unpinned order would select a provider
    # nobody chose.
    #
    # The `UP_` spelling works ONLY because `validate_by_name=True` is set
    # in model_config above. It was measured as ignored before that flag was
    # added and measured again afterwards, because adding it changed the
    # answer; the test pins the order that is true now.
    #
    # SecretStr, not str: `repr(settings)` is the shape that reaches a log
    # line, a traceback frame and a pytest failure report, and a plain `str`
    # put the key in all three.
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "OPENAI_API_KEY"),
    )
    llm_base_url: BaseUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_BASE_URL", "OPENAI_BASE_URL"),
    )
    """Where the OpenAI-compatible API lives. `None` means OpenAI direct --
    the client's own default -- so an unset value keeps the previous
    behaviour exactly and only an explicit setting redirects it."""

    chat_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    """Model identifiers are PROVIDER-SCOPED and the two providers disagree:
    OpenAI direct wants `gpt-4.1-mini`, OpenRouter wants
    `openai/gpt-4.1-mini`. The defaults here are OpenAI's, matching the
    `llm_base_url=None` default, so the shipped configuration is internally
    consistent; `.env.example` documents both sets."""

    # Local stores
    chroma_dir: StorePath = Path("./.chroma")
    checkpoint_db: StorePath = Path("./checkpoints.db")
    workspace_dir: StorePath = Path("./.workspaces")

    # Repository access guards.
    # NoDecode is required: pydantic-settings JSON-decodes complex-typed env
    # values *before* field validators run, so a comma-separated string would
    # raise SettingsError. NoDecode disables that decode and lets _split_csv
    # handle the value. Verified against pydantic-settings 2.15.0.
    allowed_local_roots: Annotated[tuple[AllowedRoot, ...], NoDecode] = ()
    allowed_url_schemes: Annotated[frozenset[UrlScheme], NoDecode] = frozenset({"https", "git"})
    max_repo_files: int = 5000
    max_repo_bytes: int = 50 * 1024 * 1024
    # ge=1: `clone.py` clamps with `max(1, depth)`, so a configured 0 became
    # a depth-1 clone -- silently destroying the churn signal that `depth`
    # exists to provide. The clamp is a reasonable last-ditch defence; the
    # setting is the right place to refuse the value.
    clone_depth: int = Field(default=100, ge=1)

    # Graph and run limits
    max_rag_iterations: int = 3
    max_concurrent_runs: int = 4

    # API
    cors_origins: Annotated[tuple[NonBlankSetting, ...], NoDecode] = ("http://localhost:5173",)

    @field_validator("allowed_local_roots", "allowed_url_schemes", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated strings from .env for collection fields."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def llm_configured(self) -> bool:
        """Whether a usable key is present.

        Spelled out rather than `bool(self.llm_api_key)`: a `SecretStr`
        wrapping `""` is an object, and reading it as truthy would report a
        configured key where `OPENROUTER_API_KEY=` had been exported empty.

        Stripped for the same reason `NonBlankStr` strips -- a key of three
        spaces is not a key, and reporting it as configured would put a lie
        in the health check and turn a misconfiguration into a 401 from the
        provider instead of a clear local answer.
        """
        return self.llm_api_key is not None and bool(self.llm_api_key.get_secret_value().strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
