"""The `postgres` tests must refuse to run against the deployment's database.

`tests/api/test_run_ownership.py`'s cleanup fixture runs
`DROP TABLE run_owners` after every test. Pointed at the deployment, that
deletes the mapping from every `thread_id` to its owner, and ADR-002 D6's
guarantee does not fail loudly -- it evaporates. Every paused run becomes
unclaimed, `require_owner` then refuses its real owner, and the table cannot
be reconstructed because the information only ever existed there.

The near-miss that prompted this: verifying the Neon connection fix meant
setting `UP_TEST_POSTGRES_URL` to the production DSN, and the run left
checkpoint rows in the deployment's database. Nothing warned. That residue was
harmless; a run that reached the ownership tests would not have been.

Tested here rather than only implemented, because the guard's failure mode is
silence -- a comparison that stops matching still lets every test pass.
"""

import pytest

from tests.conftest import _dsn_identity, _the_same_database

NEON = "postgresql://neondb_owner:pw@ep-x.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require"
LOCAL = "postgresql://localhost:5432/upgradepilot_test"


def test_the_deployment_database_is_refused() -> None:
    assert _the_same_database(NEON, NEON) is True


def test_a_separate_local_database_is_allowed() -> None:
    """The ordinary developer arrangement must not be broken by this guard.

    A guard that refuses the normal case gets deleted, and then the dangerous
    case is unguarded again.
    """
    assert _the_same_database(LOCAL, NEON) is False


@pytest.mark.parametrize(
    ("spelling", "why"),
    [
        (
            "postgresql://other_role:different@ep-x.c-5.eu-central-1.aws.neon.tech/neondb",
            "a different role and password, and no sslmode -- still the same database",
        ),
        (
            "postgres://neondb_owner:pw@EP-X.c-5.eu-central-1.AWS.neon.tech/NeonDB",
            "the postgres:// scheme and mixed case -- hostnames and database names "
            "are matched case-insensitively",
        ),
        (
            "  postgresql://neondb_owner:pw@ep-x.c-5.eu-central-1.aws.neon.tech/neondb  ",
            "surrounding whitespace, which a copy-paste routinely carries",
        ),
    ],
)
def test_near_miss_spellings_of_the_same_database_are_still_refused(
    spelling: str, why: str
) -> None:
    """String equality would pass every one of these, which is the whole point.

    The guard exists to catch a copy-paste of the deployment DSN, and a
    copy-paste is exactly what arrives with a changed role, a dropped query
    parameter, or a stray space. Comparing DSNs literally would produce a
    guard that only catches the one case nobody makes.
    """
    assert _the_same_database(spelling, NEON) is True, why


def test_a_dsn_naming_no_host_and_no_database_is_not_treated_as_a_match() -> None:
    """Two unidentifiable DSNs must not compare equal to each other.

    `postgresql://` with nothing after it names no host and no database, and
    so does a bare unix-socket DSN. Comparing their empty identities would
    make them "the same database" as each other and refuse a legitimate local
    run -- a false positive that would get this guard removed.
    """
    assert _the_same_database("postgresql://", "postgresql://") is False


def test_identity_discards_credentials_and_keeps_host_and_database() -> None:
    """Pinned directly, because it is the property the comparison rests on.

    If `_dsn_identity` ever started including the password, every assertion
    above would still pass except the near-miss cases, and the guard would
    silently narrow to catching only byte-identical DSNs.
    """
    assert _dsn_identity(NEON) == ("ep-x.c-5.eu-central-1.aws.neon.tech", "neondb")


def test_no_configured_deployment_means_nothing_to_protect() -> None:
    """CI has no `UP_CHECKPOINT_URL`, and must not be refused for it."""
    assert _the_same_database(LOCAL, None) is False
