# ADR-0010: Pin the CIK for successor registrants; zero filings is a failure
* **Status:** Accepted · **Date:** 2026-09-21

## Context
In 2026 ExxonMobil reorganised into a holding company. SEC's `company_tickers.json` now maps `XOM`
to a **new** registrant (CIK 0002115436) that has no 10-K history; all annual reports through FY2025
remain under the old CIK (0000034088). Ingestion resolved the ticker, found an entity with no 10-Ks,
and reported success - a silent hole in the corpus that was only noticed by counting filings by hand.

## Decision
1. `CompanySpec.cik` lets the universe **pin** the CIK that holds the history.
2. The pipeline treats *zero filings for a universe member* as an **error**, with a message that
   names the likely cause and the fix.
3. Both behaviours have regression tests, and the pinned CIK for XOM has its own test.

## Alternatives considered
* *Follow SEC's former-names / successor metadata automatically* - not reliably present for this kind
  of event, and hard to test; an explicit, reviewable pin is safer.
* *Union the old and new CIKs* - needed once FY2026 10-Ks are filed by the new entity; recorded as
  future work in ROADMAP.

## Consequences
+ The failure mode is loud and self-explaining; the universe file documents the special case.
− Someone must update the pin when a company reorganises again - which the loud failure prompts.
