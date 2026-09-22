# Architecture Decision Records

Short, dated records of significant decisions: context, decision, alternatives, consequences.
Format: [MADR](https://adr.github.io/madr/)-lite. Status is one of *Proposed · Accepted ·
Superseded by ADR-x*. Decisions are revisited when evaluation produces contrary evidence.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-hybrid-retrieval-rrf.md) | Hybrid retrieval (dense + BM25) fused with RRF, then rerank | Accepted |
| [0003](0003-numbers-from-xbrl-tools.md) | Numbers come from XBRL via tools, never from the LLM | Accepted |
| [0004](0004-qdrant-vector-store.md) | Qdrant (embedded → server) as vector store | Accepted |
| [0005](0005-fastembed-onnx.md) | FastEmbed (ONNX) for embeddings and reranking | Accepted |
| [0006](0006-llm-provider-and-roles.md) | Claude via official SDK; model configured per role | Accepted |
| [0007](0007-duckdb-fact-store.md) | DuckDB + Parquet as the structured fact store | Accepted |
| [0008](0008-evaluation-first.md) | Evaluation-first: components must earn default status | Accepted |
| [0009](0009-deterministic-tool-router.md) | A deterministic tool router as the offline, no-LLM system | Accepted |
| [0010](0010-pin-cik-for-successor-registrants.md) | Pin the CIK for successor registrants; zero filings is a failure | Accepted |
| [0011](0011-local-free-llm-provider.md) | A local, zero-cost LLM provider (Ollama) alongside Claude | Accepted |
| [0012](0012-explicit-provider-selection-at-serve-time.md) | Explicit, serve-time LLM provider selection for the API and UI | Accepted |
