"""Who is calling, and whether they may touch this run.

ADR-002 D4 and D6. Two questions that are easy to conflate and must not be:
*authentication* asks whether there is a valid Clerk session, and
*authorization* asks whether that session owns the run being addressed. This
module answers both, and answers them differently on purpose -- an absent
session is a 401 telling the caller to sign in, while somebody else's run is a
404 that reveals nothing.

**Why the 404.** A distinct 403 for "that run is not yours" would confirm that
the thread id exists, which is the one fact an enumerating caller cannot get
any other way. Thread ids are unguessable, so refusing to distinguish "not
yours" from "not there" costs a legitimate caller nothing -- they never see
either for their own runs -- and costs an illegitimate one everything.
"""

from dataclasses import dataclass
from typing import Annotated

from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import Depends, Request

from upgradepilot.api.deps import RuntimeDep
from upgradepilot.api.runtime import Runtime
from upgradepilot.models.errors import (
    THREAD_NOT_FOUND_MESSAGE,
    ThreadNotFoundError,
    UnauthenticatedError,
)


@dataclass(frozen=True)
class Caller:
    """The authenticated user, or the absence of authentication.

    `user_id is None` means the deployment has no Clerk key configured, which
    is the local and test posture -- not "a caller we failed to identify". A
    failed identification raises instead of arriving here, so this type cannot
    represent a rejected request and no downstream check has to remember the
    difference.
    """

    user_id: str | None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


async def authenticate(request: Request, runtime: RuntimeDep) -> Caller:
    """Verify the Clerk session token, or pass through when auth is disabled.

    `authenticate_request_async`, not the synchronous sibling: it fetches and
    caches Clerk's JWKS, and a blocking network call on the event loop would
    stall every other run's status poll -- the same reason
    `analyze_repo` uses `asyncio.to_thread`.

    Starlette's `Request` is handed over directly. Clerk's `Requestish` is a
    Protocol requiring only `headers: Mapping[str, str]`, which `Request`
    already satisfies, so there is no adapter here to drift.

    `authorized_parties` reuses `cors_origins` rather than adding a setting.
    Both answer the same question -- which origins this API belongs to -- and
    Clerk uses it to reject a token minted for a different application. Two
    settings holding one fact would eventually disagree, and the failure would
    be an accepted token from somewhere else.
    """
    if runtime.clerk is None:
        return Caller(user_id=None)

    state = await runtime.clerk.authenticate_request_async(
        request,
        AuthenticateRequestOptions(
            secret_key=runtime.settings.clerk_secret_key.get_secret_value()
            if runtime.settings.clerk_secret_key
            else None,
            authorized_parties=list(runtime.settings.cors_origins),
        ),
    )
    if not state.is_signed_in:
        # `state.reason` names which check failed -- expired, wrong party, no
        # token. It goes to `detail` and never to `message`: rule 27 keeps the
        # user-facing text comprehensible, and telling an unauthenticated
        # caller precisely why their token was refused is a probing oracle.
        raise UnauthenticatedError(
            "Sign in to use this service.",
            detail=f"clerk rejected the session: {state.reason}",
        )

    subject = (state.payload or {}).get("sub")
    if not isinstance(subject, str) or not subject:
        # Signed in with no subject claim should be impossible. Treated as a
        # rejection rather than trusted, because the alternative is a `None`
        # owner flowing into `claim()` and a run nobody can be shown to own.
        raise UnauthenticatedError(
            "Sign in to use this service.",
            detail="clerk reported a signed-in session with no 'sub' claim",
        )
    return Caller(user_id=subject)


CallerDep = Annotated[Caller, Depends(authenticate)]


async def claim_run(runtime: Runtime, thread_id: str, caller: Caller) -> None:
    """Record the caller as this run's owner, when there is one to record."""
    if runtime.ownership is None or caller.user_id is None:
        return
    await runtime.ownership.claim(thread_id, caller.user_id)


async def require_owner(runtime: Runtime, thread_id: str, caller: Caller) -> None:
    """Raise `ThreadNotFoundError` unless this caller owns the run.

    **An unclaimed run is refused, not shared.** A thread with no owner row is
    treated exactly like one owned by somebody else, because the two cases are
    indistinguishable from the outside and the permissive reading is the
    dangerous one: it would make every run that predates this table, or whose
    claim failed to write, readable by anyone signed in.
    """
    if runtime.ownership is None or caller.user_id is None:
        return
    if await runtime.ownership.owner_of(thread_id) != caller.user_id:
        # The same message a genuinely missing thread gets, from the same
        # constant, so the two cannot be told apart by response text.
        raise ThreadNotFoundError(THREAD_NOT_FOUND_MESSAGE)
