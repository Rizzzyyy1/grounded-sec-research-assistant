"""Data ingestion layer.

Everything that touches the outside world for *data*: SEC EDGAR filings, XBRL structured
facts and (optionally) market prices. Nothing in here knows about embeddings, retrieval or
LLMs.

Status: planned - Phase 1 (Data layer).
"""

from __future__ import annotations
