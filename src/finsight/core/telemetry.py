"""Timing, token and cost telemetry.

Context managers and counters for latency, token usage and dollar cost per request, surfaced
in logs, API responses and evaluation reports.

Public API (planned):
    - timed(stage) -> ContextManager
    - CostTracker.add(usage, model)

Status: planned - Phase 4 (Grounded generation).
"""

from __future__ import annotations
