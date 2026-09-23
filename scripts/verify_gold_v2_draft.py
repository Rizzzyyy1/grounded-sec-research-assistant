"""Independent numeric/ratio verification for gold_v2_draft.jsonl (read-only, no LLM, no cost).

Recomputes every numeric/computed_metric/trend/comparison row's expected value directly from the
DuckDB fact store using the same formula definitions as analytics/ratios.py, and reports the
accession/filing URL each raw fact came from. For qualitative rows, prints the opening of the
labelled (ticker, fiscal_year, item) chunk text so a reviewer can see what the label actually
points at without opening the filing by hand first. This script only reports; it does not change
gold_v2_draft.jsonl, and it does not judge whether a qualitative section answers its question -
that stays a human call (see docs/GOLD_V2_REVIEW_CHECKLIST.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from finsight.analytics.ratios import compute_ratio
from finsight.config.settings import get_settings
from finsight.core.schemas import Chunk
from finsight.ingestion.xbrl.store import FactStore
from finsight.processing.pipeline import read_chunks

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "eval" / "gold_v2_draft.jsonl"

# id -> (ticker, metric, fiscal_year) for a direct single-fact lookup
DIRECT_FACTS: dict[str, tuple[str, str, int]] = {
    "nat-num-001": ("AMZN", "revenue", 2024), "nat-num-002": ("NVDA", "net_income", 2025),
    "nat-num-003": ("TSLA", "total_assets", 2023), "nat-num-004": ("GOOGL", "operating_income", 2024),
    "nat-num-005": ("WMT", "operating_cash_flow", 2024), "nat-num-007": ("JNJ", "revenue", 2022),
    "nat-num-008": ("PG", "net_income", 2024), "nat-inj-026": ("KO", "revenue", 2023),
}  # fmt: skip

# id -> (ratio name, {ratio input -> (ticker, metric, fiscal_year)})
RATIO_CHECKS: dict[str, tuple[str, dict[str, tuple[str, str, int]]]] = {
    "nat-ratio-009": ("net_margin", {"net_income": ("MSFT", "net_income", 2024), "revenue": ("MSFT", "revenue", 2024)}),
    "nat-ratio-010": ("gross_margin", {"gross_profit": ("AAPL", "gross_profit", 2023), "revenue": ("AAPL", "revenue", 2023)}),
    "nat-ratio-011": ("roe", {"net_income": ("XOM", "net_income", 2023), "shareholders_equity": ("XOM", "shareholders_equity", 2023)}),
    "nat-ratio-012": ("current_ratio", {"current_assets": ("WMT", "current_assets", 2024), "current_liabilities": ("WMT", "current_liabilities", 2024)}),
}  # fmt: skip

# id -> (start (ticker, metric, year), end (ticker, metric, year))
TREND_CHECKS: dict[str, tuple[tuple[str, str, int], tuple[str, str, int]]] = {
    "nat-trend-013": (("JPM", "net_income", 2022), ("JPM", "net_income", 2024)),
    "nat-trend-014": (("NVDA", "revenue", 2022), ("NVDA", "revenue", 2024)),
}

# id -> (ratio name, ticker A, ticker B)
COMPARISON_CHECKS: dict[str, tuple[str, str, str]] = {
    "nat-cmp-015": ("operating_margin", "AMZN", "GOOGL"),
    "nat-cmp-016": ("net_margin", "KO", "PG"),
}


def fact(store: FactStore, ticker: str, metric: str, year: int) -> dict[str, Any] | None:
    f = store.get_fact(ticker, metric, year)
    if f is None:
        return None
    return {
        "value": f.value,
        "tag": f.tag,
        "accession": f.accession,
        "filed": str(f.filed),
        "url": store.filing_url(f.accession),
    }


def check_direct(store: FactStore, row_id: str, expected: float) -> None:
    f1 = fact(store, *DIRECT_FACTS[row_id])
    print(f"  fact: {f1} vs gold {expected}")


def check_ratio(store: FactStore, row_id: str, expected: float) -> None:
    name, inputs = RATIO_CHECKS[row_id]
    facts = {k: fact(store, *v) for k, v in inputs.items()}
    recomputed = compute_ratio(name, {k: v["value"] for k, v in facts.items()})  # type: ignore[index]
    print(f"  recomputed {name} = {recomputed} vs gold {expected}")
    for k, f in facts.items():
        print(f"  {k}: {f}")


def check_trend(store: FactStore, row_id: str, expected: float) -> None:
    start, end = TREND_CHECKS[row_id]
    f1, f2 = fact(store, *start), fact(store, *end)
    recomputed = (f2["value"] - f1["value"]) / abs(f1["value"])  # type: ignore[index]
    print(f"  recomputed change = {recomputed} vs gold {expected}")
    print(f"  start: {f1}")
    print(f"  end:   {f2}")


def check_comparison(store: FactStore, row_id: str, expected: float) -> None:
    ratio_name, a, b = COMPARISON_CHECKS[row_id]
    metrics = {
        "operating_margin": ("operating_income", "revenue"),
        "net_margin": ("net_income", "revenue"),
    }
    num_metric, den_metric = metrics[ratio_name]
    values = {}
    for t in (a, b):
        fn, fd = fact(store, t, num_metric, 2024), fact(store, t, den_metric, 2024)
        assert fn is not None and fd is not None
        values[t] = (
            compute_ratio(ratio_name, {num_metric: fn["value"], den_metric: fd["value"]}),
            fn,
            fd,
        )
    winner = a if values[a][0] > values[b][0] else b
    for t in (a, b):
        v, fn, fd = values[t]
        print(f"  {t} {ratio_name} = {v}  ({fn} / {fd})")
    print(f"  winner={winner} value={max(values[a][0], values[b][0])} vs gold {expected}")


def check_qualitative(row: dict[str, Any], by_key: dict[tuple[str, int, str], list[Chunk]]) -> None:
    for gs in row["gold_sources"]:
        chunks = by_key.get((gs["ticker"], gs["fiscal_year"], gs["item"]), [])
        print(
            f"  {gs['ticker']} FY{gs['fiscal_year']} Item {gs['item']}: {len(chunks)} chunk(s) indexed"
        )
        for c in chunks[:1]:
            print(f"    first chunk opening: {c.text[:220]!r}")


def check_row(
    store: FactStore, row: dict[str, Any], by_key: dict[tuple[str, int, str], list[Chunk]]
) -> None:
    exp = row["expected"]
    row_id = row["id"]
    if row_id == "nat-num-006":
        print("  -> Coca-Cola diluted EPS is reported directly as a per-share XBRL fact; no")
        print("     recompute path here (see manual review notes).")
    elif row_id in DIRECT_FACTS:
        check_direct(store, row_id, exp["numeric"]["value"])
    elif row_id in RATIO_CHECKS:
        check_ratio(store, row_id, exp["numeric"]["value"])
    elif row_id in TREND_CHECKS:
        check_trend(store, row_id, exp["numeric"]["value"])
    elif row_id in COMPARISON_CHECKS:
        check_comparison(store, row_id, exp["numeric"]["value"])
    elif row["type"] == "qualitative":
        check_qualitative(row, by_key)


def main() -> None:
    settings = get_settings()
    store = FactStore(settings.fact_db_path)
    catalogue = {c.id: c for c in read_chunks(settings.processed_dir / "chunks.parquet")}
    by_key: dict[tuple[str, int, str], list[Chunk]] = {}
    for c in catalogue.values():
        by_key.setdefault((c.metadata.ticker, c.metadata.fiscal_year, c.metadata.item), []).append(
            c
        )

    rows = [json.loads(line) for line in GOLD.read_text().splitlines()]
    for row in rows:
        print(f"=== {row['id']} [{row['type']}] {row['question']}")
        check_row(store, row, by_key)
        print()

    print("=== reference: tickers with facts ===")
    print(sorted(store.tickers()))


if __name__ == "__main__":
    main()
