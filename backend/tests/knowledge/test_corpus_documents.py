"""The corpus document schema and its frontmatter parser.

Spec §7.2 fixes the frontmatter shape. What this file pins is not the YAML
dialect but the *refusals*: a corpus document is the origin of every
`SourceRef` the product prints, so a document that parses into the wrong
shape produces a citation that resolves to the wrong claim. Every test below
that expects a raise is there because the silent alternative is a lie with a
source attached.
"""

from pathlib import Path

import pytest

from upgradepilot.models.enums import Severity, SourceType
from upgradepilot.services.knowledge.corpus import (
    CorpusDocumentError,
    load_corpus,
    parse_document,
)

FRONTMATTER = """\
---
source_id: pydantic-v2-migration#validator-renamed
title: "@validator replaced by @field_validator"
source_type: migration_guide
dependency: pydantic
from_version: "1.x"
to_version: "2.0"
to_version_major: 2
affected_symbols: [validator, root_validator]
severity: high
url_or_reference: https://docs.pydantic.dev/latest/migration/
created_at: 2026-08-24
tags: [validators, api-rename]
---

Pydantic v2 renames the validator decorators.

The v1 form is `@validator("field")`; the v2 form is `@field_validator("field")`.
"""


def document(**overrides: str) -> str:
    """The sample above with frontmatter lines replaced or removed.

    A value of `None` is spelled as the sentinel `"<<drop>>"` so the helper
    stays `str`-typed under strict mypy.
    """
    front, _, body = FRONTMATTER.partition("\n---\n\n")
    lines = front.splitlines()[1:]
    kept: list[str] = []
    for line in lines:
        key = line.split(":", 1)[0]
        if key in overrides:
            replacement = overrides[key]
            if replacement != "<<drop>>":
                kept.append(f"{key}: {replacement}")
        else:
            kept.append(line)
    for key, value in overrides.items():
        if value != "<<drop>>" and not any(line.startswith(f"{key}:") for line in kept):
            kept.append(f"{key}: {value}")
    return "---\n" + "\n".join(kept) + "\n---\n\n" + body


# -- the happy path --------------------------------------------------------


def test_frontmatter_and_body_are_separated() -> None:
    doc = parse_document(FRONTMATTER, path="corpus/pydantic/validator-renamed.md")

    assert doc.source_id == "pydantic-v2-migration#validator-renamed"
    assert doc.title == "@validator replaced by @field_validator"
    assert doc.source_type is SourceType.MIGRATION_GUIDE
    assert doc.severity is Severity.HIGH
    assert doc.affected_symbols == ("validator", "root_validator")
    assert doc.tags == ("validators", "api-rename")
    assert doc.to_version_major == 2
    assert doc.body.startswith("Pydantic v2 renames the validator decorators.")
    assert "---" not in doc.body


def test_the_originating_path_is_carried_on_the_document() -> None:
    """Every downstream failure -- a bad chunk, a symbol that never joins --
    is diagnosed by opening the file that produced it. Losing the path here
    means the only way back to the author's mistake is a grep for the
    `source_id`."""
    doc = parse_document(FRONTMATTER, path="corpus/pydantic/validator-renamed.md")
    assert doc.path == "corpus/pydantic/validator-renamed.md"


def test_the_dependency_name_is_canonicalized() -> None:
    """`dependency` is an exact-match Chroma filter joined against
    `DependencySpec.name`, which is canonicalized on the way in (PEP 503).
    If only one side is normalised the filter silently matches nothing and
    retrieval returns an empty set that reads as "no known breaking
    changes"."""
    doc = parse_document(document(dependency="Pydantic_Core"), path="p.md")
    assert doc.dependency == "pydantic-core"


# -- refusals: the document must not parse into a plausible wrong shape ----


def test_a_document_with_no_frontmatter_is_refused() -> None:
    with pytest.raises(CorpusDocumentError, match="frontmatter"):
        parse_document("Just a body, no metadata at all.\n", path="p.md")


def test_an_unterminated_frontmatter_block_is_refused() -> None:
    """The opening fence alone would otherwise consume the whole file as
    YAML and produce a document with no body."""
    with pytest.raises(CorpusDocumentError, match="frontmatter"):
        parse_document("---\nsource_id: x\ntitle: y\n", path="p.md")


def test_a_missing_required_field_is_refused_and_the_path_is_named() -> None:
    with pytest.raises(CorpusDocumentError) as excinfo:
        parse_document(document(severity="<<drop>>"), path="corpus/pydantic/bad.md")
    assert "corpus/pydantic/bad.md" in str(excinfo.value)
    assert "severity" in str(excinfo.value)


def test_an_empty_body_is_refused() -> None:
    """Frontmatter alone is metadata about nothing. It would embed as an
    empty chunk, rank arbitrarily, and cite a document that says nothing."""
    front, _, _ = FRONTMATTER.partition("\n---\n\n")
    with pytest.raises(CorpusDocumentError, match="body"):
        parse_document(front + "\n---\n\n   \n", path="p.md")


