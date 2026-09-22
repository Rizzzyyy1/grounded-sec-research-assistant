"""Build a human-review worksheet for `data/eval/gold_v2_draft.jsonl`.

    .venv/bin/python scripts/build_gold_v2_review_worksheet.py

Every row is draft judgement (see the file's own `notes` field and `scripts/make_gold_v2_draft.py`).
This script does not verify anything itself - it turns the 38 rows into a checklist a human works
through, with a suggested verification command per row so nobody has to hand-derive one. See
`docs/GOLD_V2_REVIEW_CHECKLIST.md` for the review standards this worksheet is meant to be used
against. Re-run after any edit to the draft file; the worksheet is generated, never hand-edited.
"""

from __future__ import annotations

from pathlib import Path

from finsight.config.settings import get_settings
from finsight.evaluation.datasets import load_gold

ROOT = Path(__file__).resolve().parents[1]


def verify_hint(row: object) -> str:  # type: ignore[valid-type]
    if row.type.value == "out_of_scope":  # type: ignore[attr-defined]
        return "Read the question. Confirm abstention is the right call and say why (see checklist §3)."
    if row.gold_sources:  # type: ignore[attr-defined]
        s = ", ".join(f"{g.ticker} FY{g.fiscal_year} Item {g.item}" for g in row.gold_sources)  # type: ignore[attr-defined]
        return f"Open the filing at {s}; confirm it actually answers the question (checklist §2)."
    if row.expected.numeric:  # type: ignore[attr-defined]
        q = row.question.replace('"', "'")  # type: ignore[attr-defined]
        return f'`finsight ask "{q}" --llm extractive --system router` and compare to the 10-K (checklist §1).'
    return "Read the question against the checklist's general row checks (checklist §4)."


def main() -> None:
    settings = get_settings()
    rows = load_gold(settings.eval_dir / "gold_v2_draft.jsonl")
    lines = [
        "# gold_v2_draft.jsonl - human review worksheet",
        "",
        f"Generated from `data/eval/gold_v2_draft.jsonl` ({len(rows)} rows, all `provenance=draft`). "
        "Review standards: [docs/GOLD_V2_REVIEW_CHECKLIST.md](../docs/GOLD_V2_REVIEW_CHECKLIST.md). "
        "This file is generated - re-run `scripts/build_gold_v2_review_worksheet.py` after editing "
        "the draft, do not hand-edit this table.",
        "",
        "| id | type | question | expected | suggested check | verified (Y/N/fix) | reviewer notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.type.value == "out_of_scope":
            expected = "abstain"
        elif r.expected.numeric:
            expected = f"{r.expected.numeric.value:,.0f} {r.expected.numeric.unit}"
        elif r.gold_sources:
            expected = ", ".join(f"{g.ticker} FY{g.fiscal_year} It{g.item}" for g in r.gold_sources)
        else:
            expected = "-"
        q = r.question.replace("|", "\\|")
        lines.append(f"| {r.id} | {r.type.value} | {q} | {expected} | {verify_hint(r)} |  |  |")
    out = ROOT / "reports" / "gold_v2_review_worksheet.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
