"""The golden retrieval set: query in, expected `source_id` out.

Spec §7.2 asks for "RAG evaluation documented" as an executable test rather
than a paragraph, and this is it. One case per corpus document, which also
makes CLAUDE.md rule 25 -- a new corpus document requires a golden-set case in
the same change -- something a test can enforce instead of something a
reviewer has to remember.

**What the floors measure, stated plainly.** The offline embedding is lexical
(see `fake_embedding.py`), so these numbers score the retrieval *pipeline* --
filters, the symbol join, dedup, ordering, the distance-to-relevance mapping
-- under a bag-of-words similarity. They are a regression barrier: a change
that breaks ranking shows up here. They are **not** a measurement of
`text-embedding-3-small`, and nobody should quote them as evidence about
semantic retrieval quality.

Queries are phrased the way a retrieval planner would phrase them: containing
the symbol names, because §7.3's `plan_retrieval` generates queries from the
*actual symbol inventory*. Phrasing them without those names would be testing
a query shape this system never issues.
"""

from typing import NamedTuple


class GoldenCase(NamedTuple):
    query: str
    expected_source_id: str


GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        "the @validator decorator is deprecated, what replaces it",
        "pydantic-v2-migration#validator-renamed",
    ),
    GoldenCase(
        "root_validator whole model validation replacement in v2",
        "pydantic-v2-migration#root-validator-renamed",
    ),
    GoldenCase(
        "the nested class Config no longer configures the model",
        "pydantic-v2-migration#config-class-replaced",
    ),
    GoldenCase(
        "calling dict and json and copy on a model instance",
        "pydantic-v2-migration#model-methods-renamed",
    ),
    GoldenCase(
        "parse_obj and parse_raw classmethods for building a model",
        "pydantic-v2-migration#parsing-methods-renamed",
    ),
    GoldenCase(
        "generating a json schema, the schema method is gone",
        "pydantic-v2-migration#schema-methods-renamed",
    ),
    GoldenCase(
        "an Optional annotated field without a default is now required",
        "pydantic-v2-migration#optional-no-longer-implies-default",
    ),
    GoldenCase(
        "orm_mode and from_orm for reading a database row",
        "pydantic-v2-migration#orm-mode-renamed",
    ),
    GoldenCase(
        "importing BaseSettings fails after the upgrade",
        "pydantic-v2-migration#basesettings-moved",
    ),
    GoldenCase(
        "min_items and max_items and unique_items on a list Field",
        "pydantic-v2-migration#field-constraint-renames",
    ),
    GoldenCase(
        "update_forward_refs on a self referencing model",
        "pydantic-v2-migration#update-forward-refs-renamed",
    ),
    GoldenCase(
        "iterating __fields__ to introspect a model's fields",
        "pydantic-v2-migration#fields-attribute-renamed",
    ),
    GoldenCase(
        "a custom type using __get_validators__ stopped validating",
        "pydantic-v2-migration#custom-types-core-schema",
    ),
    GoldenCase(
        "each_item and always and allow_reuse arguments on a validator",
        "pydantic-v2-migration#validator-arguments-removed",
    ),
    GoldenCase(
        "using Extra forbid to reject unexpected input fields",
        "pydantic-v2-migration#extra-config-values",
    ),
    GoldenCase(
        "keeping some models on the old version while upgrading",
        "pydantic-v2-migration#v1-compatibility-namespace",
    ),
    GoldenCase(
        "should we migrate every module at once or incrementally",
        "internal#adr-incremental-migration",
    ),
    GoldenCase(
        "is there a codemod, and which changes does it not handle",
        "internal#adr-codemod-then-review",
    ),
    GoldenCase(
        "what actually broke when a service was upgraded",
        "internal#upgrade-report-billing-service",
    ),
)


def rank_of(expected_source_id: str, retrieved_source_ids: list[str]) -> int | None:
    """1-based rank of the first chunk from the expected document, or None.

    Ranked by *document*, not by chunk: a document split into three chunks
    would otherwise be scored as though the two that did not come first were
    misses, which measures the chunker rather than retrieval.
    """
    for position, source_id in enumerate(retrieved_source_ids, start=1):
        if source_id == expected_source_id:
            return position
    return None


def recall_at_k(ranks: list[int | None], k: int) -> float:
    """Fraction of cases whose expected document appeared in the top `k`."""
    if not ranks:
        return 0.0
    return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)


def mean_reciprocal_rank(ranks: list[int | None]) -> float:
    """Mean of 1/rank, counting a miss as 0.

    Rewards ranking the right document *first* rather than merely somewhere in
    the window, which recall@5 cannot distinguish. Both are asserted because a
    change that pushes every correct answer from rank 1 to rank 5 leaves
    recall untouched.
    """
    if not ranks:
        return 0.0
    return sum(0.0 if rank is None else 1.0 / rank for rank in ranks) / len(ranks)
