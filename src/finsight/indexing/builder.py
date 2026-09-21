"""Index builder: corpus -> BM25 index + vector index + a manifest.

* **Resumable.** Embedding is the slow step (tens of minutes on a laptop CPU). Chunks already in
  the vector store are skipped, and work proceeds in checkpointed batches, so an interrupted run
  simply continues where it stopped.
* **Manifest.** Records the embedding model, dimension, corpus hash and chunker settings. Loading
  an index verifies it against the current embedder, so vectors from two different models can
  never be mixed silently (they would return plausible-looking nonsense).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from finsight import __version__
from finsight.core.exceptions import IndexingError
from finsight.core.logging import get_logger
from finsight.core.schemas import Chunk
from finsight.indexing.embeddings import Embedder
from finsight.indexing.sparse_index import SparseIndex
from finsight.indexing.vector_store import VectorStore

log = get_logger(__name__)
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class IndexManifest:
    embedding_model: str
    embedding_dim: int
    n_chunks: int
    corpus_hash: str
    created_at: str
    finsight_version: str
    chunking: dict[str, object]

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MANIFEST_NAME).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def read(cls, directory: Path) -> IndexManifest:
        try:
            return cls(**json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise IndexingError(f"no index manifest in {directory}; run `finsight index`") from exc


def corpus_hash(chunks: Sequence[Chunk]) -> str:
    """Order-independent fingerprint of the corpus (chunk ids are content hashes)."""
    digest = hashlib.sha256()
    for chunk_id in sorted(c.id for c in chunks):
        digest.update(chunk_id.encode())
    return digest.hexdigest()[:16]


def verify_manifest(manifest: IndexManifest, embedder: Embedder) -> None:
    if manifest.embedding_model != embedder.name or manifest.embedding_dim != embedder.dim:
        raise IndexingError(
            f"index was built with {manifest.embedding_model} (dim {manifest.embedding_dim}) but "
            f"the current embedder is {embedder.name} (dim {embedder.dim}); rebuild the index or "
            "restore the matching embedding settings"
        )


def build_indexes(
    chunks: Sequence[Chunk],
    embedder: Embedder,
    store: VectorStore,
    sparse: SparseIndex,
    *,
    index_dir: Path,
    chunking: dict[str, object] | None = None,
    batch_size: int = 256,
    on_progress: Callable[[str], None] = lambda _m: None,
) -> IndexManifest:
    started = time.monotonic()
    on_progress(f"building BM25 index over {len(chunks):,} chunks")
    sparse.build(chunks)
    sparse.save(index_dir / "bm25")

    done = store.indexed_ids()
    pending = sorted((c for c in chunks if c.id not in done), key=lambda c: c.metadata.token_count)
    on_progress(
        f"embedding {len(pending):,} chunks with {embedder.name} ({len(done):,} already indexed)"
    )
    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        store.upsert(batch, embedder.embed_documents([c.indexed_text for c in batch]))
        embedded += len(batch)
        elapsed = time.monotonic() - started
        rate = embedded / elapsed if elapsed else 0.0
        remaining = (len(pending) - embedded) / rate / 60 if rate else 0.0
        on_progress(
            f"embedded {embedded:,}/{len(pending):,} ({rate:.1f}/s, ~{remaining:.0f} min left)"
        )

    manifest = IndexManifest(
        embedding_model=embedder.name,
        embedding_dim=embedder.dim,
        n_chunks=len(chunks),
        corpus_hash=corpus_hash(chunks),
        created_at=datetime.now(UTC).isoformat(),
        finsight_version=__version__,
        chunking=chunking or {},
    )
    manifest.write(index_dir)
    return manifest
