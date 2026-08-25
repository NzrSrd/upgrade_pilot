"""The ChromaDB knowledge base: ingestion in, retrieval out.

The store's contract with the rest of the system is that **every result can
cite itself**. A `RetrievedChunk` carries its document's identifying metadata,
so building a `SourceRef` never needs a second round-trip and a chunk can
never reach the model as prose with no source attached (CLAUDE.md rule 1).

Facts about the pinned `chromadb==1.5.9` this module is built on, each
established by probe rather than assumed and pinned by
`tests/knowledge/test_chroma_contract.py`:

- List-valued metadata is stored and returned as a real list.
- `$contains` against a list is **exact-element**, which is what makes the
  symbol join safe: a filter for `Config` does not match `ConfigDict`.
- `$in` against a list silently returns nothing. Never use it for symbols.
- A single-clause `$and`/`$or` raises `ValueError`, so a where-builder must
  not wrap unconditionally -- see `_combine`.
- An **empty** list metadata value is rejected outright ("Expected metadata
  list value ... to be non-empty"), so a document naming no symbols omits the
  key rather than storing `[]` -- see `_metadata`.
"""

from copy import deepcopy
from pathlib import Path
from typing import Literal, cast

import chromadb
from chromadb.api.collection_configuration import (
    CreateCollectionConfiguration,
    CreateHNSWConfiguration,
)
from chromadb.api.models.Collection import Collection
from chromadb.api.types import Embeddable, EmbeddingFunction, Metadata, Where

from upgradepilot.models.enums import Severity, SourceType
from upgradepilot.models.errors import KnowledgeBaseUnavailableError
from upgradepilot.models.inputs import canonicalize_name
from upgradepilot.models.knowledge import CorpusDocument, IngestReport, RetrievedChunk
from upgradepilot.services.knowledge.chunking import chunk_document

COLLECTION_NAME = "upgradepilot-corpus"
"""Chroma requires 3-512 characters from `[a-zA-Z0-9._-]` (ADR-001)."""

CORPUS_CONFIGURATION = CreateCollectionConfiguration(hnsw=CreateHNSWConfiguration(space="cosine"))
"""The collection's settings. **Never passed to chroma directly** -- see
`_configuration()` below, which hands over a copy.

Cosine, chosen so `distance` has a fixed, interpretable range rather than
whatever magnitudes the embedding model happens to produce. `_relevance`
depends on that: cosine distance is `1 - similarity`, which is what makes the
mapping to a printable 0-1 relevance meaningful. Under Chroma's default L2 the
same arithmetic would produce a number with no interpretation at all.

Set through `configuration=` rather than the older `metadata={"hnsw:space": ...}`
spelling. Both were measured against the pinned chromadb 1.5.9 and both work;
this one is the typed form and is the one the collection reflects back."""

DEFAULT_LIMIT = 5
"""Matches the golden set's recall@5 so the number CI asserts is the number
the product actually retrieves with."""


def _configuration() -> CreateCollectionConfiguration:
    """A fresh copy of `CORPUS_CONFIGURATION` for one `create` call.

    Chroma **writes into** the configuration mapping it is given, inserting
    the embedding function under an `embedding_function` key. Handing it a
    module-level constant therefore makes that constant shared mutable state:
    whichever store opens first stamps its embedder into it, and every store
    opened afterwards is validated against that stale embedder rather than the
    one it was actually given.

    Found by running the suite with `--live`. The live embedding test opened a
    store with the real embedder and every offline test after it failed at
    fixture setup with an embedding-function conflict -- which looked like a
    test-isolation problem and was not. Outside the suite the same defect means
    a process opening a second collection gets the first one's embedder
    imposed on it, or fails outright.

    `deepcopy`, not `dict(...)`: the nested `hnsw` mapping would otherwise
    still be shared, and it is a mapping chroma also fills in with defaults.
    """
    return deepcopy(CORPUS_CONFIGURATION)


