"""Application configuration. The only place environment variables are read."""

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    BeforeValidator,
    Field,
    SecretStr,
    field_validator,
    model_validator,
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
_POSTGRES_URL = re.compile(r"postgres(?:ql)?://[^\s]*\Z")
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


def _require_postgres_url(value: str) -> str:
    """Reject a checkpointer URL psycopg could not open.

    This setting selects the checkpointer backend by its presence, which
    makes a malformed value worse than a missing one: anything non-empty
    routes the run away from SQLite, so a typo does not fall back -- it picks
    Postgres and then fails to connect, and the operator sees a connection
    error rather than "that is not a Postgres URL".

    The scheme is the whole check, and both spellings are accepted because
    psycopg accepts both. Host, port, database and parameters are
    deliberately not validated: Cloud Run reaches Cloud SQL over a unix
    socket, spelled `postgresql://user:pw@/db?host=/cloudsql/INSTANCE` with
    an empty host section, and a rule demanding a hostname would reject the
    one topology ADR-002 actually deploys.
    """
    if not _POSTGRES_URL.match(value):
        raise ValueError(
            f"must be a postgresql:// or postgres:// URL with no whitespace (got {value!r})"
        )
    return value


PostgresUrl = Annotated[
    str, BeforeValidator(_reject_blank_text), AfterValidator(_require_postgres_url)
]
"""A Postgres connection URL. Selects the Postgres checkpointer by existing."""


UrlScheme = Annotated[NonBlankSetting, AfterValidator(_require_matchable_scheme)]
"""An entry in the clone-URL scheme allowlist. Lowercase and scheme-shaped,
or rejected at startup -- an entry that cannot match is a policy that
silently denies everything."""


_AUTHORIZED_PARTY = re.compile(r"https?://[A-Za-z0-9.\-*]+(?::\d+)?\Z")
r"""A browser origin, optionally carrying one `*` inside its leftmost label.

Scheme, host, optional port, and nothing else. Clerk's `azp` claim is the
origin the token was minted on, which never has a path, so an entry with one
would match nothing. `\Z` and not `$` for the reason `_HTTP_BASE_URL` gives.
"""


def _require_authorized_party(value: str) -> str:
    """Reject an allowlist entry that is not an origin, or that is too broad.

    This is the `azp` allowlist Clerk's token verification used to be handed
    directly, and the grammar exists because the deployed frontend does not
    have one origin. Vercel gives every deployment an immutable URL of the
    form `<project>-<hash>-<scope>.vercel.app` alongside the project alias,
    so an exact list is a list that goes stale on the next `vercel deploy`.
    One `*`, bounded to a single label, names the whole scope without naming
    anything outside it: the scope slug is globally unique on Vercel, so
    `https://*-upgrade-pilot.vercel.app` cannot be claimed by a stranger.

    What is refused, and why each case is refused rather than narrowed:

    - `https://*.vercel.app` and `https://*`, where the wildcard *is* the
      leftmost label. Both read as "our deployments" and mean "anybody's".
      This is the one mistake that turns the allowlist into an open door,
      and it is the one an operator is most likely to type.
    - a wildcard anywhere but the leftmost label, and more than one wildcard.
      Neither is needed for any URL Vercel issues, and both make the entry
      harder to read than to write.
    - a bare host with no scheme, or anything carrying a path. `azp` is an
      origin; these would match nothing and deny every caller silently.
    """
    if not _AUTHORIZED_PARTY.match(value):
        raise ValueError(
            f"must be a browser origin -- scheme, host, optional port, no path (got {value!r})"
        )
    host = value.split("://", 1)[1].partition(":")[0]
    if "*" not in host:
        return value
    if host.count("*") > 1:
        raise ValueError(f"must contain at most one '*' (got {value!r})")
    label, dot, rest = host.partition(".")
    if "*" in rest:
        raise ValueError(
            f"may only wildcard the leftmost label (got {value!r}): a '*' further right "
            "matches hostnames in domains this deployment does not control"
        )
    if not dot or label == "*":
        raise ValueError(
            f"is too broad (got {value!r}): a '*' that is the whole leftmost label admits "
            "every host under that domain, including ones belonging to strangers -- give "
            "the label a literal part, e.g. 'https://*-your-vercel-scope.vercel.app'"
        )
    return value


