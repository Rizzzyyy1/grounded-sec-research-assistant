Machine: arm64 / Darwin, 8 logical cores, 16 GB; localhost; ONE uvicorn worker; real index (23,221 chunks); no LLM calls. Requests per cell shown in `n`.

| workload | concurrency | n | req/s | p50 ms | p95 ms | max ms | errors |
|---|---|---|---|---|---|---|---|
| router: numeric question (XBRL tool) | 1 | 96 | 52.6 | 18 | 21 | 46 | 0 |
| router: numeric question (XBRL tool) | 4 | 96 | 77.6 | 49 | 84 | 90 | 0 |
| router: numeric question (XBRL tool) | 16 | 96 | 31.0 | 471 | 831 | 856 | 0 |
| rag: text question (hybrid retrieval + extractive) | 1 | 48 | 5.8 | 172 | 173 | 216 | 0 |
| rag: text question (hybrid retrieval + extractive) | 4 | 48 | 6.7 | 598 | 669 | 803 | 0 |
| rag: text question (hybrid retrieval + extractive) | 16 | 48 | 6.5 | 2320 | 3396 | 3408 | 0 |
| GET financials (DuckDB read) | 1 | 96 | 50.9 | 19 | 19 | 56 | 0 |
| GET financials (DuckDB read) | 4 | 96 | 81.2 | 48 | 71 | 93 | 0 |
| GET financials (DuckDB read) | 16 | 96 | 43.0 | 332 | 516 | 611 | 0 |

## Reading the numbers

* **Single-request latency is small**: 18 ms (router numeric), 19 ms (DuckDB read), 172 ms (hybrid
  retrieval: one query embedding + dense + BM25 + RRF).
* **Throughput peaks at concurrency 4 for the cheap endpoints (~78-81 req/s) and *falls* at 16
  (31-43 req/s)**: past the knee, added concurrency only adds queueing. Tail latency at 16 is
  ~25x the p50 at 1 for the router. This is the signature of a serialised critical section
  (`db_lock` around DuckDB, deliberately added after a thread-safety race - see ERROR_ANALYSIS)
  plus one Python process, not of the network. I have not profiled to confirm which of the two
  dominates; treat that as a hypothesis.
* **The RAG path saturates at ~6-7 req/s regardless of concurrency**: it is CPU-bound in the
  embedding model and BM25 scoring, so more client threads only lengthen the queue
  (p50 2.3 s at 16).
* **Zero errors in all 9 cells** (rate limiter raised via `FINSIGHT_API_RATE_LIMIT`; the default of
  30/min per client is intentionally far below these rates).

Scope limits: localhost, one uvicorn worker, no LLM calls (the extractive backend), a single
question per workload (so caches are warm), 48-96 requests per cell. It shows where the design
saturates, not what a production deployment would sustain. Scaling paths are in
`docs/LIMITATIONS.md` (multiple workers need the Qdrant *server* - embedded mode is single-process).
