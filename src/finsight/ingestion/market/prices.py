"""Daily price history.

Fetches and caches adjusted daily prices (yfinance) to Parquet. Powers valuation-context
tools such as trailing P/E or drawdown, and is clearly labelled non-advisory.

Public API (planned):
    - get_price_history(ticker, start, end) -> DataFrame

Status: planned - Phase 6 (Agent & analytics).
"""

from __future__ import annotations
