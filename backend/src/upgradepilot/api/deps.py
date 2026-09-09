"""Shared FastAPI dependencies.

`get_runtime` lived in `routes/agent.py` until `api/auth.py` needed it too.
Leaving it there and importing it from `auth` would have been a cycle --
`agent` imports the caller dependency, `auth` imports the runtime one -- so it
moved here rather than being duplicated into a second three-line accessor
whose drift nobody would notice.
"""

from typing import Annotated

from fastapi import Depends, Request

from upgradepilot.api.runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:  # pragma: no cover - the lifespan always sets it
        raise RuntimeError("the application runtime was not initialised")
    return runtime  # type: ignore[no-any-return]


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
