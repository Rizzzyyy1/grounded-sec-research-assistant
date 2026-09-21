# ADR-0006: Claude via the official SDK; model configured per role
* **Status:** Accepted · **Date:** 2026-09-21

## Context
Generation, query analysis and LLM-judging have different quality/cost profiles, and evaluation
issues thousands of calls. We also need strong tool use for the agent.

## Decision
Call Claude through the **official Anthropic Python SDK** behind a thin `LLMClient` wrapper that
owns retries, streaming, prompt caching, usage/cost accounting and explicit handling of
`max_tokens` and `refusal` stop reasons. Model ids are **settings per role** (`llm.model`,
`llm.analysis_model`, `llm.judge_model`), defaulting to `claude-opus-5`; nothing is hard-coded at
call sites. Credentials come from the SDK's own chain (env var or `ant auth login` profile) and are
never stored in `Settings`.

## Alternatives considered
* *Provider-agnostic abstraction (LiteLLM etc.)* — flexibility we do not need now; hides
  provider-specific features (caching, thinking, tool semantics) that the design uses.
* *Single model for all roles* — simpler; wasteful for judge/analysis if cheaper models suffice.
  Roles keep that a one-line, evaluable change.

## Consequences
+ Cost/quality tunable per role; features used natively. − Tied to one provider; the `LLMClient`
seam and the fake used in tests keep a future swap contained.
