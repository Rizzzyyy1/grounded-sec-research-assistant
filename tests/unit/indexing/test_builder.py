"""Index builder: resumability, manifest, compatibility checks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from finsight.core.exceptions import IndexingError
from finsight.indexing.builder import (
    IndexManifest,
    build_indexes,
    corpus_hash,
    verify_manifest,
)
from finsight.indexing.embeddings import HashingEmbedder
from finsight.indexing.sparse_index import SparseIndex
from finsight.indexing.vector_store import InMemoryVectorStore

from tests.unit.conftest import ChunkFactory  # isort: skip

pytestmark = pytest.mark.unit


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__(64)
        self.embedded = 0

    def embed_documents(self, texts, on_batch=None):  # type: ignore[no-untyped-def]
        self.embedded += len(texts)
        return super().embed_documents(texts, on_batch)


def chunks(make_chunk: ChunkFactory, n: int = 10) -> list:  # type: ignore[type-arg]
    return [make_chunk(f"chunk number {i} about risk", tokens=10 + i) for i in range(n)]


def test_build_indexes_everything_and_writes_manifest_and_bm25(
    tmp_path: Path, make_chunk: ChunkFactory
) -> None:
    corpus = chunks(make_chunk)
    store, sparse, emb = InMemoryVectorStore(), SparseIndex(), CountingEmbedder()
    manifest = build_indexes(corpus, emb, store, sparse, index_dir=tmp_path, batch_size=4)
    assert store.count() == len(sparse) == manifest.n_chunks == 10
    assert emb.embedded == 10
    assert manifest.embedding_model == "hashing-64" and manifest.embedding_dim == 64
    assert IndexManifest.read(tmp_path) == manifest
    assert (tmp_path / "bm25" / "ids.json").is_file()


def test_rerun_embeds_nothing_new(tmp_path: Path, make_chunk: ChunkFactory) -> None:
    corpus = chunks(make_chunk)
    store, emb = InMemoryVectorStore(), CountingEmbedder()
    build_indexes(corpus, emb, store, SparseIndex(), index_dir=tmp_path)
    build_indexes(corpus, emb, store, SparseIndex(), index_dir=tmp_path)
    assert emb.embedded == 10  # the second run found everything already indexed


def test_interrupted_build_resumes_where_it_stopped(
    tmp_path: Path, make_chunk: ChunkFactory
) -> None:
    corpus = chunks(make_chunk)
    store, emb = InMemoryVectorStore(), CountingEmbedder()
    # simulate a crash: only the first 6 chunks made it into the store
    store.upsert(corpus[:6], emb.embed_documents([c.indexed_text for c in corpus[:6]]))
    emb.embedded = 0
    build_indexes(corpus, emb, store, SparseIndex(), index_dir=tmp_path, batch_size=2)
    assert emb.embedded == 4
    assert store.count() == 10


def test_pending_chunks_are_embedded_shortest_first(
    tmp_path: Path, make_chunk: ChunkFactory
) -> None:
    corpus = [
        make_chunk("long " * 50, tokens=50),
        make_chunk("tiny", tokens=1),
        make_chunk("mid " * 5, tokens=5),
    ]
    order: list[int] = []

    class Spy(HashingEmbedder):
        def embed_documents(self, texts, on_batch=None):  # type: ignore[no-untyped-def]
            order.extend(len(t) for t in texts)
            return super().embed_documents(texts, on_batch)

    build_indexes(
        corpus, Spy(32), InMemoryVectorStore(), SparseIndex(), index_dir=tmp_path, batch_size=1
    )
    assert order == sorted(order)  # minimal padding cost on the real model


def test_progress_messages_are_emitted(tmp_path: Path, make_chunk: ChunkFactory) -> None:
    seen: list[str] = []
    build_indexes(chunks(make_chunk, 5), HashingEmbedder(32), InMemoryVectorStore(), SparseIndex(),
                  index_dir=tmp_path, batch_size=2, on_progress=seen.append)  # fmt: skip
    assert any("BM25" in m for m in seen)
    assert any("embedded 5/5" in m for m in seen)


def test_corpus_hash_is_order_independent_and_content_sensitive(make_chunk: ChunkFactory) -> None:
    a = chunks(make_chunk, 5)
    assert corpus_hash(a) == corpus_hash(list(reversed(a)))
    assert corpus_hash(a) != corpus_hash(a[:-1])


def test_manifest_mismatch_is_refused_with_actionable_message(
    tmp_path: Path, make_chunk: ChunkFactory
) -> None:
    manifest = build_indexes(chunks(make_chunk), HashingEmbedder(64), InMemoryVectorStore(),
                             SparseIndex(), index_dir=tmp_path)  # fmt: skip
    verify_manifest(manifest, HashingEmbedder(64))  # same model: fine
    with pytest.raises(IndexingError, match="rebuild the index"):
        verify_manifest(manifest, HashingEmbedder(128))


def test_reading_a_missing_manifest_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(IndexingError, match="finsight index"):
        IndexManifest.read(tmp_path)


def test_manifest_records_chunking_config(tmp_path: Path, make_chunk: ChunkFactory) -> None:
    m = build_indexes(chunks(make_chunk, 2), HashingEmbedder(16), InMemoryVectorStore(), SparseIndex(),
                      index_dir=tmp_path, chunking={"target_tokens": 400})  # fmt: skip
    assert m.chunking == {"target_tokens": 400}
    assert np.isfinite(len(m.corpus_hash))
