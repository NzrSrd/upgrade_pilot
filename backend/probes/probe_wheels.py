"""Phase 13.0: does every dependency resolve on linux/amd64 for CPython 3.14?

The question is not "does it install here". This is an arm64 Mac and Cloud Run
runs x86_64 only -- "Cloud Run specifically supports the Linux x86_64 ABI
format" -- so what matters is whether a *manylinux x86_64* wheel exists that
CPython 3.14 can load. Anything missing means a source build inside the image,
or no support at all, and either would change ADR-002's base-image decision.

Two checks, because ADR-001's verification record makes two separate claims:

  additions()  the dependencies ADR-002 adds, named explicitly, so the row
               about them can be re-derived without reading this file.
  installed()  every platform-specific distribution already in the venv --
               found by reading WHEEL tags rather than from a hand-kept list,
               so a new transitive cannot quietly escape the sweep.

Four wheel shapes load on linux/amd64 CPython 3.14:

  py3-none-any                      pure Python, loads anywhere
  cp37-abi3-manylinux_..._x86_64    stable ABI, any CPython >= 3.7
  cp314-cp314-manylinux_..._x86_64  built for 3.14 specifically
  py3-none-manylinux_..._x86_64     a native binary with no Python ABI at all

That fourth shape is the one worth spelling out, because the first version of
this probe missed it and reported two false failures. `ruff` and `sqlite-vec`
ship a Rust binary and a C extension, so they are not pure Python -- but they
expose no Python C-API either, so their wheels carry the `py3` tag beside a
*platform* tag. ADR-001 already records that shape, having been caught by it
once: "2 that are platform-locked despite a `py3` prefix (`ruff` and
`sqlite-vec`, both `py3-none-macosx_11_0_arm64`)".

The second error was subtler and would have been worse: the linux filter
matched the substring `linux`, which also matches `musllinux`. A musllinux-only
wheel does not load on ADR-002's Debian base, so that version of the probe
would have green-lit an image that fails at import. `manylinux` is required
explicitly below for that reason.

Run:
    ./.venv/bin/python probes/probe_wheels.py
"""

import json
import pathlib
import re
import sys
import urllib.request

PURE_TAGS = frozenset({"py3-none-any", "py2.py3-none-any", "py2-none-any"})

# ADR-002's additions. `clerk-backend-api` is D4's and is checked but not
# installed, so its transitives are listed with it rather than discovered.
ADDITIONS = {
    "langgraph-checkpoint-postgres": "D2 AsyncPostgresSaver",
    "psycopg": "D2 driver (pure; needs a system libpq)",
    "psycopg-binary": "D2 driver with libpq bundled",
    "psycopg-pool": "D2 connection pool",
    "aiosqlite": "13.1 -- imported directly, previously undeclared",
    "clerk-backend-api": "D4 session-token verification",
    "cryptography": "D4 transitive",
    "pyjwt": "D4 transitive",
}


def loads_on_linux_amd64_py314(filename: str) -> str | None:
    """Why this wheel would load on linux/amd64 CPython 3.14, or None."""
    if not filename.endswith(".whl"):
        return None
    if "-py3-none-any.whl" in filename or "-py2.py3-none-any.whl" in filename:
        return "pure python"
    # manylinux, never musllinux: ADR-002's base image is Debian, so glibc.
    if "manylinux" not in filename or "x86_64" not in filename:
        return None
    if "abi3" in filename:
        return "abi3"
    if "cp314" in filename:
        return "cp314"
    if "-py3-none-" in filename or "-py2.py3-none-" in filename:
        return "no python ABI"
    return None


def verdict(name: str) -> tuple[str, list[str], bool]:
    """Return (latest version, reasons it loads, whether a musl wheel exists)."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30) as r:
        data = json.load(r)
    latest = data["info"]["version"]
    files = data["releases"].get(latest, data["urls"])
    reasons = sorted({t for f in files if (t := loads_on_linux_amd64_py314(f["filename"]))})
    musl = any("musllinux" in f["filename"] and "x86_64" in f["filename"] for f in files)
    return latest, reasons, musl


def platform_specific_installed() -> dict[str, str]:
    """Installed distributions whose WHEEL tags are not purely py3-none-any."""
    roots = [pathlib.Path(p) for p in sys.path if p.endswith("site-packages")]
    found: dict[str, str] = {}
    for root in roots:
        for wheel in sorted(root.glob("*.dist-info/WHEEL")):
            tags = [t.strip() for t in re.findall(r"^Tag:\s*(.+)$", wheel.read_text(), re.M)]
            if not tags or all(t in PURE_TAGS for t in tags):
                continue
            stem = wheel.parent.name.removesuffix(".dist-info")
            dist, _, version = stem.rpartition("-")
            found[dist.replace("_", "-").lower()] = version
    return found


def report(title: str, names: dict[str, str]) -> list[str]:
    print(f"\n=== {title} ({len(names)}) ===")
    failures = []
    for name, note in sorted(names.items()):
        latest, reasons, musl = verdict(name)
        if reasons:
            print(f"  ok   {name:<32} {latest:<12} {','.join(reasons):<22} {note}")
        else:
            failures.append(name)
            why = "musllinux ONLY -- breaks a Debian base" if musl else "NO LINUX WHEEL"
            print(f"  FAIL {name:<32} {latest:<12} {why:<22} {note}")
    return failures


if __name__ == "__main__":
    bad = report("ADR-002 additions", ADDITIONS)
    installed = platform_specific_installed()
    bad += report(
        "platform-specific packages already installed",
        {n: f"installed {v}" for n, v in installed.items()},
    )
    print()
    if bad:
        sys.exit(f"FAIL -- no manylinux x86_64 wheel for CPython 3.14: {sorted(set(bad))}")
    print("PASS -- every package above loads on linux/amd64 CPython 3.14 from a manylinux wheel.")
