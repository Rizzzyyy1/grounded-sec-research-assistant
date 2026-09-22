# FinSight — Architecture

Diagrams render natively on GitHub (Mermaid). Rationale for each choice: [DESIGN](DESIGN.md)
and [`adr/`](adr/).

## 1. System context

```mermaid
flowchart LR
    user([Analyst]) --> ui[Streamlit UI]
    user --> cli[CLI]
    ui --> api[FastAPI service]
    cli --> core
    api --> core

    subgraph core [FinSight core]
        direction TB
        rag[RAG pipeline]
        agent[Research agent]
        analytics[Analytics]
    end

    core -->|messages + tools| claude[(Anthropic API<br/>Claude)]
    core --> stores[(Local stores<br/>Qdrant · BM25 · DuckDB)]
    ingest[Ingestion jobs] -->|fair-access, 8 req/s| sec[(SEC EDGAR<br/>filings + XBRL)]
    ingest --> stores
```

## 2. Two planes

### 2.1 Offline plane — build the knowledge base

```mermaid
flowchart LR
    subgraph text [Text path]
        direction LR
        a1[EDGAR client] --> a2[Raw HTML<br/>+ manifest sha256]
        a2 --> a3[HTML parser<br/>blocks]
        a3 --> a4[Section splitter<br/>Items 1, 1A, 7, 8 ...]
        a4 --> a5[Chunker<br/>400 tok, 15% overlap<br/>tables standalone]
        a5 --> a6[Enrichment<br/>contextual header]
        a6 --> a7[Embedder<br/>bge-small ONNX]
        a6 --> a8[BM25 index]
        a7 --> a9[(Qdrant<br/>payload indexes)]
    end
    subgraph num [Numbers path]
        direction LR
        b1[EDGAR companyfacts JSON] --> b2[Facts parser<br/>dedupe restatements<br/>fiscal alignment]
        b2 --> b3[Metric resolver<br/>tag fallbacks]
        b3 --> b4[(DuckDB<br/>fact table)]
    end
```