def _contains(field: str, value: str) -> Where:
    """`{field: {"$contains": value}}`, typed to chromadb's `Where` alias.

    The explicit annotation is not decoration: a plain nested dict literal
    infers as `dict[str, str]`, and `dict` is invariant, so it structurally
    matches no member of `Where`'s value union. Only spelling out the exact
    member type satisfies it.
    """
    condition: dict[Literal["$contains", "$not_contains"], str | int | float | bool] = {
        "$contains": value
    }
    return {field: condition}


def _in(field: str, values: list[str]) -> Where:
    condition: dict[Literal["$in", "$nin"], list[str | int | float | bool]] = {"$in": list(values)}
    return {field: condition}


def _combine(operator: Literal["$and", "$or"], clauses: list[Where]) -> Where | None:
    """Join clauses, never wrapping fewer than two.

    Chroma raises `ValueError` on a single-clause `$and`/`$or` (ADR-001), and
    the commonest query in this system -- dependency alone -- has exactly one
    clause. A builder that always wrapped would fail on the ordinary case
    while looking correct for the complicated ones.
    """
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    combined: dict[Literal["$and", "$or"], list[Where]] = {operator: clauses}
    return cast(Where, combined)


def _relevance(distance: float) -> float:
    """Map a cosine distance to a 0-1 relevance the report can print.

    Cosine distance is `1 - similarity`, so this is the similarity itself
    with negative similarity clamped to zero: identical text scores 1.0, text
    sharing nothing scores 0.0, and anti-correlated text scores 0.0 rather
    than a negative number no reader could interpret.

    `RetrievedChunk` keeps the raw `distance` alongside the result of this
    function on purpose. The mapping is a decision this project makes, and
    discarding its input would make the decision uncheckable.
    """
    return max(0.0, min(1.0, 1.0 - distance))


def _metadata(document: CorpusDocument, ordinal: int) -> Metadata:
    """Flatten a document's frontmatter into Chroma metadata for one chunk.

    `affected_symbols` and `tags` are omitted when empty rather than stored
    as `[]`: chroma 1.5.9 rejects an empty list metadata value outright. An
    omitted key simply never matches `$contains`, which is the correct
    behaviour for a document that names no symbols -- it is not reachable by
    the symbol join, and it was never meant to be.
    """
    metadata: dict[str, str | int | float | bool | list[str]] = {
        "source_id": document.source_id,
        "title": document.title,
        "source_type": document.source_type.value,
        "url_or_reference": document.url_or_reference,
        "dependency": document.dependency,
        "from_version": document.from_version,
        "to_version": document.to_version,
        "to_version_major": document.to_version_major,
        "severity": document.severity.value,
        "created_at": document.created_at.isoformat(),
        "path": document.path,
        "ordinal": ordinal,
    }
    if document.affected_symbols:
        metadata["affected_symbols"] = list(document.affected_symbols)
    if document.tags:
        metadata["tags"] = list(document.tags)
    return cast(Metadata, metadata)


def _string(metadata: Metadata, key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str):
        raise KnowledgeBaseUnavailableError(
            "The knowledge base returned a document that cannot be cited.",
            detail=f"chunk metadata key {key!r} is {type(value).__name__}, expected str",
        )
    return value


def _symbols(metadata: Metadata) -> tuple[str, ...]:
    value = metadata.get("affected_symbols")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise KnowledgeBaseUnavailableError(
            "The knowledge base returned a document that cannot be cited.",
            detail=f"affected_symbols is {type(value).__name__}, expected a list",
        )
    return tuple(str(item) for item in value)


