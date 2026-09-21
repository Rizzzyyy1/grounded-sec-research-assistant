# ADR-0007: DuckDB + Parquet as the structured fact store
* **Status:** Accepted · **Date:** 2026-09-21

## Context
Financial facts are small, columnar and analytical: YoY growth, CAGR, rolling windows, peer
percentiles. We want real SQL for analysis (also useful for notebooks and a data-analyst audience)
without running a database server.

## Decision
Store normalised `FinancialFact` rows in **DuckDB** (file-backed) and export Parquet for
notebooks. Typed access goes through `FactStore`; ad-hoc analysis uses SQL directly.

## Alternatives considered
* *SQLite* — ubiquitous, but weak analytics (no good window/columnar performance).
* *Postgres* — production-grade; unnecessary operational weight at this scale.
* *pandas only* — no persistence, no SQL; poor for the "SQL skills" story.

## Consequences
+ Zero-ops analytical SQL; trivial notebook access. − Single-writer file; fine for a batch-built,
read-mostly store (documented).
