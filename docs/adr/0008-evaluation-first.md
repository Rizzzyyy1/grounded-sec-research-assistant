# ADR-0008: Evaluation-first — components must earn default status
* **Status:** Accepted · **Date:** 2026-09-21

## Context
RAG projects commonly stack fashionable components (rerankers, agents, LLM-generated context)
without evidence that they help, and quote a handful of cherry-picked demos as proof.

## Decision
Build the evaluation harness (Phase 5) **before** the agent (Phase 6), fix the protocol in
`docs/EVALUATION.md` before results exist, and require each optional component to beat the simpler
alternative on the frozen test split (with confidence intervals) to become the default. The
single-shot RAG pipeline is the standing baseline.

## Consequences
+ Claims in the README are measured, reproducible, and attributable to a run. + Negative results
are recorded, not hidden. − Slows the "wow" features; accepted, since it is the point of the
project.
