"""Phase 0 probe: ChromaDB persistence and metadata filtering.

Run: backend/.venv/bin/python probes/probe_chroma.py
"""

import hashlib
import tempfile
from pathlib import Path

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings


class DeterministicEmbedding(EmbeddingFunction[Documents]):
    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return [
            [hashlib.sha256(t.lower().encode()).digest()[i] / 255.0 for i in range(16)]
            for t in input
        ]

    @staticmethod
    def name() -> str:
        return "deterministic-probe-embedding"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "chroma")
        ef = DeterministicEmbedding()

        client = chromadb.PersistentClient(path=path)
        collection = client.get_or_create_collection("probe", embedding_function=ef)
        collection.add(
            ids=["a", "b"],
            documents=["validator renamed to field_validator", "Query API replaced by select"],
            metadatas=[
                {
                    "dependency": "pydantic",
                    "to_version_major": 2,
                    "affected_symbols": ["validator"],
                },
                {"dependency": "sqlalchemy", "to_version_major": 2, "affected_symbols": ["Query"]},
            ],
        )
        print(f"seeded                 : {collection.count()} documents")

        reopened = chromadb.PersistentClient(path=path).get_collection(
            "probe", embedding_function=ef
        )
        print(f"survives restart       : {reopened.count()} documents")

        filtered = reopened.query(
            query_texts=["validator"], n_results=2, where={"dependency": "pydantic"}
        )
        print(f"scalar filter result   : {filtered['ids'][0]}")

        try:
            reopened.add(
                ids=["c"],
                documents=["list metadata probe"],
                metadatas=[{"affected_symbols": ["validator", "root_validator"]}],
            )
            stored = reopened.get(ids=["c"])["metadatas"][0]["affected_symbols"]
            kind = type(stored).__name__
            print(f"list metadata          : ACCEPTED, round-trips as {kind} {stored}")
        except Exception as exc:  # noqa: BLE001 - probe reports the class deliberately
            print(f"list metadata          : REJECTED ({type(exc).__name__})")
            return

        exact = reopened.get(where={"affected_symbols": {"$contains": "validator"}})["ids"]
        partial = reopened.get(where={"affected_symbols": {"$contains": "valid"}})["ids"]
        in_op = reopened.get(where={"affected_symbols": {"$in": ["validator"]}})["ids"]
        print(f"$contains exact match  : {exact}")
        print(f"$contains partial      : {partial}  <- empty: exact-element, not substring")
        print(f"$in on list value      : {in_op}  <- empty: unusable, hence $contains")


if __name__ == "__main__":
    main()
