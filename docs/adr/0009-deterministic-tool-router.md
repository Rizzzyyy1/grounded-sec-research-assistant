# ADR-0009: A deterministic tool router as the offline, no-LLM system
* **Status:** Accepted · **Date:** 2026-09-21

## Context
The design (ADR-0003) says numbers must come from structured data through tools. The LLM agent
implements that, but it needs API credentials, costs money per evaluation run, and cannot be
tested end to end in CI. We also want the *core claim* - "tools beat text for numbers" - measured
with real data rather than asserted.

## Decision
Add `ToolRouterAgent`: the rule-based query analyser picks a tool for numeric / ratio / trend /
comparison questions, calls the **same tools** the LLM agent uses, and writes the answer from the
tool output; every other question type is delegated to the RAG pipeline. It shares the `Answer`
interface, so the evaluation harness scores it like any other system. An `ExtractiveLLM` (quote the
best-matching source sentences, with citations) plays the same role for text questions.

## Alternatives considered
* *Only the LLM agent* - richer language handling, but every run needs a key and money, and the
  claim stays unmeasured until then.
* *Mocking numbers in tests* - proves plumbing, not accuracy.

## Consequences
+ Real, reproducible end-to-end numbers with no credentials; a zero-cost lower baseline that any
generative system must beat to justify its price; the API works out of the box (`mode=router`).
− Brittle to phrasing outside the analyser's rules; it is a *baseline*, not the product.
The LLM agent's advantage must show up on paraphrase and multi-step questions, not on arithmetic.
