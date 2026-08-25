"""Reading the authored corpus off disk.

One document per breaking change, Markdown with YAML frontmatter (spec
§7.2). This module is the boundary between what an author wrote and what the
rest of the system may assume: everything past `load_corpus` is a validated
`CorpusDocument`, and every way a document can be wrong is refused here with
the file named.

`CorpusDocumentError` is a plain `ValueError`, not an `UpgradePilotError`.
The corpus is authored and ingested by this project (spec §12 assumption 2),
so a malformed document is a build-time fault in our own content, not a
runtime condition a user can provoke or an API can report. Giving it an
`ErrorCode` would put a code in the taxonomy that no endpoint can ever
return. Ingestion does not catch it -- it stops, which is the point.
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from upgradepilot.models.knowledge import CorpusDocument

CORPUS_ROOT = Path(__file__).resolve().parents[4] / "corpus"
"""The corpus that ships with the backend.

Located relative to this file rather than to the process working directory,
so ingestion finds it whichever directory it is invoked from. The configured
`Settings.corpus_dir` is what an operator overrides to point at a different
corpus; this is the default that override falls back to.
"""

FRONTMATTER_FENCE = "---"

PARSER_OWNED_FIELDS = frozenset({"body", "path"})
"""Fields the parser supplies. Frontmatter may not set them.

Merged blindly, a frontmatter `body:` either silently replaces the real body
-- indexing a document whose text is one line of metadata -- or is silently
replaced by it, depending on merge order. Neither is visible downstream.
"""


class CorpusDocumentError(ValueError):
    """A corpus document that cannot be trusted to cite itself correctly."""


def _split_frontmatter(text: str, *, path: str) -> tuple[str, str]:
    """Return `(frontmatter, body)`, or refuse.

    Both refusals below are cases where a lenient parser produces something
    that looks like a document. Treating a file with no fence as all-body
    yields a document with no metadata and therefore no citation; treating an
    unterminated fence as all-frontmatter yields metadata about an empty
    body. Each would then be caught downstream as a different, less
    diagnosable problem.
    """
    opening = f"{FRONTMATTER_FENCE}\n"
    if not text.startswith(opening):
        raise CorpusDocumentError(
            f"{path}: expected YAML frontmatter -- the file must begin with a "
            f"{FRONTMATTER_FENCE!r} line"
        )
    rest = text[len(opening) :]
    closing = f"\n{FRONTMATTER_FENCE}\n"
    end = rest.find(closing)
    if end == -1:
        raise CorpusDocumentError(
            f"{path}: the frontmatter block is not terminated -- expected a closing "
            f"{FRONTMATTER_FENCE!r} line before the body"
        )
    return rest[:end], rest[end + len(closing) :]


def _render(error: ValidationError, *, path: str) -> str:
    """Flatten pydantic's errors into one line per field, naming the file.

    The field name is what an author fixes, so it leads. `str(ValidationError)`
    alone omits the path, and the path is the only thing that says which of
    forty documents to open.
    """
    parts = []
    for detail in error.errors():
        location = ".".join(str(item) for item in detail["loc"]) or "<document>"
        parts.append(f"{location}: {detail['msg']}")
    return f"{path}: " + "; ".join(parts)


def parse_document(text: str, *, path: str) -> CorpusDocument:
    """Parse one corpus document, or refuse with `path` named."""
    frontmatter, body = _split_frontmatter(text, path=path)

    try:
        loaded = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise CorpusDocumentError(f"{path}: frontmatter is not valid YAML: {exc}") from exc

    if not isinstance(loaded, dict):
        raise CorpusDocumentError(
            f"{path}: frontmatter must be a YAML mapping of field to value, got "
            f"{type(loaded).__name__}"
        )

    overreach = sorted(PARSER_OWNED_FIELDS & set(loaded))
    if overreach:
        raise CorpusDocumentError(
            f"{path}: frontmatter must not set {overreach} -- "
            "the parser supplies those from the file itself"
        )

    try:
        return CorpusDocument.model_validate({**loaded, "body": body, "path": path})
    except ValidationError as exc:
        raise CorpusDocumentError(_render(exc, path=path)) from exc


def load_corpus(root: Path) -> tuple[CorpusDocument, ...]:
    """Read every `.md` file under `root`, in a deterministic order.

    Sorted by relative path rather than left in filesystem order: ingestion
    assigns `chunk_id`s positionally, so an unstable order would make the
    citation keys differ between two ingests of identical content, and a
    checked-in golden set would pass or fail depending on the machine.

    A file whose name begins with an underscore is not a corpus document --
    `_README.md` is the case that prompted the rule. A general convention
    rather than a match on the name `README.md`: a filename special-case
    would silently drop a real document that happened to be called that, and
    the underscore is visible in a directory listing, so nothing is skipped
    invisibly. The rule cannot hide an empty corpus, because the refusal
    below is checked after it.

    Two refusals, both of which are silent corruption otherwise:

    - An empty corpus. It ingests without error and then answers every query
      with nothing, which the report reads as "no known breaking changes"
      rather than "nothing was loaded".
    - A duplicate `source_id`. It is the citation key, so two documents
      sharing one makes every citation to it ambiguous -- and in Chroma the
      second `add` silently overwrites the first, so the corpus quietly holds
      fewer documents than the author wrote.
    """
    paths = sorted(
        (path for path in root.rglob("*.md") if not path.name.startswith("_")),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    documents = tuple(
        parse_document(
            path.read_text(encoding="utf-8"),
            path=path.relative_to(root).as_posix(),
        )
        for path in paths
    )

    if not documents:
        raise CorpusDocumentError(
            f"no corpus documents found under {root} -- an empty corpus answers every "
            "query with nothing, which reads as 'no known breaking changes'"
        )

    seen: dict[str, str] = {}
    for document in documents:
        first = seen.get(document.source_id)
        if first is not None:
            raise CorpusDocumentError(
                f"duplicate source_id {document.source_id!r} in {first} and "
                f"{document.path} -- source_id is the citation key and must be unique"
            )
        seen[document.source_id] = document.path

    return documents
