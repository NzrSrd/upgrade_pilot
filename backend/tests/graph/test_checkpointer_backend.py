"""Which checkpointer backend `open_checkpointer` selects, and what it hands it.

ADR-002 D2 adds a Postgres backend selected by `Settings.checkpoint_url`.
Three properties are worth pinning, and only the first is obvious.

**The default must stay SQLite.** Rule 22 says unit tests touch no network,
and the whole hermetic suite depends on the absence of a URL meaning "a file".
A change that made Postgres the default would not fail loudly -- it would fail
as a connection error in every graph test at once, which is a confusing way to
learn about a default.

**A URL must not merely be preferred; the SQLite path must go untouched.**
"Postgres wins" and "Postgres is used and SQLite is also opened" are
indistinguishable from a passing resume test, and the second would leave a
stray database file on a deployment whose whole point is not having one.

**Postgres must get the same serializer as SQLite.** This is the property with
teeth. `tests/graph/test_checkpoint_serde.py` establishes that an unregistered
type does not raise on deserialization -- it comes back as a plain `dict`, so
`BreakingChange.source` stops being required and `RiskFactor.evidence`'s
`min_length=1` stops holding. A Postgres backend wired without the allowlist
would therefore drop every honesty invariant this project encodes in its types
*on the exact path Postgres exists to make durable*, and no test that only
checks "the run resumed" would notice.

**Why the Postgres branch is exercised against a stub.** Opening it for real
needs a live database, which rule 22 forbids here. What is asserted below is
the branch decision and the arguments -- which is this module's own logic --
while the claim that the real backend survives a real process restart is
measured by `probes/probe_postgres_checkpointer.py`, whose result is recorded
in ADR-001's verification record. Neither stands in for the other: the probe
cannot run in CI without a database, and a stub cannot prove durability.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from upgradepilot.graph import checkpointer as checkpointer_module
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.models.inputs import DependencySpec

POSTGRES_URL = "postgresql://user:pw@/db?host=/cloudsql/project:region:instance"


class _NotOurModel(BaseModel):
    """A Pydantic model from outside `upgradepilot.models`.

    Stands in for any third-party model that could reach state. It must
    degrade to a `dict`, because that is what proves the allowlist is real.
    """

    value: str


class _StubPostgresSaver(BaseCheckpointSaver[str]):
    """Stands in for `AsyncPostgresSaver` without opening a connection.

    Subclasses `BaseCheckpointSaver` rather than being a bare object, because
    that is what `open_checkpointer` is annotated to yield -- so the identity
    assertions below type-check on their own rather than needing an ignore.
    Every inherited method is left unimplemented: these tests exercise
    selection and construction, and a stub that pretended to store
    checkpoints would invite a durability assertion it cannot support.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setup_calls = 0

    async def setup(self) -> None:
        self.setup_calls += 1


class _Recorder:
    def __init__(self) -> None:
        self.saver = _StubPostgresSaver()
        self.conn_string: str | None = None
        self.serde: Any = None
        self.calls = 0

    @asynccontextmanager
    async def from_conn_string(
        self, conn_string: str, *, serde: Any = None, **_: Any
    ) -> AsyncIterator[_StubPostgresSaver]:
        self.calls += 1
        self.conn_string = conn_string
        self.serde = serde
        yield self.saver


@pytest.fixture
def postgres(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(checkpointer_module, "AsyncPostgresSaver", recorder)
    return recorder


async def test_without_a_url_the_backend_is_sqlite(tmp_path: Path) -> None:
    async with open_checkpointer(tmp_path / "c.db") as saver:
        assert isinstance(saver, AsyncSqliteSaver)
    assert (tmp_path / "c.db").exists()


async def test_a_url_selects_postgres(tmp_path: Path, postgres: _Recorder) -> None:
    async with open_checkpointer(tmp_path / "c.db", url=POSTGRES_URL) as saver:
        assert saver is postgres.saver
    assert postgres.calls == 1
    assert postgres.conn_string == POSTGRES_URL


async def test_a_url_leaves_the_sqlite_path_untouched(tmp_path: Path, postgres: _Recorder) -> None:
    """`checkpoint_db` keeps its default, so the URL branch must ignore it.

    Not a tidiness assertion. A deployment that selected Postgres and still
    created a SQLite file would be writing run state to an in-memory
    filesystem alongside the durable copy -- the failure ADR-002 D2 exists to
    remove, present and invisible.
    """
    path = tmp_path / "c.db"
    async with open_checkpointer(path, url=POSTGRES_URL):
        pass
    assert not path.exists()


async def test_setup_runs_on_the_postgres_backend(tmp_path: Path, postgres: _Recorder) -> None:
    """The schema is created by opening, as it is for SQLite.

    A backend whose tables are only created by a separate migration step
    would come up healthy and fail on the first checkpoint write.
    """
    async with open_checkpointer(tmp_path / "c.db", url=POSTGRES_URL):
        pass
    assert postgres.saver.setup_calls == 1


async def test_postgres_is_given_the_project_type_allowlist(
    tmp_path: Path, postgres: _Recorder
) -> None:
    """The honesty invariants must survive a resume on *this* backend too.

    Asserted through the serializer's behaviour rather than by reading its
    allowlist attribute, for the same reason
    `test_our_serializer_actually_restricts_rather_than_allowing_everything`
    does: the attribute is private, and the failure mode being guarded
    against is not "the list is short" but "an unregistered type comes back
    as a dict". Round-tripping one of ours and one of theirs proves both
    halves -- that our types are registered, and that the serializer is not
    still in permissive mode, where the first assertion would pass for the
    wrong reason.
    """
    async with open_checkpointer(tmp_path / "c.db", url=POSTGRES_URL):
        pass

    assert isinstance(postgres.serde, JsonPlusSerializer)
    ours = DependencySpec(name="pydantic", current_version="1.10.13", target_version="2.13.4")
    restored = postgres.serde.loads_typed(
        postgres.serde.dumps_typed({"ours": ours, "theirs": _NotOurModel(value="x")})
    )

    assert isinstance(restored["ours"], DependencySpec), (
        "the Postgres backend got a serializer that does not know this project's types, "
        "so a resumed run would carry dicts where it expects models"
    )
    assert isinstance(restored["theirs"], dict), (
        "an unregistered type survived, so the allowlist is still permissive and the "
        "assertion above passed for the wrong reason"
    )
