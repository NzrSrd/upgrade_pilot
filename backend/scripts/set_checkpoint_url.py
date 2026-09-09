"""Assemble `UP_CHECKPOINT_URL` from its parts and write it into `backend/.env`.

Exists because the value is a five-part string and the provider's dashboard
presents the parts separately, so hand-assembly went wrong twice -- both times
producing a bare hostname, which `Settings` correctly refused, taking the whole
test suite down with it.

**The password is read with `getpass`**, so it is never echoed to the terminal,
never lands in shell history, and never reaches a scrollback that might be
pasted somewhere. Nothing here prints the assembled URL either; it goes
straight into the file. Run it from a real terminal -- `getpass` needs a tty.

Verifies before writing. A DSN that does not connect is worse than none: the
absence of one is a working SQLite deployment, while a bad one is a refusal to
boot, and finding that out at deploy time rather than here is the whole reason
this checks first.
"""

from __future__ import annotations

import getpass
import re
import sys
import urllib.parse as urlparse
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
SETTING = "UP_CHECKPOINT_URL"

DEFAULT_ROLE = "neondb_owner"
DEFAULT_DATABASE = "neondb"


def _ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def _direct_host(host: str) -> str:
    """Strip Neon's `-pooler` suffix if it is present.

    The pooled endpoint is the one the dashboard offers first and it is the
    wrong one here: `AsyncPostgresSaver` opens connections with
    `prepare_threshold=0`, so every statement it issues is a named server-side
    prepared statement, and transaction-pooled connections do not carry those
    across checkouts. Corrected rather than rejected, because the two hostnames
    differ by one token and refusing would just move the edit back to a human.
    """
    direct = re.sub(r"-pooler(?=\.)", "", host, count=1)
    if direct != host:
        print(f"  note: using the direct endpoint {direct} (dropped '-pooler')")
    return direct


def _verify(url: str) -> bool:
    """Connect for real, and confirm the role may create tables.

    DDL rights are checked because `AsyncPostgresSaver.setup()` and
    `RunOwnership.setup()` both run at startup and both need them, so a
    read-only role would pass a bare connection check and fail on boot.
    """
    try:
        import psycopg
    except ImportError:
        print("  psycopg is not importable; skipping verification", file=sys.stderr)
        return True

    try:
        with psycopg.connect(url, connect_timeout=20, autocommit=True) as connection:
            version = connection.execute("SELECT version()").fetchone()
            connection.execute("CREATE TABLE IF NOT EXISTS up_write_probe (x int)")
            connection.execute("DROP TABLE up_write_probe")
    except Exception as error:  # noqa: BLE001 -- reported, not swallowed (rule 20)
        print(f"\n  FAILED to connect: {type(error).__name__}: {error}", file=sys.stderr)
        return False

    assert version is not None
    print(f"  connected: {str(version[0]).split(' (')[0]}")
    print("  the role can create and drop tables, which setup() needs")
    return True


def _stored_value() -> str | None:
    """The first live `UP_CHECKPOINT_URL=` value currently in the file."""
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{SETTING}="):
            return line.split("=", 1)[1].strip()
    return None


def _write(url: str) -> None:
    """Replace or append the setting, leaving every other line alone."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(f"{SETTING}=") and not replaced:
            out.append(f"{SETTING}={url}")
            replaced = True
        elif line.startswith(f"{SETTING}="):
            out.append(f"# superseded: {SETTING} appeared more than once")
        else:
            out.append(line)
    if not replaced:
        out.append(f"{SETTING}={url}")
    ENV_FILE.write_text("\n".join(out) + "\n")


def main() -> int:
    print(f"Writing {SETTING} into {ENV_FILE}\n")
    print("Paste the host from Neon's dashboard (with or without '-pooler'):")
    host = input("  host: ").strip()
    if not host:
        print("no host given", file=sys.stderr)
        return 1
    host = _direct_host(host)

    role = _ask("  role", DEFAULT_ROLE)
    database = _ask("  database", DEFAULT_DATABASE)
    password = getpass.getpass("  password (not shown): ")
    if not password:
        print("no password given", file=sys.stderr)
        return 1

    # quote_plus on both, because a generated password can contain @ : / ? #,
    # each of which changes where the parser thinks the host begins.
    url = (
        f"postgresql://{urlparse.quote_plus(role)}:{urlparse.quote_plus(password)}"
        f"@{host}/{database}?sslmode=require"
    )

    print("\nVerifying...")
    if not _verify(url):
        print("\nNothing was written. Check the host, role and password.", file=sys.stderr)
        return 1

    _write(url)

    # Read back rather than trusting the write. Measured: the first successful
    # run of this script reported "Written to ..." and the value was not there
    # a minute later -- an editor holding `.env` open saved a stale buffer over
    # it. A write-only script cannot tell that from success, and the symptom
    # appears much later as a deployment pointing at the wrong database.
    if _stored_value() != url:
        print(
            f"\nWROTE, THEN READ BACK SOMETHING ELSE from {ENV_FILE}.\n"
            "Something is overwriting the file -- most likely an editor with "
            "it open. Close it and run this again.",
            file=sys.stderr,
        )
        return 1

    print(f"\nWritten to {ENV_FILE} and read back intact.")
    print("The URL was not printed anywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