AuthorizedParty = Annotated[NonBlankSetting, AfterValidator(_require_authorized_party)]
"""An entry in the Clerk `azp` allowlist. An origin, or one scoped wildcard."""


def _matches_authorized_party(pattern: str, azp: str) -> bool:
    """Whether one allowlist entry admits this `azp` claim.

    The wildcard matches at least one character and never a `.`, so the entry
    spans exactly one label. Matching across a dot is what would make
    `https://*-upgrade-pilot.vercel.app` admit
    `https://anything.attacker.example-upgrade-pilot.vercel.app`, a host the
    pattern does not read as naming.
    """
    prefix, star, suffix = pattern.partition("*")
    if not star:
        return pattern == azp
    if len(azp) <= len(prefix) + len(suffix):
        return False
    if not azp.startswith(prefix) or not azp.endswith(suffix):
        return False
    return "." not in azp[len(prefix) : len(azp) - len(suffix)]


def authorizes_party(patterns: Sequence[str], azp: str | None) -> bool:
    """Whether a token minted on `azp` may call this API.

    Moved out of `clerk_backend_api` deliberately. Its own check is an exact
    membership test against `authorized_parties`, which cannot express the
    set of origins a Vercel project actually serves, so the SDK is asked to
    skip the check and this function makes the decision instead. Everything
    else about verification -- signature, expiry, issuer -- stays in the SDK.

    `None` is refused, matching what the SDK does with a payload carrying no
    `azp` while an allowlist is configured. A token minted somewhere that
    sent no origin is not a token this deployment can place.
    """
    if azp is None:
        return False
    return any(_matches_authorized_party(pattern, azp) for pattern in patterns)


class ModelPrice(BaseModel):
    """What one model costs, per million tokens.

    Input and output are priced separately because every provider in use
    charges several times more for output. A single blended rate would
    misprice every call whose output-to-input ratio differs from whatever
    ratio the blend assumed -- which is all of them.
    """

    input_per_1m: float = Field(ge=0.0)
    output_per_1m: float = Field(ge=0.0)


