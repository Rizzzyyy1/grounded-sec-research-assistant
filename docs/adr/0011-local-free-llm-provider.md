# ADR-0011: A local, zero-cost LLM provider (Ollama) alongside Claude

* **Status:** Accepted · **Date:** 2026-09-22

## Context

ADR-0006 chose the official Anthropic SDK behind a thin `LLMClient` seam and explicitly declined a
provider-agnostic abstraction, reasoning that the flexibility was not needed yet. It was needed
sooner than expected: the "Claude-powered agent... has not been run live" gap (no API key) was the
single biggest weakness in the project - every claim about the LLM agent's tool use, grounding and
abstention was untested. Waiting for a paid key blocked verifying the most novel part of the system.

## Decision

Add `OllamaLLM` (`generation/ollama.py`), a second `LLMClient` implementation that calls a local
model through [Ollama](https://ollama.com)'s HTTP API - no API key, no network egress beyond
`localhost`, nothing to bill. `finsight ask --llm ollama` and `finsight eval run --llm ollama` use
it exactly like `--llm claude`, because both satisfy the same three-method `LLMClient` protocol
(`generation/llm.py`) that ADR-0006 already established as the seam for this. `agent/orchestrator.py`,
`agent/tools.py` and `generation/pipeline.py` needed **zero changes** - the whole point of the
existing seam is that they only know `LLMClient`, never a concrete provider.

The one piece of real design work is translation. Every `LLMClient` in this codebase speaks one
**canonical message shape** (a plain string, or Anthropic-style `{"type": "text"|"tool_use"|
"tool_result", ...}` blocks - see `generation/ollama.py`'s module docstring). `OllamaLLM` is the
only place that shape is translated to and from Ollama's own chat format, so the orchestrator's
tool loop never has to know which provider is running underneath it.

Default model: `llama3.2:3b` (~2 GB, tool-calling capable, fast enough on a laptop). Configurable
via `OllamaSettings` (`base_url`, `model`, `context_tokens`, `timeout_s`, `max_retries`) -
environment variables `FINSIGHT_OLLAMA__*`. `finsight doctor` checks reachability and whether the
configured model is pulled.

Two properties are non-negotiable for a "free" claim to mean anything:

1. **No fallback.** If Ollama is unreachable or the model is missing, `OllamaLLM.complete` raises a
   `GenerationError` naming the fix (`ollama pull <model>`, or start the server). It never silently
   switches to `AnthropicLLM` - a test asserts `generation/ollama.py` does not even import
   `anthropic`.
2. **Cost is always exactly $0.00.** Token counts are recorded (Ollama reports them) so latency and
   throughput are still comparable, but no price table is applied.

Generation is deterministic (`temperature=0`, `seed=0`): a local model's default sampling gave a
different tool call - sometimes a different tool entirely - for an identical question on
consecutive runs, which is incompatible with `docs/EVALUATION.md`'s reproducibility requirement.

## Alternatives considered

* *A provider-agnostic library (LiteLLM etc.)* - ADR-0006's objection still holds: it would hide
  Claude's own caching/thinking/tool semantics behind a lowest-common-denominator interface. Adding
  one more concrete `LLMClient`, behind the seam that already exists, is a smaller and more honest
  change.
* *OpenAI-compatible endpoint (`llama.cpp` server, LM Studio, vLLM) instead of Ollama* - Ollama was
  chosen for the lowest setup friction (`brew install ollama && ollama pull <model>`, one process,
  no separate model-conversion step) and mainstream tool-calling support. Because `OllamaLLM` talks
  plain HTTP/JSON, not an SDK, pointing `ollama.base_url` at any Ollama-API-compatible server works
  without further changes.
* *A larger default local model (e.g. `llama3.1:8b`)* - likely more reliable tool use, but ~4.7 GB
  and slower; `llama3.2:3b` was chosen so the default smoke test and evaluation runs finish in
  minutes on a modest laptop. The model is one setting, not a hard-coded choice.

## Consequences

+ The agent's tool-selection, grounding and abstention behaviour is now exercised end to end with a
  real model, at zero cost, reproducibly. The natural-phrasing evaluation (`docs/EVALUATION.md`
  §2.2) can compare the agent against the router and the extractive baseline on identical questions
  for the first time.
+ `finsight doctor`, `.env.example` and `README.md` gained a real "try it now, free" path that does
  not require anyone to trust an unverified claim about the Claude agent.
− A 3B local model is not Claude. Where results below show the agent losing to the deterministic
  router (`reports/RESULTS.md`), that is a property of this specific small model on this specific
  benchmark, not evidence about what `--llm claude` would do - which remains unmeasured. The two
  results must never be conflated in the README or in resume language (`docs/PORTFOLIO.md`).
− Small-model tool use has real, observed failure modes even with `temperature=0` (occasionally
  narrating a tool call as prose instead of issuing one; picking the wrong tool for a ratio
  question) - see `docs/ERROR_ANALYSIS.md`. One general, non-gold-specific fix was made in response
  (`agent/tools.py`'s `_metric` names the correct tool when a ratio name is used where a reported
  metric was expected); the rest are documented as local-model limitations, not patched around,
  because narrow patches for one small model's quirks would not generalise and would not be
  honestly separable from "tuning to the benchmark."