class KnowledgeStore:
    """A persistent Chroma collection holding the authored corpus.

    The client is held; the *collection* is fetched per call. That is
    deliberate rather than wasteful: a collection dropped or a directory
    wiped between requests must surface as `KB_UNAVAILABLE` at the moment of
    use, and a cached handle would instead fail somewhere less diagnosable,
    or appear to work against a collection that no longer exists.
    """

    def __init__(
        self,
        client: chromadb.api.ClientAPI,
        *,
        embedding_function: EmbeddingFunction[Embeddable],
    ) -> None:
        self._client = client
        self._embedding_function = embedding_function

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        embedding_function: EmbeddingFunction[Embeddable],
    ) -> "KnowledgeStore":
        """Open (creating if needed) the store at `path`.

        Spec §7.3: Chroma unreachable becomes `AppError(KB_UNAVAILABLE)` and
        an empty context flagged `evidence_available: False`, which §8.1 turns
        into a hard confidence ceiling. That chain starts with a typed error,
        so a raw `ChromaError` escaping here would be handled -- if at all --
        as an internal fault and the ceiling would never engage. Absent
        evidence must not be able to produce a confident answer.
        """
        try:
            client = chromadb.PersistentClient(path=str(path))
        except chromadb.errors.ChromaError as exc:
            raise KnowledgeBaseUnavailableError(
                "The knowledge base could not be opened, so no documented "
                "breaking changes could be consulted.",
                detail=f"chromadb.PersistentClient(path={str(path)!r}): {exc}",
            ) from exc
        store = cls(client, embedding_function=embedding_function)
        store._collection(create=True)
        return store

    def _collection(self, *, create: bool = False) -> Collection:
        try:
            if create:
                return self._client.get_or_create_collection(
                    COLLECTION_NAME,
                    configuration=_configuration(),
                    embedding_function=self._embedding_function,
                )
            return self._client.get_collection(
                COLLECTION_NAME,
                embedding_function=self._embedding_function,
            )
        except chromadb.errors.ChromaError as exc:
            raise KnowledgeBaseUnavailableError(
                "The knowledge base is unavailable, so no documented breaking "
                "changes could be consulted.",
                detail=f"collection {COLLECTION_NAME!r}: {exc}",
            ) from exc

    def count(self) -> int:
        return int(self._collection().count())

    def chunk_ids(self, wanted: list[str]) -> frozenset[str]:
        """Which of `wanted` this collection actually holds.

        Spec 8.4's first check needs to know whether a citation still
        resolves, and the failure it guards against is quiet: a corpus
        re-ingest rewrites a document from three chunks to one, and a report
        generated before it now cites `#chunk-2`, which resolves to nothing.
        The citation still looks right, and the reader following it finds a
        real document with no such passage.

        `include=[]` deliberately: the check needs existence, not content, and
        asking for documents and metadata would pull the whole cited corpus
        into memory to answer a set-membership question.

        Returns what is *present* rather than what is missing, so a caller
        that passes an empty list gets an empty set rather than a claim about
        nothing.
        """
        if not wanted:
            return frozenset()
        try:
            result = self._collection().get(ids=list(wanted), include=[])
        except chromadb.errors.ChromaError as exc:
            raise KnowledgeBaseUnavailableError(
                "The knowledge base could not be queried to verify citations.",
                detail=f"get(ids=[{len(wanted)} ids]): {exc}",
            ) from exc
        return frozenset(result["ids"])

    def drop(self) -> None:
        """Delete the collection. Used by ingestion's rebuild and by tests."""
        try:
            self._client.delete_collection(COLLECTION_NAME)
        except chromadb.errors.ChromaError as exc:
            raise KnowledgeBaseUnavailableError(
                "The knowledge base could not be reset.",
                detail=f"delete_collection({COLLECTION_NAME!r}): {exc}",
            ) from exc

    def ingest(self, documents: tuple[CorpusDocument, ...]) -> IngestReport:
        """Chunk, embed and persist every document, then report what is there.

        `upsert`, not `add`: `chunk_id` is deterministic, so re-ingesting
        unchanged content lands on the same ids. With `add` those would
        either raise or duplicate, and duplicated chunks would let one
        document occupy several of the top-`n` slots -- degrading retrieval
        while every count still looked plausible.

        The returned counts are read back from the collection rather than
        taken from the input, so a write that silently did nothing cannot
        report success.
        """
        collection = self._collection(create=True)
        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[Metadata] = []

        for document in documents:
            for chunk in chunk_document(document):
                ids.append(chunk.chunk_id)
                # The title is embedded with the chunk, not stored in place
                # of it. A chunk that is mostly a code block has little
                # prose to match on, and its document's title is the one
                # sentence that says what the code is an example of. The
                # authored text is what `RetrievedChunk.text` returns, so
                # nothing the reader is shown was written by this line.
                texts.append(f"{document.title}\n\n{chunk.text}")
                metadatas.append({**_metadata(document, chunk.ordinal), "text": chunk.text})

        if ids:
            try:
                collection.upsert(ids=ids, documents=texts, metadatas=metadatas)
            except chromadb.errors.ChromaError as exc:
                raise KnowledgeBaseUnavailableError(
                    "The knowledge base could not be written to.",
                    detail=f"upsert of {len(ids)} chunks: {exc}",
                ) from exc

        return IngestReport(documents=len(documents), chunks=int(collection.count()))

    def search(
        self,
        query_text: str,
        *,
        dependency: str,
        to_version_major: int | None = None,
        source_types: tuple[SourceType, ...] = (),
        symbols: tuple[str, ...] = (),
        symbol_annotations: tuple[str, ...] = (),
        limit: int = DEFAULT_LIMIT,
    ) -> tuple[RetrievedChunk, ...]:
        """Filtered similarity search, annotated with the symbols it covers.

        `symbols` both **filters** (an `$or` of `$contains`, joined in the
        database per spec §7.2) and annotates. `symbol_annotations` only
        annotates: it is how a caller asks "which of these symbols does a
        semantically-retrieved chunk happen to cover?" without narrowing the
        search to documents that name them. The distinction matters because
        `matched_symbols` feeds §7.3's deterministic sufficiency gate, and a
        chunk that covers nothing must be able to say so.
        """
        clauses: list[Where] = [{"dependency": canonicalize_name(dependency)}]
        if to_version_major is not None:
            clauses.append({"to_version_major": to_version_major})
        if source_types:
            clauses.append(_in("source_type", [t.value for t in source_types]))
        if symbols:
            symbol_clause = _combine("$or", [_contains("affected_symbols", s) for s in symbols])
            if symbol_clause is not None:
                clauses.append(symbol_clause)

        where = _combine("$and", clauses)
        collection = self._collection()
        try:
            result = collection.query(
                query_texts=[query_text],
                n_results=max(1, limit),
                where=where,
                include=["metadatas", "distances"],
            )
        except chromadb.errors.ChromaError as exc:
            raise KnowledgeBaseUnavailableError(
                "The knowledge base could not be queried, so no documented "
                "breaking changes could be consulted.",
                detail=f"query(where={where!r}): {exc}",
            ) from exc

        return self._to_chunks(result, asked_about=(*symbols, *symbol_annotations), limit=limit)

    def _to_chunks(
        self,
        result: chromadb.QueryResult,
        *,
        asked_about: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievedChunk, ...]:
        """Build `RetrievedChunk`s, deduplicated by `chunk_id` and ordered as
        the store ranked them.

        Dedup is by `chunk_id` rather than by `source_id`: two chunks of one
        long document are two distinct pieces of evidence and both may be
        cited, but the same chunk returned twice would double-count in any
        coverage judgement built on the result.
        """
        ids = result["ids"][0] if result["ids"] else []
        metadatas_all = result.get("metadatas")
        distances_all = result.get("distances")
        metadatas = metadatas_all[0] if metadatas_all else []
        distances = distances_all[0] if distances_all else []

        wanted = {symbol for symbol in asked_about}
        chunks: list[RetrievedChunk] = []
        seen: set[str] = set()

        for chunk_id, metadata, distance in zip(ids, metadatas, distances, strict=True):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            covered = _symbols(metadata)
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    source_id=_string(metadata, "source_id"),
                    title=_string(metadata, "title"),
                    source_type=SourceType(_string(metadata, "source_type")),
                    url_or_reference=_string(metadata, "url_or_reference"),
                    text=_string(metadata, "text"),
                    dependency=_string(metadata, "dependency"),
                    to_version_major=int(cast(int, metadata["to_version_major"])),
                    severity=Severity(_string(metadata, "severity")),
                    affected_symbols=covered,
                    distance=max(0.0, float(distance)),
                    relevance=_relevance(float(distance)),
                    matched_symbols=tuple(s for s in covered if s in wanted),
                )
            )
            if len(chunks) >= limit:
                break

        return tuple(chunks)