### 2.2 Online plane — answer a question

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant G as Guardrails
    participant Q as Query analysis
    participant R as Retriever
    participant T as Tools (XBRL, ratios)
    participant L as Claude
    participant V as Validators

    U->>G: question
    G-->>U: decline (out of scope)
    G->>Q: in-scope question
    Q->>Q: tickers, years, form, item, QueryType
    Q->>R: query + metadata filters
    R->>R: dense ‖ BM25 → RRF fuse → rerank → diversify
    R-->>L: cited context [S1..Sn]
    loop up to N agent steps
        L->>T: tool_use (metric / ratio / compare)
        T-->>L: tool_result (values + formula + sources)
    end
    L-->>V: draft answer with [S#]
    V->>V: citations resolve? numbers match tool/source?
    V-->>U: Answer (text, citations, tool trace, usage, latency)
```

The single-shot **RAG baseline** is this flow without the tool loop. The **agent** adds the loop.
Both return the same `Answer` type, so the evaluation harness scores them interchangeably.

## 3. Package layout and dependency rules

```
src/finsight/
├── config/        settings (env) · universe & preset loaders (YAML)
├── core/          schemas · exceptions · logging · telemetry           ← imports nothing internal
├── ingestion/     edgar/ · xbrl/ · market/ · pipeline                  ← core, config
├── processing/    html_parser · sections · tables · chunking · enrichment
├── indexing/      embeddings · vector_store · sparse_index · builder
├── retrieval/     query_analysis · dense · sparse · hybrid · rerank · retriever
├── generation/    llm (Claude) · ollama (free, local) · offline (extractive) · prompts · context ·
│                  citations · guardrails · pipeline
├── analytics/     ratios · dupont · trends · peers · risk_diff · sentiment   ← pure functions
├── agent/         tools · orchestrator · trace
├── evaluation/    datasets · metrics/ · judges · runner · ablation · stats · report
├── api/           FastAPI app, routers, schemas, middleware
├── ui/            Streamlit app, pages, components
└── cli.py         Typer entrypoint
```

**Dependency direction** (a layer may import only from layers to its left/above):

```mermaid
flowchart TB
    core --> ingestion
    core --> analytics
    ingestion --> processing
    processing --> indexing
    indexing --> retrieval
    retrieval --> generation
    analytics --> agent
    generation --> agent
    retrieval --> agent
    generation --> evaluation
    agent --> evaluation
    generation --> api
    agent --> api
    api --> ui
```

Rules, enforced by `make arch` (import-linter contracts in `pyproject.toml`, run in CI; each contract
was checked to *fail* on an injected violation, so a pass means something):

1. `core` and `analytics` import **nothing** from other FinSight packages (analytics is pure).
2. `ingestion` and `processing` never import `retrieval`, `generation` or `agent`.
3. `RetrievalFilters` lives in `core`, not `retrieval`: `indexing` needs it to pre-filter and
   `retrieval` depends on `indexing`, so keeping it in `retrieval` made a package cycle (found by the
   linter's first draft, fixed by moving the value object). Heavy third-party SDKs (`anthropic`,
   `qdrant_client`, `duckdb`) are each imported from exactly one module.
4. `evaluation` depends on pipelines only through the `Answer`-returning interface — it never
   reaches into their internals.
5. `api` and `ui` contain no business logic; they translate and render.
6. Everything external (embedder, vector store, LLM, HTTP) is behind a `Protocol` so tests inject
   fakes.

## 4. Data model

```mermaid
erDiagram
    FILING_REF ||--o{ SECTION : "split into"
    SECTION ||--o{ CHUNK : "chunked into"
    FILING_REF ||--o{ FINANCIAL_FACT : "reports"
    CHUNK ||--o{ CITATION : "supports"
    ANSWER ||--o{ CITATION : "carries"
    ANSWER ||--o{ TOOL_CALL_RECORD : "traced by"

    FILING_REF {
        string cik
        string ticker
        enum form
        string accession PK
        date filed
        date period_of_report
        int fiscal_year
        enum fiscal_period
    }
    SECTION {
        string item "1A, 7, 8 ..."
        string title
        text text
    }
    CHUNK {
        string id PK "sha256 prefix"
        text text "what the LLM reads"
        text embed_text "header + text, what is indexed"
        json metadata "ticker, form, FY, item, type, tokens"
    }
    FINANCIAL_FACT {
        string metric "canonical name"
        string tag "raw us-gaap concept"
        float value
        string unit
        enum period_type "duration or instant"
        date start
        date end
        int fiscal_year
    }
    ANSWER {
        text text
        bool abstained
        string prompt_version
        string model
        float latency_ms
    }
    CITATION {
        string source_id "S1, S2 ..."
        string chunk_id FK
        text quote
    }
    TOOL_CALL_RECORD {
        string name
        json arguments
        bool is_error
    }
```

All of these are frozen Pydantic models in [`core/schemas.py`](../src/finsight/core/schemas.py).

## 5. Storage layout on disk

```
data/
├── raw/edgar/<cik>/<accession>/      primary HTML + manifest.json (sha256, url, fetched_at)
├── raw/xbrl/<cik>.json               companyfacts snapshot (cached, ETag-aware)
├── interim/sections/<accession>.jsonl
├── processed/
│   ├── chunks.parquet                every chunk with metadata (the corpus of record)
│   └── facts.duckdb                  normalised financial facts
├── indexes/
│   ├── qdrant/                       embedded Qdrant collection
│   ├── bm25/                         bm25s index
│   └── manifest.json                 embedding model, dim, chunker cfg, corpus hash
└── eval/gold_v1.jsonl                tracked in git: the project's ground truth
reports/runs/<run_id>/                config.json · results.jsonl · summary.md  (git-ignored)
```

Everything under `data/` except `data/eval/` is **regenerable** from `finsight ingest && finsight
index`, so it is git-ignored; the index manifest makes stale/incompatible indexes detectable.

## 6. Deployment view (Phase 8)

```mermaid
flowchart LR
    subgraph compose [docker compose]
        ui[ui: Streamlit :8501] --> api[api: FastAPI :8000]
        api --> qdrant[(qdrant :6333)]
        api --- vol[(volume: data/)]
    end
    api -->|HTTPS| claude[(Anthropic API)]
```

The same code runs embedded (dev, CI) or against the Qdrant server (compose) by changing one
setting; no application code differs between the two.

## 7. Cross-cutting concerns

| Concern | Where |
|---|---|
| Configuration | `config/settings.py` — env vars, nested groups, validated |
| Errors | `core/exceptions.py` — one hierarchy, mapped to HTTP codes in the API layer |
| Tracing | `core/logging.py` — `trace_id` context var attached to every log line |
| Cost & latency | `core/telemetry.py` — per-stage timers, token/cost accounting |
| Caching | SEC responses (ETag), LLM responses in eval (keyed by prompt+model+params), prompt-prefix caching in generation |
| Secrets | Environment / SDK credential chain only; never in `Settings`, logs or run artefacts |
