"""Financial table handling.

Extracts tables to DataFrames, serialises them to markdown for the LLM, detects primary
statements, and normalises numbers (parentheses -> negative, 'in millions' scaling).

Public API (planned):
    - extract_table(block) -> ParsedTable
    - table_to_markdown(table) -> str

Status: planned - Phase 2 (Parsing & chunking).
"""

from __future__ import annotations