DEFAULT_MODEL_PRICING: dict[str, ModelPrice] = {
    # US dollars per million tokens, as published on 2026-08-25. Rates change,
    # which is why this is a setting and not a code constant (spec §9.4) --
    # `UP_MODEL_PRICING` takes a JSON object and replaces the whole table.
    #
    # Both spellings of each model are listed, and that repetition is
    # deliberate. Model identifiers are provider-scoped and the two providers
    # disagree: OpenAI direct wants `gpt-4.1-mini`, OpenRouter wants
    # `openai/gpt-4.1-mini`. The tempting alternative -- strip the vendor
    # prefix and look up what is left -- is refused in `price_call`, because
    # the prefix names *who is serving* the model and a gateway sets its own
    # price. Stripping it yields a confident number wrong by the gateway's
    # margin; listing both yields two rows that can each be corrected.
    #
    # The OpenRouter rows currently carry OpenAI's list rates because that is
    # what the gateway passes through today. They are separate rows precisely
    # so that stops being an assumption the moment it stops being true. In
    # practice OpenRouter also reports its own per-call charge, which
    # `price_call` prefers over this table entirely.
    "gpt-4.1-mini": ModelPrice(input_per_1m=0.40, output_per_1m=1.60),
    "openai/gpt-4.1-mini": ModelPrice(input_per_1m=0.40, output_per_1m=1.60),
    "text-embedding-3-small": ModelPrice(input_per_1m=0.02, output_per_1m=0.0),
    "openai/text-embedding-3-small": ModelPrice(input_per_1m=0.02, output_per_1m=0.0),
}
"""The shipped price table. An unknown model yields `cost = None`, never a
fabricated `$0.00` -- see `services/llm/pricing.price_call`."""


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

    model_pricing: dict[str, ModelPrice] = Field(
        default_factory=lambda: dict(DEFAULT_MODEL_PRICING)
    )
    """Per-model rates, replaceable wholesale via `UP_MODEL_PRICING` as JSON.

    A `dict` field is JSON-decoded by pydantic-settings before validators run,
    which is the behaviour wanted here -- unlike the CSV collection settings
    below, which need `NoDecode` for the opposite reason."""

    # Local stores
    corpus_dir: StorePath | None = None
    """Where the authored corpus lives. `None` means the one that ships with
    the backend (`services/knowledge/corpus.CORPUS_ROOT`).

    Defaults to `None` rather than to a relative path, deliberately. Every
    other path setting here is resolved against the process working
    directory, which is right for a *store* the operator chooses the location
    of; the corpus is content shipped alongside the code, so a relative
    default would make ingestion succeed or fail depending on which directory
    it was invoked from. `None` means "the one next to the code", found by
    file location and immune to that."""

    chroma_dir: StorePath = Path("./.chroma")
    checkpoint_db: StorePath = Path("./checkpoints.db")
    workspace_dir: StorePath = Path("./.workspaces")

    clerk_secret_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CLERK_SECRET_KEY"),
    )
    """The Clerk secret key, or `None` for an unauthenticated API.

    ADR-002 D4. `CLERK_SECRET_KEY` is the name Clerk's own docs, CLI and SDK
    use, so a shell that already has it works without translation -- the same
    reason `llm_api_key` reads `OPENROUTER_API_KEY` before its own prefixed
    spelling. `UP_CLERK_SECRET_KEY` also works, and only because
    `validate_by_name=True` is set in `model_config`.

    **`None` leaves the API open, and that is deliberate rather than a
    permissive default.** The hermetic suite and local development have no
    Clerk instance and must not need one (rule 22), and inventing a fake key
    to satisfy a mandatory setting would mean every test ran against a code
    path production does not use. What makes it safe is that the absence is
    *reported* rather than assumed harmless: `/api/health` publishes
    `auth_required`, so a deployment that forgot the key says so in the one
    place an operator looks, instead of silently accepting every caller."""

    checkpoint_url: PostgresUrl | None = None
    """The Postgres checkpointer, or `None` for the SQLite file above.

    ADR-002 D2. Set in a hosted deployment and unset everywhere else, so
    `None` is the configuration this project develops and tests under and
    the hermetic suite needs no database (rule 22).

    **Why a second setting rather than widening `checkpoint_db`.** A single
    field holding either a path or a URL would have to guess which it was
    handed, and the guess is wrong exactly when it matters: a value that
    fails to parse as a URL would be treated as a filename, so a typo in a
    production DSN would silently create a SQLite file named after the typo
    and the run would appear to work while every paused run was again held on
    an ephemeral disk. Two fields cannot do that -- `checkpoint_url` is
    either a Postgres URL or absent, and `_require_postgres_url` refuses
    everything in between.

    When both are set, this one wins and `checkpoint_db` is ignored rather
    than rejected. A deployment sets the URL by adding one variable; making
    it also unset a path that has a default would be a second step whose
    omission is an error, for no benefit."""

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
    """Which browser origins may call this API cross-origin.

    This and nothing else. It used to be handed to Clerk as
    `authorized_parties` as well, on the reasoning that both answer "which
    origins does this API belong to" -- which held right up until the deployed
    frontend had more than one origin. ADR-002 D4 notes that the Vercel
    rewrite keeps the browser same-origin so CORS is never exercised, and
    concluded that setting this "costs nothing". Because the value was doing
    double duty, it cost every origin but the one listed a 401 from Clerk.
    `authorized_parties` below is now the separate setting."""

    authorized_parties: Annotated[tuple[AuthorizedParty, ...], NoDecode] = ()
    """Which frontends' session tokens this API accepts, by `azp` claim.

    Empty by default because an open deployment never consults it: with no
    Clerk key there is no token to place, which is the local and test posture
    (rule 22). A gated one must fill it in, and
    `_a_gated_deployment_names_its_frontends` refuses to boot until it does."""

    @field_validator(
        "allowed_local_roots",
        "allowed_url_schemes",
        "cors_origins",
        "authorized_parties",
        mode="before",
    )
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

    @model_validator(mode="after")
    def _an_authenticated_deployment_needs_postgres(self) -> "Settings":
        """Refuse to boot with a gate but no way to enforce ownership.

        ADR-002 D6. Authentication without ownership is a *downgrade*: it
        replaces "nobody can get in" with "anyone who is in can read and
        resume anyone else's run", including answering someone else's pending
        decision, which the append-only `human_decisions` channel would then
        record as that user's answer. The run registry and the ownership table
        both live in Postgres, so a Clerk key without `UP_CHECKPOINT_URL`
        describes a deployment where the gate exists and ownership cannot be
        checked.

        Refused here rather than handled per-request, because the alternative
        is a service that starts, looks gated, and silently shares runs. This
        is the one configuration whose failure mode is invisible from the
        outside, so it fails at startup where an operator is watching.

        The pairing is one-directional on purpose: Postgres without Clerk is
        fine and is what a single-tenant deployment looks like. It is only the
        gate that implies ownership.
        """
        if self.auth_required and self.checkpoint_url is None:
            raise ValueError(
                "CLERK_SECRET_KEY is set but UP_CHECKPOINT_URL is not. "
                "Authenticated deployments need the Postgres checkpointer, "
                "because run ownership is stored there and a gate without "
                "ownership lets any signed-in user read and resume any run. "
                "Set UP_CHECKPOINT_URL, or unset CLERK_SECRET_KEY to run open."
            )
        return self

    @model_validator(mode="after")
    def _a_gated_deployment_names_its_frontends(self) -> "Settings":
        """Refuse to boot with a gate that would reject every caller.

        `authorizes_party` admits nobody against an empty allowlist, so a
        Clerk key with no `UP_AUTHORIZED_PARTIES` describes a deployment that
        starts, reports `auth_required: true`, serves `/api/health`, and 401s
        every single run. That is the exact failure this setting was split out
        of `cors_origins` to end, and leaving it reachable by omission would
        reintroduce it one deploy later.

        Fail-closed is the right default for an allowlist and the wrong one to
        arrive at silently, so the emptiness is refused where an operator is
        watching rather than discovered in a browser console.
        """
        if self.auth_required and not self.authorized_parties:
            raise ValueError(
                "CLERK_SECRET_KEY is set but UP_AUTHORIZED_PARTIES is empty. "
                "Clerk tokens carry the origin they were minted on, and this "
                "API refuses every origin it was not told to trust -- so the "
                "deployment would answer /api/health and reject every run. "
                "Set UP_AUTHORIZED_PARTIES to the frontend origins, e.g. "
                "https://your-app.vercel.app,https://*-your-scope.vercel.app"
            )
        return self

    @property
    def auth_required(self) -> bool:
        """Whether requests must carry a Clerk session.

        Same shape as `llm_configured`, but measured rather than assumed:
        `SecretStr` defines `__len__` and not `__bool__`, so `SecretStr("")`
        is already falsy and a naive `bool(...)` would catch the
        exported-empty case for free. What it would **not** catch is
        `SecretStr("   ")`, which has length 3 and is therefore truthy. So the
        `.strip()` is load-bearing for whitespace only, and
        `test_an_empty_clerk_key_reads_as_no_auth_rather_than_as_configured`
        proves exactly that: under a naive `bool(self.clerk_secret_key)` the
        blank-string case still passes and only the whitespace case goes red.

        Worth the precision because the failure direction is bad. Reporting a
        whitespace key as configured would announce the API as gated while
        every request was in fact rejected -- an operator reading
        `auth_required: true` would believe the deployment was private for
        the wrong reason.

        (The `llm_configured` docstring above states this as "a `SecretStr`
        wrapping `""` is an object, and reading it as truthy" -- which the
        measurement above contradicts. Left alone here rather than edited in
        passing, but it is imprecise in the same way.)
        """
        return self.clerk_secret_key is not None and bool(
            self.clerk_secret_key.get_secret_value().strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
