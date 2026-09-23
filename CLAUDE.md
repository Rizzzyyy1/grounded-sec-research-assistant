# FinSight — Contributor instructions for Claude Code

Grounded financial research assistant (RAG over SEC filings + XBRL analytics + Claude agent).
Read `docs/DESIGN.md` first; `docs/ROADMAP.md` says which phase is current.

## Commands
```bash
make install     # venv (.venv, Python 3.13) + runtime dependencies + dev tooling
make check       # ruff + ruff format --check + mypy --strict + arch + pytest  (== CI)
make test        # hermetic tests only; -m "not network and not llm and not slow"
make arch        # import-linter architecture contracts (docs/ARCHITECTURE.md section 3)
make doctor      # finsight doctor: env, credentials, installed extras
# pipeline:  finsight ingest -> process -> index -> eval gold -> eval run/ablate -> serve / ui
# results:   python scripts/collect_results.py --readme   (README numbers are generated, never typed)
```

## Rules that matter
* **Numbers come from XBRL tools, never from the model** (ADR-0003). Any code path that lets the
  LLM state a figure it did not get from a tool result or cited passage is a bug.
* **Never invent results.** README/docs metrics must come from `reports/runs/*/summary.md`.
  Placeholders (`—`) stay until a real run exists.
* **No secrets in `Settings`** — Anthropic credentials are resolved by the SDK. A test enforces it.
* **Model ids come from `Settings.llm.*` per role** — never hard-code a model at a call site.
* **A new `LLMClient` (`generation/*.py`) must never fall back to another provider on failure** —
  raise `GenerationError` with an actionable message instead (`generation/ollama.py` is the pattern).
* Everything external (embedder, vector store, LLM, HTTP) is behind a `Protocol` with a fake;
  unit tests must be hermetic (no network, model downloads, or API keys).
* Dependency direction is in `docs/ARCHITECTURE.md` §3; `core` and `analytics` import nothing
  from other FinSight packages.
* SEC access goes only through `ingestion/edgar/` (User-Agent + ≤ 10 req/s limiter + cache).
* Domain models are frozen with `extra="forbid"`; add fields deliberately.

## Conventions
* Python ≥ 3.11, `from __future__ import annotations`, strict typing, Ruff line length 100.
* New module → docstring stating responsibility (a test enforces it). New decision → ADR.
* Tests mirror the package under `tests/unit/`; mark costly ones `network` / `llm` / `slow`.
* Rich treats `[x]` in strings as markup — don't put bracketed text in table cells un-escaped.
* Tests run with cwd = a tmp dir and a scrubbed `FINSIGHT_*` env (see `tests/conftest.py`).

## Provider and evaluation workflow

* Use the official Anthropic SDK for Claude; obtain model IDs from settings rather than this file.
* Keep provider selection explicit. Check response stop reasons and preserve error reporting.
* Preserve AI co-author attribution; never claim independent human review of AI-generated work.
* Consult `docs/DATASET_INTEGRITY.md` before interpreting or changing evaluations. Mechanical
  scoring does not prevent contamination through development on the scored questions.
* Do not run the proposed `holdout_v1_draft` against the system before its labels are reviewed.
* Local sampling uses `temperature=0, seed=0`; this is not a guarantee across model versions or
  backends. Record model identity and environment for new runs.
* Do not run commands concurrently against the same embedded Qdrant or DuckDB store.
* Keep results-generation prose and committed reports consistent. A public clone omits raw
  `reports/runs/` artifacts; regenerating without them replaces measurements with missing cells.
* Validate changes with targeted checks and `make check` where dependencies are available.
  Live provider or serving checks must be reported separately from tests using fakes.
