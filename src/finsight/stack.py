"""Assemble the runtime system (indexes, retriever, pipeline) from configuration.

One place wires the pieces together so the CLI, the API and the evaluation harness all build
*identical* systems. Heavy parts (embedding model, reranker) load lazily and are shared between
retrievers, which is what makes an ablation over many presets affordable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType

from finsight.agent.orchestrator import ResearchAgent
from finsight.agent.router import ToolRouterAgent
from finsight.agent.tools import AgentContext
from finsight.config.settings import RetrievalSettings, Settings
from finsight.config.universe import Universe, load_universe
from finsight.core.exceptions import ConfigError
from finsight.core.schemas import Answer, Chunk
from finsight.generation.llm import AnthropicLLM, LLMClient
from finsight.generation.offline import ExtractiveLLM
from finsight.generation.pipeline import RagPipeline
from finsight.indexing.builder import IndexManifest, verify_manifest
from finsight.indexing.embeddings import Embedder, make_embedder
from finsight.indexing.sparse_index import SparseIndex
from finsight.indexing.vector_store import QdrantVectorStore
from finsight.ingestion.xbrl.store import FactStore
from finsight.processing.pipeline import read_chunks
from finsight.retrieval.dense import DenseRetriever
from finsight.retrieval.query_analysis import QueryAnalyzer
from finsight.retrieval.rerank import Reranker, make_reranker
from finsight.retrieval.retriever import Retriever
from finsight.retrieval.sparse import SparseRetriever


@dataclass
class Stack:
    settings: Settings
    universe: Universe
    catalogue: dict[str, Chunk]
    manifest: IndexManifest
    embedder: Embedder
    store: QdrantVectorStore
    sparse: SparseIndex
    analyzer: QueryAnalyzer
    _rerankers: dict[str, Reranker] = field(default_factory=dict)

    def retriever(self, rs: RetrievalSettings | None = None) -> Retriever:
        rs = rs or self.settings.retrieval
        reranker: Reranker | None = None
        if rs.rerank:
            if rs.rerank_model not in self._rerankers:
                built = make_reranker(rs)
                assert built is not None
                self._rerankers[rs.rerank_model] = built
            reranker = self._rerankers[rs.rerank_model]
        return Retriever(
            self.catalogue,
            rs,
            dense=DenseRetriever(self.embedder, self.store),
            sparse=SparseRetriever(self.sparse),
            reranker=reranker,
            analyzer=self.analyzer,
        )

    def pipeline(self, llm: LLMClient, rs: RetrievalSettings | None = None) -> RagPipeline:
        return RagPipeline(self.retriever(rs), llm, self.settings.llm, analyzer=self.analyzer)

    def system(
        self, kind: str, llm: LLMClient, facts: FactStore | None = None
    ) -> Callable[[str], Answer]:
        """A ``question -> Answer`` callable for ``rag``, ``router`` or ``agent`` (see ADR-0009)."""
        if kind == "rag":
            return self.pipeline(llm).answer
        if facts is None:
            raise ConfigError(f"system {kind!r} needs the fact store; run `finsight ingest` first")
        ctx = AgentContext(facts=facts, retriever=self.retriever(), universe=self.universe)
        if kind == "router":
            offline = self.pipeline(ExtractiveLLM())  # text questions fall back to extractive RAG
            return ToolRouterAgent(ctx, offline.answer).answer
        if kind == "agent":
            return ResearchAgent(llm, ctx, self.settings.llm).answer
        raise ConfigError(f"unknown --system {kind!r}; choose rag, router or agent")

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> Stack:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def open_vector_store(settings: Settings, dim: int) -> QdrantVectorStore:
    """Embedded local storage by default; a Qdrant server when ``qdrant_url`` is configured."""
    if settings.qdrant_url:
        return QdrantVectorStore(dim=dim, url=settings.qdrant_url)
    return QdrantVectorStore(dim=dim, path=settings.index_dir / "qdrant")


def load_stack(settings: Settings) -> Stack:
    chunks_path = settings.processed_dir / "chunks.parquet"
    if not chunks_path.is_file():
        raise ConfigError("no chunks.parquet - run `finsight process` first")
    catalogue = {c.id: c for c in read_chunks(chunks_path)}
    manifest = IndexManifest.read(settings.index_dir)
    embedder = make_embedder(settings.embedding)
    verify_manifest(manifest, embedder)  # refuses to mix vectors from different models
    return Stack(
        settings=settings,
        universe=load_universe(settings.configs_dir / "universe.yaml"),
        catalogue=catalogue,
        manifest=manifest,
        embedder=embedder,
        store=open_vector_store(settings, embedder.dim),
        sparse=SparseIndex.load(settings.index_dir / "bm25", catalogue),
        analyzer=QueryAnalyzer(load_universe(settings.configs_dir / "universe.yaml")),
    )


def make_llm(kind: str, settings: Settings) -> LLMClient:
    if kind == "extractive":
        return ExtractiveLLM()
    if kind == "claude":
        return AnthropicLLM(settings.llm)
    raise ConfigError(f"unknown --llm {kind!r}; choose 'extractive' or 'claude'")
