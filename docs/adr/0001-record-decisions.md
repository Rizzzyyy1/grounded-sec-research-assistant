# ADR-0001: Record architecture decisions
* **Status:** Accepted · **Date:** 2026-09-21

## Context
The project's value to a reader is largely in *why* it is built this way. Rationale that lives
only in commit messages or memory is lost, and reviewers cannot tell deliberate trade-offs from
accidents.

## Decision
Keep short ADRs in `docs/adr/` for every significant, hard-to-reverse choice. Superseded ADRs are
kept and linked, not deleted.

## Consequences
+ Rationale is reviewable and searchable. + Forces alternatives to be considered explicitly.
− Small ongoing writing cost; mitigated by keeping records to one page.
