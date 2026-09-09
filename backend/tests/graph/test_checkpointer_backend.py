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

**The pool must check connections on checkout.** The Postgres branch used to
call `AsyncPostgresSaver.from_conn_string`, which holds one connection for the
process lifetime; against a provider that suspends when idle, that connection
is dead by the next resume. `check=` is pinned here rather than only in the
live test because the live test needs a database and this one does not, so
this is the assertion that runs in CI on every push.

**Why the Postgres branch is exercised against a stub.** Opening it for real
needs a live database, which rule 22 forbids here. What is asserted below is
the branch decision and the arguments -- which is this module's own logic --
while the claim that the real backend survives a real process restart is
measured by `probes/probe_postgres_checkpointer.py`, and the claim that it
survives the database hanging up is measured by
`test_checkpointer_survives_an_idle_disconnect.py`. None stands in for
another: the probe and the live test both need a database, and a stub cannot
prove durability.

**These stubs must intercept the pool, not just the saver.** When they did not,
this module stopped being hermetic without failing -- `pool.open(wait=True)`
dialled the fake DSN for real and the run hung for two minutes instead of
erroring. A stub that leaves a live connection attempt in place is a rule 22
violation that presents as slowness.
"""

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from upgradepilot.graph import checkpointer as checkpointer_module
from upgradepilot.graph.checkpointer import open_checkpointer
from upgradepilot.models.inputs import DependencySpec

POSTGRES_URL = "postgresql://user:pw@db.example.invalid:5432/upgradepilot?sslmode=require"
"""Never dialled -- the pool is stubbed -- but shaped like the real thing.

A TCP DSN with TLS rather than the Cloud SQL unix-socket form this constant
first held, because ADR-002 D2's provider is now Neon and a socket path would
misdescribe the deployment for the next reader.
"""


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


class _StubPool:
    """Stands in for `AsyncConnectionPool`, recording how it was configured.

    `open(wait=True)` is a no-op here. In the real thing it is what makes a
    bad DSN fail at startup, and it is also what made this module hang for two
    minutes when the stub did not exist -- see the module docstring.
    """

    def __init__(self, conninfo: str, **kwargs: Any) -> None:
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.open_calls = 0

    async def __aenter__(self) -> "_StubPool":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def open(self, wait: bool = False) -> None:
        self.open_calls += 1
        self.wait = wait


class _Recorder:
    """Captures both halves of the Postgres branch: the pool and the saver."""

    def __init__(self) -> None:
        self.saver = _StubPostgresSaver()
        self.pool: _StubPool | None = None
        self.conn_string: str | None = None
        self.serde: Any = None
        self.calls = 0

    def pool_factory(self, conninfo: str, **kwargs: Any) -> _StubPool:
        self.conn_string = conninfo
        self.pool = _StubPool(conninfo, **kwargs)
        return self.pool

    def saver_factory(self, conn: Any, *, serde: Any = None, **_: Any) -> _StubPostgresSaver:
        self.calls += 1
        self.given_conn = conn
        self.serde = serde
        return self.saver


class _StubPoolClass:
    """Callable stand-in for the `AsyncConnectionPool` *class*.

    A class rather than a `SimpleNamespace`, because Python resolves
    `__call__` on the type and never on the instance -- a namespace with a
    `__call__` attribute raises "object is not callable". It carries the real
    `check_connection` so the assertion that the code passes it is comparing
    against psycopg's function and not against something this file invented.
    """

    # `staticmethod`, or instance access binds it and the assertion compares a
    # bound method of this stub against psycopg's plain function. The code
    # under test reads the attribute off whatever `AsyncConnectionPool` names,
    # which here is an *instance*, so the descriptor protocol applies.
    check_connection = staticmethod(AsyncConnectionPool.check_connection)

    def __init__(self, recorder: "_Recorder") -> None:
        self._recorder = recorder

    def __call__(self, conninfo: str, **kwargs: Any) -> _StubPool:
        return self._recorder.pool_factory(conninfo, **kwargs)


@pytest.fixture
def postgres(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(checkpointer_module, "AsyncConnectionPool", _StubPoolClass(recorder))
    monkeypatch.setattr(checkpointer_module, "AsyncPostgresSaver", recorder.saver_factory)
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


async def test_the_pool_checks_a_connection_before_handing_it_over(
    tmp_path: Path, postgres: _Recorder
) -> None:
    """The assertion that would have caught the idle-disconnect bug in CI.

    `test_checkpointer_survives_an_idle_disconnect.py` proves the behaviour
    against a real server, and skips without one -- so on a machine or a CI
    job with no database, this is the only thing standing between a refactor
    and a deployment that 500s on the first resume after an idle night.

    `check_connection` specifically, and it is compared against psycopg's own
    function rather than merely asserted non-None: `check=` accepts any
    callable, and a no-op one would satisfy a truthiness check while
    reinstating the bug exactly. Measured -- with the pool present and this
    argument removed, the live test fails and logs `discarding closed
    connection`, the pool noticing only once the dead connection came back.
    """
    async with open_checkpointer(tmp_path / "c.db", url=POSTGRES_URL):
        pass

    assert postgres.pool is not None
    assert postgres.pool.kwargs["check"] is AsyncConnectionPool.check_connection


async def test_the_saver_is_given_the_pool_rather_than_a_bare_connection(
    tmp_path: Path, postgres: _Recorder
) -> None:
    """Building a pool and then not using it would pass every test above.

    `AsyncPostgresSaver` accepts either a connection or a pool, and the
    checking behaviour lives entirely on the pool -- so a saver handed a
    connection checked out once at startup is back to holding one connection
    for the process lifetime, with a pool sitting beside it proving nothing.
    """
    async with open_checkpointer(tmp_path / "c.db", url=POSTGRES_URL):
        pass

    assert postgres.given_conn is postgres.pool


async def test_the_pool_opens_eagerly_and_waits(tmp_path: Path, postgres: _Recorder) -> None:
    """A bad DSN must fail at startup, not on the first checkpoint write.

    `open=False` then `open(wait=True)` is the pair that does it. Without the
    wait, `open_checkpointer` returns successfully against an unreachable
    database and the failure surfaces later, on a request, as a 500 that
    looks like a bug in the run rather than in the configuration.
    """
    async with open_checkpointer(tmp_path / "c.db", url=POSTGRES_URL):
        pass

    assert postgres.pool is not None
    assert postgres.pool.kwargs["open"] is False, "the pool connected before open(wait=True)"
    assert postgres.pool.open_calls == 1
    assert postgres.pool.wait is True


async def test_the_connection_kwargs_the_saver_depends_on_are_set(
    tmp_path: Path, postgres: _Recorder
) -> None:
    """`from_conn_string` used to supply these; we replaced it, so we owe them.

    `autocommit` is the one with teeth. The saver's non-pipeline path opens a
    bare cursor with no surrounding `transaction()`, so without autocommit
    psycopg starts an implicit transaction that nothing ever commits and every
    checkpoint write is discarded when the connection closes -- a paused run
    that reports itself saved and is not.
    """
    async with open_checkpointer(tmp_path / "c.db", url=POSTGRES_URL):
        pass

    assert postgres.pool is not None
    assert postgres.pool.kwargs["kwargs"]["autocommit"] is True
    assert postgres.pool.kwargs["kwargs"]["row_factory"] is dict_row


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
