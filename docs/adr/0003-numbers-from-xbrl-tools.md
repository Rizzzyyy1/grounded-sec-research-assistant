# ADR-0003: Numbers come from XBRL via tools, never from the model
* **Status:** Accepted · **Date:** 2026-09-21

## Context
LLMs misquote figures and make arithmetic errors; text retrieval alone can also return a
superseded value or the wrong period. In finance a wrong number is worse than no answer.

## Decision
Numeric answers are sourced from **XBRL facts** (structured, machine-readable, filed with the
10-K) through deterministic tools, and derived metrics are computed by tested code that returns
its inputs and formula. Prose from retrieval may *explain* a number but is not the source of it.
A numeric consistency check verifies that every figure in the final answer appears in a tool
result or a cited passage.

## Alternatives considered
* *Extract numbers from filing text/tables with the LLM* — fragile, needs OCR-like table handling,
  and unverifiable.
* *Text-to-SQL over the fact table* — flexible but riskier (invalid or misleading queries); kept
  as a stretch tool behind a read-only, schema-limited executor.

## Consequences
+ Verifiable, auditable numbers; exact arithmetic. + Enables a crisp headline metric (numeric accuracy).
− Requires solving XBRL quirks (tag drift, restatements, fiscal alignment; see DATA.md).
− Only covers metrics we map; other numeric questions fall back to cited text.
