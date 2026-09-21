"""Hashing embedder (the hermetic fake) and the embedder factory."""

from __future__ import annotations

import numpy as np
import pytest

from finsight.config.settings import EmbeddingSettings
from finsight.indexing.embeddings import HashingEmbedder, make_embedder

pytestmark = pytest.mark.unit


def test_vectors_are_unit_norm_float32_with_declared_dim() -> None:
    emb = HashingEmbedder(128)
    vecs = emb.embed_documents(["supply chain risk", "net sales grew"])
    assert vecs.shape == (2, 128) == (2, emb.dim)
    assert vecs.dtype == np.float32
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)


def test_embeddings_are_deterministic_across_instances() -> None:
    a = HashingEmbedder().embed_documents(["net sales $391,035 million"])
    b = HashingEmbedder().embed_documents(["net sales $391,035 million"])
    assert np.array_equal(a, b)


def test_lexical_overlap_drives_similarity() -> None:
    emb = HashingEmbedder(512)
    q = emb.embed_query("supply chain disruption risk")
    docs = emb.embed_documents(
        [
            "supply chain disruption could harm our business",
            "supply of components risk",
            "the board approved a quarterly dividend",
        ]
    )
    sims = docs @ q
    assert sims[0] > sims[1] > sims[2]


def test_query_and_document_paths_agree_for_identical_text() -> None:
    emb = HashingEmbedder()
    assert np.allclose(emb.embed_query("hello world"), emb.embed_documents(["hello world"])[0])


def test_empty_batch_and_empty_text_are_safe() -> None:
    emb = HashingEmbedder(32)
    assert emb.embed_documents([]).shape == (0, 32)
    assert np.isfinite(emb.embed_query("")).all()  # zero vector normalised without NaNs


def test_batch_callback_reports_progress() -> None:
    seen: list[int] = []
    HashingEmbedder().embed_documents(["a b", "c d"], on_batch=seen.append)
    assert seen == [2]


def test_factory_returns_hashing_backend_without_touching_the_network() -> None:
    emb = make_embedder(EmbeddingSettings(backend="hashing"))
    assert emb.name == "hashing-256"
