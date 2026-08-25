# The UpgradePilot corpus

One breaking change per document, Markdown with YAML frontmatter (spec §7.2).
`services/knowledge/corpus.py` is the only reader; every refusal it makes is
documented there and tested in `tests/knowledge/test_corpus_documents.py`.

## Two kinds of document, and what each one claims

`pydantic/` holds **primary sources**: descriptions of real, published
Pydantic v1 to v2 breaking changes. Their `url_or_reference` points at the
upstream migration guide, and their content must be checkable against it.
These are reached by the symbol join, so each names the symbols it is about.

`internal/` holds **authored engineering guidance** — ADRs and an upgrade
report — standing in for the internal documents a real team would have. They
are written by this project, not sourced from anywhere, and their
`url_or_reference` points at the corpus file itself rather than at an external
URL that does not exist. The upgrade report says so in its own first line: it
is a worked example, not a report of a production incident. These are reached
semantically rather than by symbol, which is why they are allowed to name no
`affected_symbols` at all.

Keeping the two apart matters because the product prints their citations side
by side. A reader must be able to tell which claims trace to Pydantic's own
documentation and which are this project's opinion.

## Adding a document

1. Frontmatter must carry every required field. `to_version_major` must agree
   with `to_version`, and `to_version: 2.0` must be **quoted** — unquoted it
   is a YAML float and the parser refuses it rather than rewriting what you
   typed.
2. `source_id` is the citation key and must be unique across the whole corpus.
3. A `migration_guide`, `changelog` or `compat_note` must name at least one
   `affected_symbol`; retrieval joins on that field, so one naming none can
   never be found by symbol.
4. Per CLAUDE.md rule 25, a new document requires a golden-set case in the
   same change — see `tests/knowledge/golden_set.py`.
