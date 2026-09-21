# ADR-0004: Qdrant as the vector store (embedded → server)
* **Status:** Accepted · **Date:** 2026-09-21

## Context
Retrieval must be filtered by ticker, fiscal year, form and Item, and those filters must be
applied *before* top-k (post-filtering can return zero results). We also want zero infrastructure
in development/CI and a realistic deployment story.

## Decision
Use **Qdrant**: embedded/local mode in dev and CI, the server container in `docker compose`.
Payload indexes on `ticker`, `fiscal_year`, `form`, `item`, `chunk_type`. Access sits behind a
`VectorStore` protocol; an in-memory implementation serves tests.

## Alternatives considered
* *FAISS* — fast, but no native metadata filtering or persistence story.
* *Chroma* — easy, weaker filtering semantics and production path in our experience.
* *pgvector* — excellent for production; needs Postgres in dev/CI. Reasonable future swap.
* *LanceDB* — attractive (hybrid built in) but less common in industry job descriptions.

## Consequences
+ Real pre-filtering; same API locally and in prod. − Embedded mode is single-process; concurrent
API workers need the server (documented in Phase 8).
