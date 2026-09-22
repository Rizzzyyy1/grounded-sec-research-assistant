# ADR-0012: Explicit, serve-time LLM provider selection for the API and UI

* **Status:** Accepted · **Date:** 2026-09-22

## Context

ADR-0011 added `OllamaLLM` as a second `LLMClient`, usable from `finsight ask`/`finsight eval run`
via `--llm ollama`. The HTTP API (`finsight serve`) and the Streamlit UI could not use it:
`api/deps.py::have_llm_credentials()` only ever checked for Anthropic credentials, so `mode=agent`
was Claude-or-nothing regardless of whether a local model was available. A portfolio reviewer
running only `finsight serve` + `finsight ui` - the most likely way anyone actually tries this
project - could never reach the agent without paying for an API key, even though the CLI path had
proven it worked for free.

## Decision

`Services.llm_factory` is built **once, at server startup**, from exactly one provider, chosen by
`Settings.llm_provider` (`"auto" | "claude" | "ollama"`, env `FINSIGHT_LLM_PROVIDER`, or
`finsight serve --llm ...` which sets that env var before starting uvicorn - not a request-time
option). `api/main.py::_resolve_llm` is the single place that decision is made:

* `"auto"` (default): Claude if credentials exist, else no LLM (router/extractive only) - **never**
  Ollama. Preserved exactly as the pre-existing default behaviour; nothing already deployed changes.
* `"claude"`: Claude, or a clearly-labelled unavailable state naming the missing credential -
  distinguishable in `/readyz` from the "auto" case ("llm_provider=claude but ..." vs "no Claude
  credentials").
* `"ollama"`: always Ollama, **even if `ANTHROPIC_API_KEY` happens to be set** - an explicit request
  is never overridden by what else happens to be configured. Reachability is checked once at
  startup with `generation.ollama.describe_status` (cheap and local, unlike Claude, where no such
  check exists without spending a call), so the degraded reason is exact rather than a guess.

Whichever branch runs is the *only* branch that can run for the life of the process - there is no
code path where a request-time provider failure retries a different provider. `resolve_mode`'s
error message names both remediations (`ANTHROPIC_API_KEY` and `finsight serve --llm ollama`)
because the process cannot know which one the operator meant to use.

`ReadyOut.llm_provider` (`/readyz`) and the Streamlit home page / Ask page sidebar surface exactly
what is active, so "is the free agent actually being served" is answered by the UI itself, not by
reading source.

## Why serve-time, not request-time

A per-request provider choice was considered and rejected: `Services` deliberately builds heavy
resources (the retriever, the fact store) once and reuses them, and `RagPipeline`/`ResearchAgent`
already assume one `LLMClient` per run. Making provider a request parameter would mean either
building a new `LLMClient` per request (defeats connection reuse, and for Ollama means re-paying
model-load latency) or caching multiple factories behind a dynamic key (real complexity for a need
nobody has stated - a demo server serving both providers to different callers simultaneously is not
a scenario this project needs to support). Serve-time selection, mirroring the CLI's `--llm` flag
exactly, is the smaller change and matches how the rest of the settings surface already works.

### Why an env var, not only a CLI flag

`finsight serve --llm ollama` sets `FINSIGHT_LLM_PROVIDER` in its own process *before* calling
`uvicorn.run`, rather than passing `llm` through as a Python closure argument. `uvicorn.run(...,
reload=True)` re-execs `app_factory` in a fresh subprocess that re-imports the module from scratch;
a closure-captured value would not survive that respawn, while an environment variable is inherited
automatically. It also means `FINSIGHT_LLM_PROVIDER=ollama` in `.env` or `docker-compose.yml`'s
`environment:` block works identically without going through the CLI at all - the same pattern
already used for `FINSIGHT_QDRANT_URL`.

## Consequences

+ The full free path - install, `finsight serve --llm ollama`, `finsight ui`, ask a question, see
  citations (when the model produces them) and a cost of exactly `$0.0000` - is now real and was
  verified live end to end (`/readyz`, a real `/v1/query`, and the Streamlit Ask page through a
  browser), not just unit-tested against fakes.
+ No new abstraction: `_resolve_llm` is one function, and every other piece it touches
  (`Services`, `ReadyOut`, the Streamlit pages) already existed and needed only additive fields.
− The API now has a real (if bounded) worst-case latency it did not have to consider as seriously
  before: a local model is much slower per call than Claude, and the synchronous `/v1/query` handler
  has no separate request timeout of its own beyond `OllamaSettings.timeout_s` × `max_agent_steps`
  (documented, not newly introduced, in `docs/LIMITATIONS.md`).
− `finsight ui` still has no way to show *both* providers at once or switch without restarting the
  API; this was judged the right amount of complexity for a local/demo tool, not a gap to close now.
