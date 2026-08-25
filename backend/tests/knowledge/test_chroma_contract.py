"""Locks the ChromaDB facts spec §7.2 depends on.

1. A PersistentClient survives process restart (simulated by a new client
   over the same directory).
2. Scalar metadata filters work.
3. List-valued metadata is accepted and round-trips as a real list — the
   brief assumed this was rejected; it is not, for the pinned
   `chromadb==1.5.9`. Symbol matching is therefore a `where`-clause using
   `$contains` (exact-element match), not a post-retrieval Python re-rank
   over a delimited string.
"""

import hashlib
from pathlib import Path
from typing import Literal

import chromadb
import numpy as np
import numpy.typing as npt
from chromadb.api import ClientAPI
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings, Metadata, Where

DIM = 16


class DeterministicEmbedding(EmbeddingFunction[Documents]):
    """Hash-based embeddings: stable, offline, and good enough to rank exact repeats first."""

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        vectors: list[npt.NDArray[np.int32 | np.float32]] = []
        for text in input:
            digest = hashlib.sha256(text.lower().encode()).digest()
            vectors.append(np.array([digest[i] / 255.0 for i in range(DIM)], dtype=np.float32))
        return vectors

    @staticmethod
    def name() -> str:
        return "deterministic-test-embedding"


def fake_embedding_function() -> DeterministicEmbedding:
    """Shared by every knowledge-base test from Phase 3 onward."""
    return DeterministicEmbedding()


def _contains(field: str, symbol: str) -> Where:
    """Build a `{field: {"$contains": symbol}}` where-clause typed to chromadb's `Where` alias.

    A plain nested dict literal (`{"$contains": symbol}`) infers as
    `dict[str, str]`, which does not structurally match any member of
    `Where`'s value union -- `dict` is invariant, so even a dict literal
    typed with the exact right `Literal` key still needs the exact right
    value-type union alongside it. Verified: the plain-literal and the
    exact-Literal-key-only forms both fail; only matching the full member
    type below satisfies it.
    """
    condition: dict[Literal["$contains", "$not_contains"], str | int | float | bool] = {
        "$contains": symbol
    }
    return {field: condition}


def _in(field: str, values: list[str]) -> Where:
    condition: dict[Literal["$in", "$nin"], list[str | int | float | bool]] = {"$in": list(values)}
    return {field: condition}


DOCS: list[tuple[str, str, Metadata]] = [
    (
        "pydantic-v2#validator",
        "@validator was replaced by @field_validator in Pydantic v2.",
        {
            "dependency": "pydantic",
            "to_version_major": 2,
            "source_type": "migration_guide",
            "affected_symbols": ["validator", "root_validator"],
        },
    ),
    (
        "pydantic-v2#config",
        "class Config was replaced by model_config = ConfigDict(...).",
        {
            "dependency": "pydantic",
            "to_version_major": 2,
            "source_type": "migration_guide",
            "affected_symbols": ["Config"],
        },
    ),
    (
        "sqlalchemy-2#select",
        "Legacy Query API gives way to select() in SQLAlchemy 2.0.",
        {
            "dependency": "sqlalchemy",
            "to_version_major": 2,
            "source_type": "changelog",
            "affected_symbols": ["Query"],
        },
    ),
]


def _seed(client: ClientAPI) -> None:
    collection = client.get_or_create_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )
    collection.add(
        ids=[d[0] for d in DOCS],
        documents=[d[1] for d in DOCS],
        metadatas=[d[2] for d in DOCS],
    )


def test_persistent_client_survives_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "chroma")
    _seed(chromadb.PersistentClient(path=path))

    reopened = chromadb.PersistentClient(path=path)
    # chromadb's own default embedding function is typed `EmbeddingFunction[Documents]` too
    # (see `chromadb/api/types.py`'s `DefaultEmbeddingFunction`, assigned to this exact
    # parameter upstream with the same `# type: ignore`) -- `ClientAPI.get_collection`'s
    # declared parameter type is the contravariant `EmbeddingFunction[Embeddable]`, which no
    # `Documents`-only embedding function can satisfy structurally, only in practice.
    collection = reopened.get_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )
    assert collection.count() == 3


def test_scalar_metadata_filter_narrows_results(tmp_path: Path) -> None:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    _seed(client)
    collection = client.get_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )

    result = collection.query(
        query_texts=["validator renamed"],
        n_results=3,
        where={"dependency": "pydantic"},
    )
    returned_ids = result["ids"][0]

    assert returned_ids, "filter returned nothing"
    assert all(i.startswith("pydantic-v2#") for i in returned_ids)
    assert "sqlalchemy-2#select" not in returned_ids


def test_source_metadata_round_trips(tmp_path: Path) -> None:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    _seed(client)
    collection = client.get_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )

    result = collection.get(ids=["pydantic-v2#validator"])
    metadatas = result["metadatas"]
    assert metadatas is not None
    metadata = metadatas[0]

    assert metadata["source_type"] == "migration_guide"
    assert metadata["to_version_major"] == 2
    assert metadata["affected_symbols"] == ["validator", "root_validator"]


def test_list_valued_metadata_is_accepted_and_round_trips(tmp_path: Path) -> None:
    """chromadb 1.5.9 stores list metadata and returns a real list.

    The brief assumed this was rejected. It is not - which is why symbol
    filtering is a where-clause rather than a post-retrieval Python pass.
    """
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    _seed(client)
    collection = client.get_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )

    metadatas = collection.get(ids=["pydantic-v2#validator"])["metadatas"]
    assert metadatas is not None
    metadata = metadatas[0]

    assert isinstance(metadata["affected_symbols"], list)
    assert metadata["affected_symbols"] == ["validator", "root_validator"]


def test_symbol_filter_matches_whole_elements_not_substrings(tmp_path: Path) -> None:
    """$contains is exact-element. This is the guard against false evidence.

    If it ever became substring-based, a filter for `Config` would match a
    document about `ConfigDict` and the report would cite a breaking change
    the repository does not actually use.
    """
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )
    collection.add(
        ids=["exact", "longer"],
        documents=["validator renamed", "ConfigDict introduced"],
        metadatas=[
            {"affected_symbols": ["validator"]},
            {"affected_symbols": ["ConfigDict"]},
        ],
    )

    def ids_for(symbol: str) -> list[str]:
        return collection.get(where=_contains("affected_symbols", symbol))["ids"]

    assert ids_for("validator") == ["exact"]
    assert ids_for("ConfigDict") == ["longer"]
    assert ids_for("valid") == [], "partial symbol must not match"
    assert ids_for("Config") == [], "Config must not match ConfigDict"


def test_multiple_symbols_filter_with_or_of_contains(tmp_path: Path) -> None:
    """The AST finds several symbols at once; this is how they are queried."""
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    _seed(client)
    collection = client.get_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )

    result = collection.get(
        where={
            "$or": [
                _contains("affected_symbols", "validator"),
                _contains("affected_symbols", "Config"),
            ]
        }
    )

    assert sorted(result["ids"]) == ["pydantic-v2#config", "pydantic-v2#validator"]


def test_in_operator_does_not_work_on_list_metadata(tmp_path: Path) -> None:
    """Documents why $contains is used and $in is not.

    $in against a list-valued field returns nothing rather than erroring, so
    using it would silently retrieve zero evidence - the worst failure mode
    available. Locked here so nobody 'simplifies' the filter back to $in.
    """
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    _seed(client)
    collection = client.get_collection(
        "migrations",
        embedding_function=fake_embedding_function(),  # type: ignore[arg-type]
    )

    result = collection.get(where=_in("affected_symbols", ["validator"]))

    assert result["ids"] == []
