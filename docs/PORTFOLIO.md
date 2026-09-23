# FinSight — Project overview

FinSight combines SEC filing retrieval, deterministic XBRL calculations, and a tool-using
language-model agent. This page maps the implementation to its evidence and limitations.

## Implementation and evidence

| Area | Implementation | Evidence |
|---|---|---|
| Data pipeline | Cached SEC ingestion, fiscal-period normalization, DuckDB fact store | [Data](DATA.md), [coverage checks](coverage.md) |
| Retrieval | Section-aware chunks, dense + BM25 search, reciprocal rank fusion | [Architecture](ARCHITECTURE.md), [ablation results](../reports/RESULTS.md) |
| Financial calculations | Deterministic ratios, trends, peer comparisons and DuPont analysis | `src/finsight/analytics/`, `tests/unit/analytics/` |
| Agent | Provider interface for Claude and Ollama, structured tools, citation and figure checks | [Provider decision](adr/0011-local-free-llm-provider.md), [error analysis](ERROR_ANALYSIS.md) |
| Evaluation | Rule-based scoring, bootstrap intervals, paired comparisons, dataset exposure records | [Evaluation](EVALUATION.md), [dataset integrity](DATASET_INTEGRITY.md) |
| Serving and quality | FastAPI, Streamlit, Docker, strict typing, architecture contracts and CI | [CI](https://github.com/Rizzzyyy1/grounded-sec-research-assistant/actions), [load test](../reports/load_test.md) |

## Reported results and their scope

The committed [results report](../reports/RESULTS.md) records local Ollama agent accuracy of
0.853 versus 0.941 for the deterministic router on the 34 scored `gold_v1` test questions.
The paired difference is -0.088 with a 95% interval of [-0.235, 0.059]. This does not establish
an accuracy difference, and it does not establish equivalence.

The same report records **32.5% citation hygiene** for that agent test run. This automated
check is distinct from accuracy and does not establish that every citation supports its claim.
The agent's 4.2% dev figure comes from an older implementation; current dev performance has
not been re-measured. Citation quality remains a substantial limitation.

The natural-phrasing comparison records agent accuracy of 0.885 versus 0.846 for the router
on 26 scored questions from a 38-question probe. That probe was used to develop fixes and is
**in-sample**, with draft labels. It is not evidence of performance on unseen questions.
`gold_v1` test failures have also been inspected; see the dataset integrity record before
interpreting any reported interval as evidence of generalization.

These are previously committed measurements, not a new evaluation. Raw per-run traces are
excluded from Git, so a public clone cannot independently recompute all reported statistics
without obtaining those artifacts or performing new runs. Claude integration is implemented,
but no live Claude evaluation is reported. Load-test results cover one worker and exclude
live LLM generation; they do not establish production capacity.

## Development process

This repository was developed with substantial AI assistance, including Claude Code.
Commit co-author trailers preserve that attribution. AI-assisted implementation and review
are distinct from independent validation: tests and recorded experiments support specific
claims, while draft labels and unmeasured paths remain explicitly identified.

Design rationale and alternatives are documented in the [architecture decisions](adr/README.md).
[CLAUDE.md](../CLAUDE.md) contains contributor workflow instructions, not evaluation evidence.

## Remaining validation

- Independently review source filings and draft labels. Reviewing `gold_v2_draft` improves label
  quality but cannot undo its use in development or make it a fresh holdout.
- Review the proposed [new holdout](HOLDOUT_V1_DESIGN.md) before running the system against it.
  Record its first evaluation separately and track any subsequent exposure or tuning.
- Measure current dev performance and generalization of citation attribution on fresh questions.
- Evaluate the Claude provider before making any claims about its accuracy, latency or cost.
- Preserve model identity, environment details and shareable run artifacts for reproducibility.

Use CI and a fresh `make test` run for the current test count; source-line and test totals are
not maintained as portfolio claims.
