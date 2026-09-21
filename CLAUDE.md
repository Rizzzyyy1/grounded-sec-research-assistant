# FinSight — working notes for Claude Code

Grounded financial research assistant (RAG over SEC filings + XBRL analytics + Claude agent).
Read `docs/DESIGN.md` first; `docs/ROADMAP.md` says which phase is current.

## Commands
```bash
make install     # venv (.venv, Python 3.13) + every runtime extra + dev tooling
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

## Claude API usage (Phase 4+)
Use the official `anthropic` SDK only. Default model `claude-opus-5`; adaptive thinking;
stream long outputs; check `stop_reason` (`max_tokens`, `refusal`) before reading content;
put stable prompt/tool prefix first for caching and verify `cache_read_input_tokens`.

## Lessons that cost time (do not repeat)
* **Scripted `str.replace` patches on files ruff has already reformatted silently match nothing.** Always
  assert the match count, or use the Edit tool. This once produced a "clean" check that joined no rows.
* **A check that passes must say how much it checked** (e.g. `identity_periods_checked`).
* **Never state a finding you have not read off the output** (notebook claims, README prose). Verify first.
* The gold `test` split was inspected during error analysis: further tuning needs a fresh split (`gold_v2`).
* Embedded Qdrant holds a process lock - do not run two `finsight` commands that open the index at once.
* **Slice-replacing a block of `cli.py` can delete the commands next to it** (`serve`/`ui` vanished this way and
  were only found by starting the server). `tests/unit/test_cli.py::test_every_documented_command_is_registered`
  guards it; prefer the Edit tool with a unique anchor over index arithmetic.
* **Read executed notebook output before trusting it.** In pandas, `if row.x:` on a NaN is truthy: notebook 04's
  bucketing silently counted 9 misses as hits and printed a wrong headline. Use `pd.isna`, and assert that a
  derived bucket count equals the metric it is meant to explain.
* **Architecture rules are enforced by `make arch` (import-linter).** Each contract was proven to fail on an
  injected violation. Keep new shared value objects in `core`, not in the package that first uses them.
* zsh does not word-split unquoted variables: pipe file lists through `xargs` when running `sed -i`.
* Long jobs (`finsight index`, ~35 min) run in the background with `nohup`; the index build is resumable.