def test_an_unquoted_version_that_yaml_reads_as_a_number_is_refused() -> None:
    """The classic YAML footgun, and this corpus is *about* version numbers.

    Unquoted `to_version: 2.0` is a float to any YAML loader, and `"2.0"`,
    `2.0` and `"2.00"` are three different strings for one version. Refused
    rather than coerced: `str(2.0)` is `'2.0'` here and `'2'` for
    `to_version: 2`, so coercion would silently rewrite the author's version
    string in a way that depends on how they typed it.
    """
    with pytest.raises(CorpusDocumentError) as excinfo:
        parse_document(document(to_version="2.0"), path="p.md")
    assert "to_version" in str(excinfo.value)


def test_an_unknown_frontmatter_key_is_refused() -> None:
    """A typo'd key -- `affected_symbol`, `serverity` -- is otherwise a
    silent no-op: the field keeps its default and the document is indexed
    with metadata the author believes they set."""
    with pytest.raises(CorpusDocumentError, match="affected_symbol"):
        parse_document(document(affected_symbol="[validator]"), path="p.md")


def test_a_source_id_that_is_not_unique_across_the_corpus_is_refused(tmp_path: Path) -> None:
    """`source_id` is the citation key. Two documents sharing one makes the
    citation ambiguous, and in Chroma the second `add` silently overwrites
    the first."""
    (tmp_path / "a.md").write_text(FRONTMATTER, encoding="utf-8")
    (tmp_path / "b.md").write_text(document(title='"A different title"'), encoding="utf-8")

    with pytest.raises(CorpusDocumentError) as excinfo:
        load_corpus(tmp_path)
    assert "pydantic-v2-migration#validator-renamed" in str(excinfo.value)
    assert "a.md" in str(excinfo.value)
    assert "b.md" in str(excinfo.value)


# -- affected_symbols: required exactly where it can be honoured ------------


def test_a_breaking_change_document_with_no_affected_symbols_is_refused() -> None:
    """Retrieval joins on `affected_symbols` with `$contains`. A
    migration_guide that names none can never be reached by that join, so
    the symbol it documents would read as uncovered -- the exact shape of
    under-report §7.3's deterministic gate exists to catch."""
    with pytest.raises(CorpusDocumentError, match="affected_symbols"):
        parse_document(document(affected_symbols="[]"), path="p.md")


def test_internal_guidance_may_name_no_symbols() -> None:
    """The other direction. An ADR or an upgrade report is prose about how a
    team should approach the migration; it is reached semantically, not by
    symbol. Forcing a symbol onto it would mean inventing one, and an
    invented symbol is a false join."""
    doc = parse_document(
        document(source_type="adr", affected_symbols="[]"),
        path="corpus/internal/adr-001.md",
    )
    assert doc.affected_symbols == ()
    assert doc.source_type is SourceType.ADR


# -- loading a directory ---------------------------------------------------


def test_load_corpus_reads_every_markdown_file(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text(FRONTMATTER, encoding="utf-8")
    nested = tmp_path / "internal"
    nested.mkdir()
    (nested / "two.md").write_text(
        document(source_id="internal#adr-1", source_type="adr", affected_symbols="[]"),
        encoding="utf-8",
    )
    (tmp_path / "README.txt").write_text("not a corpus document", encoding="utf-8")

    docs = load_corpus(tmp_path)

    assert tuple(d.source_id for d in docs) == (
        "internal#adr-1",
        "pydantic-v2-migration#validator-renamed",
    ), "documents must load in a deterministic order"
    assert docs[0].path == "internal/two.md"


def test_load_corpus_refuses_an_empty_directory(tmp_path: Path) -> None:
    """An empty corpus ingests without error and then answers every query
    with nothing, which reads as "no breaking changes found"."""
    with pytest.raises(CorpusDocumentError, match="no corpus documents"):
        load_corpus(tmp_path)


def test_frontmatter_may_not_set_a_field_the_parser_owns() -> None:
    """`body` and `path` are supplied by the parser, not by the author.

    Merged blindly, a frontmatter `body:` would be silently replaced by the
    real body -- or, depending on merge order, would silently replace it,
    indexing a document whose text is one line of metadata. Refused so the
    author is told the key is not theirs to set.
    """
    with pytest.raises(CorpusDocumentError, match="body"):
        parse_document(document(body='"a body from the frontmatter"'), path="p.md")


def test_a_major_version_that_disagrees_with_the_version_string_is_refused() -> None:
    """`to_version` is prose the reader sees; `to_version_major` is the
    scalar Chroma filters on. When they disagree, a query narrowed to major
    3 returns a document whose own text says it is about 2.0 -- retrieval
    and citation contradicting each other, with nothing in the output to
    show it.
    """
    with pytest.raises(CorpusDocumentError, match="to_version_major"):
        parse_document(document(to_version_major="3"), path="p.md")


def test_a_prerelease_target_version_still_agrees_with_its_major() -> None:
    """The check reads the leading component, so ordinary release spellings
    -- `2.0`, `2.9.0`, `2.0b1` -- are not collateral damage."""
    doc = parse_document(document(to_version='"2.0b1"'), path="p.md")
    assert doc.to_version == "2.0b1"
    assert doc.to_version_major == 2
