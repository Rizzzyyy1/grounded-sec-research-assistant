# FinSight — Limitations, Risks and Intended Use

Written in the style of a model card. It is updated as evaluation produces evidence; statements
about performance appear here only once they are measured.

## Intended use
Research assistance for analysts: locating, quoting and computing from **public SEC filings** of
the covered universe, with citations that can be checked.

## Out of scope / not intended
* **Investment advice**, recommendations, price targets or trading signals. The assistant is
  built to decline them.
* Companies or fiscal years outside `configs/universe.yaml`; non-US filers; scanned documents.
* Real-time or intraday information. Filings lag events by weeks to months.
* Legal, tax or accounting opinions.

## Known limitations
| Area | Limitation | Mitigation / status |
|---|---|---|
| Coverage | 12 companies × 5 years; not a market-wide tool | Universe is configuration; scaling is a config + compute change |
| Data | XBRL tags vary and get restated; some metrics do not exist for some issuers (e.g. banks have no gross profit) | Coverage matrix; explicit "not applicable" responses |
| Retrieval | Tables and footnotes are harder to retrieve than prose; Item filters mislead for JPM-style layouts | Tables chunked separately; measured per chunk type; documented in ERROR_ANALYSIS |
| Generation | LLMs can still misread a passage even with correct retrieval | Citation + numeric validators; abstention; faithfulness tracked |
| Evaluation | Gold set is small (~120); per-type results are noisy; LLM judge has bias | Reported with CIs; judge calibrated against human labels; frozen test split |
| Scale | One process: the API saturates at ~6-7 RAG queries/s and its cheap endpoints peak at ~80 req/s at concurrency 4, then degrade ([load test](../reports/load_test.md)). Embedded Qdrant is single-process | Multiple workers need the Qdrant *server* (`FINSIGHT_QDRANT_URL`, compose file) and a per-process DuckDB read connection; not built or measured |
| Time | Index reflects the last ingestion; later filings and amendments (10-K/A) may be missing | Manifest shows corpus date; answers state the filing they used |
| Phrasing | The tool router's rules were built against templated wording: 0.94 on templated test questions, 0.69 on 38 natural rewrites (before the guardrail fix). Ratios asked in words that avoid the metric name ("what fraction of sales was left as gross profit") fall back to plain revenue | Measured, not fixed: fixing it against the probe set would make the probe in-sample. The LLM agent, now run against a free local model, scores indistinguishably from the router on this same probe (ERROR_ANALYSIS 3c) - `--llm claude` remains unmeasured |
| Unanswerable | Without a relevance floor the extractive fallback answers live-data questions ("share price at this moment") by quoting unrelated text | Documented in ERROR_ANALYSIS 3b; an LLM's abstention or a retrieval-score threshold is the remedy |
| Local-model quality | `llama3.2:3b` (the free `--llm ollama` default) sometimes cites a source that was never retrieved, paraphrases a qualitative answer from training-data familiarity instead of the retrieved passages, or narrates a fake tool call as plain text instead of issuing one - observed on different questions on different runs, i.e. intermittent, not tied to one question | Documented, not patched around (ERROR_ANALYSIS 3c); citation/numeric validators catch the first two as warnings; a bigger local model (`FINSIGHT_OLLAMA__MODEL`) or `--llm claude` are the two ways to reduce this, neither measured here |
| Reproducibility | A local model's default sampling gave a different tool call for an identical question on consecutive runs | `generation/ollama.py` sets `temperature=0, seed=0`; confirmed deterministic, though still not bit-exact across every backend/batch size |
| API / UI | `finsight serve` and the Streamlit UI gate agent mode on `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` only (`api/deps.py`); they cannot yet serve `--llm ollama` | Not done - CLI-only for now (`finsight ask`, `finsight eval run`); wiring a provider choice through the API is future work |
| Language | English only | — |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| User over-trusts an answer | Every claim cited to an openable passage; unverified numbers flagged; disclaimer on all outputs |
| Hallucinated figure | Numbers only from tools or quoted passages; consistency check; numeric accuracy is the headline metric |
| Prompt injection via document text | Retrieved text wrapped as data; adversarial tests in the gold set |
| Misuse as advice engine | Scope guardrail; out-of-scope slice in evaluation |
| Data-provider terms | Only SEC public data used; fair-access policy enforced in code |

## Ethical and legal notes
Public-record data only; no personal data is collected. Outputs are informational and **not
investment advice**. The author is not a registered investment adviser.

## Evidence (all from `reports/`, see [RESULTS](../reports/RESULTS.md) and [ERROR_ANALYSIS](ERROR_ANALYSIS.md))

* **Retrieval generalises less well than the dev number:** recall@8 0.90 (dev, n=29) vs **0.57 (held-out
  test companies, n=15, CI [0.33, 0.80])**. All misses are right-filing / wrong-Item.
* **Filing layout varies:** JPMorgan places its MD&A and financial statements under Item 15 (87% of its
  text); Item-based filters therefore under-serve bank-style filings.
* **Over-answering:** the extractive baseline and the router's text fallback answer "current share price
  today"-style questions with an irrelevant passage. The LLM agent, run for the first time against a free
  local model (`--llm ollama`), does abstain correctly on both such questions in the smoke test
  (ERROR_ANALYSIS 3c) - a small positive result, though not yet a systematic measurement across the gold set.
* **The gold set is templated and automatically derived.** Scores are an upper bound on natural phrasing.
  The router's rules were developed against the same phrasing, so its 0.94 is optimistic. The LLM agent
  (free local model) scores statistically indistinguishably from the router on the same natural-phrasing
  probe (paired diff -0.038, CI crosses zero) - it does not (yet, at this model size) close the gap either.
* **The test split has been inspected** during error analysis and is no longer a clean holdout.
* **Not validated live:** the Claude agent specifically (`--llm claude`, no API key available), the LLM
  judge, the refusal-fallback request shape, Docker images. The agent *has* been run live against a free
  local model (`--llm ollama`, ADR-0011) - see [RESULTS](../reports/RESULTS.md) and ERROR_ANALYSIS 3c -
  which is evidence about the architecture, not about Claude's quality.
