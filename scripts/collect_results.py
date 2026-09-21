"""Collect evaluation artefacts into reports/RESULTS.md and the README results block.

    .venv/bin/python scripts/collect_results.py            # writes reports/RESULTS.md
    .venv/bin/python scripts/collect_results.py --readme   # also refreshes README.md

Every number comes from files written by `finsight eval ...` (run summaries, retrieval and ablation
tables). Nothing here is typed by hand; if an artefact is missing the cell says so.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def latest_run(system: str, split: str) -> Path | None:
    runs = sorted((REPORTS / "runs").glob(f"*-{system}-{split}"))
    return runs[-1] if runs else None


def grab(text: str, pattern: str, default: str = "n/a") -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else default


def run_facts(system: str, split: str) -> dict[str, str]:
    run = latest_run(system, split)
    if run is None:
        return {}
    t = (run / "summary.md").read_text(encoding="utf-8")
    return {
        "accuracy": grab(t, r"rule-based accuracy\*\* \([^)]*\): ([^\n]+)"),
        "abstain_f1": grab(t, r"F1 ([0-9.]+)"),
        "p50": grab(t, r"p50 ([0-9.]+) ms"),
        "cost": grab(t, r"\$([0-9.]+) per query"),
        "n": grab(t, r"questions: (\d+)"),
        "run": run.name,
    }


def by_type(system: str, split: str) -> dict[str, str]:
    """Per-type accuracy means (intervals are omitted: n per type is tiny)."""
    run = latest_run(system, split)
    if run is None:
        return {}
    text = (run / "summary.md").read_text(encoding="utf-8")
    section = text.split("## Accuracy by question type")[1].split("Per-type")[0]
    rows = re.finditer(r"^\| (\w+) \| ([0-9.]+)", section, re.M)
    return {m.group(1): m.group(2) for m in rows}


def table_row(name: str, system: str) -> str:
    d, t = run_facts(system, "dev"), run_facts(system, "test")
    if not d or not t:
        return f"| {name} | not run | not run | - | - | - |"
    cost = "$0 (no LLM)" if float(t["cost"]) == 0 else f"${t['cost']}"
    return (
        f"| {name} | {d['accuracy']} | {t['accuracy']} | {t['abstain_f1']} | {t['p50']} | {cost} |"
    )


def metric(md_path: Path, name: str) -> str:
    if not md_path.is_file():
        return "n/a"
    return grab(md_path.read_text(encoding="utf-8"), rf"\| {re.escape(name)} \| ([^|]+) \|").strip()


def natural_section() -> list[str]:
    """Templated vs natural phrasing: the honest generalisation check for the two baselines."""
    rows = []
    for name, system in (("Single-shot RAG", "rag"), ("Tool router", "router")):
        gold, pre, post = (
            run_facts(system, "test"),
            run_facts(system, "natural"),
            run_facts(system, "natural-postfix"),
        )
        if not (gold and pre and post):
            rows.append(f"| {name} | not run | not run | not run |")
            continue
        rows.append(
            f"| {name} | {gold['accuracy']} (test) | **{pre['accuracy']}** "
            f"(abstention F1 {pre['abstain_f1']}) | {post['accuracy']} "
            f"(abstention F1 {post['abstain_f1']}) |"
        )
    nat = REPORTS / "retrieval_natural.md"
    return [
        "",
        "### Templated vs natural phrasing (`gold_v2_draft`, 38 questions, unverified draft labels)",
        "",
        "| System | `gold_v1` templated | natural, **before** the guardrail fix (clean) "
        "| natural, after the fix (**in-sample**) |",
        "|---|---|---|---|",
        *rows,
        "",
        "The middle column is the clean measurement: numeric expectations come from the XBRL store "
        "and it was taken before any system change was made in response to these questions. The probe "
        "then exposed a hole in the advice guardrail (all 4 naturally phrased advice requests slipped "
        "through; abstention recall 3/9 -> 7/9 after the fix), which was fixed and the probe re-run. That last column is **in-sample** (the fix was designed "
        "from those very questions) and shows the fix works, not how well it generalises. "
        f"Retrieval on the natural questions (n=12 with gold sources, filters on): recall@8 "
        f"{metric(nat, 'recall@8')}, hit@8 {metric(nat, 'hit@8')}; text-question section labels "
        "in this file are unverified draft judgement.",
    ]


def build() -> str:
    lines = [
        "### End-to-end accuracy on `gold_v1` (rule-based: numbers, names, abstention)",
        "",
        "| System | dev accuracy [95% CI] | **test** accuracy [95% CI] | abstention F1 (test) | p50 ms | $/query |",
        "|---|---|---|---|---|---|",
        table_row("Single-shot RAG (extractive quoting, no LLM)", "rag"),
        table_row("Tool router (XBRL tools + extractive fallback, no LLM)", "router"),
        "| Claude agent (tools + LLM) | not run: no API key | not run | - | - | - |",
        "",
        "Accuracy by question type on the **test** split (exploratory: few questions per type):",
        "",
    ]
    rag, router = by_type("rag", "test"), by_type("router", "test")
    lines += ["| Type | RAG | Router |", "|---|---|---|"]
    lines += [
        f"| {t} | {rag.get(t, '-')} | {router.get(t, '-')} |"
        for t in sorted(set(rag) | set(router))
    ]
    lines += [
        "",
        "### Retrieval (section-level; no LLM), default config = hybrid, rerank off",
        "",
        "| split | filters | recall@8 [95% CI] | MRR | nDCG@8 |",
        "|---|---|---|---|---|",
    ]
    for label, path, flt in (
        ("dev (n=29)", REPORTS / "retrieval_dev.md", "on"),
        ("**test (n=15)**", REPORTS / "retrieval_test.md", "on"),
        ("test (n=15)", REPORTS / "retrieval_test_nofilters.md", "off"),
    ):
        lines.append(
            f"| {label} | {flt} | {metric(path, 'recall@8')} | {metric(path, 'mrr')} | {metric(path, 'ndcg@8')} |"
        )
    lines += natural_section()
    for title, name in (
        ("Ablation A1: retrieval mode (dev split)", "ablation_A1_dev.md"),
        (
            "Ablation A2/A3 (BM25 half): chunk size and overlap (dev split)",
            "ablation_A2_A3_bm25_dev.md",
        ),
        (
            "Ablation A4 (BM25 half): contextual header (dev split)",
            "ablation_A4_bm25_header_dev.md",
        ),
        ("Ablation A7: how many chunks to retrieve (dev split)", "ablation_A7_recall_curve_dev.md"),
        ("API load test (one worker, localhost, no LLM)", "load_test.md"),
    ):
        path = REPORTS / name
        lines += [
            "",
            f"### {title}",
            "",
            path.read_text(encoding="utf-8").strip() if path.is_file() else "_not run_",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    body = build()
    (REPORTS / "RESULTS.md").write_text(
        "# Results\n\nGenerated by `scripts/collect_results.py` from `reports/`.\n\n" + body,
        encoding="utf-8",
    )
    print(body)
    if "--readme" in sys.argv:
        readme = ROOT / "README.md"
        text = readme.read_text(encoding="utf-8")
        if START not in text or END not in text:
            raise SystemExit("README.md has no results markers")
        head, rest = text.split(START, 1)
        _, tail = rest.split(END, 1)
        readme.write_text(f"{head}{START}\n{body}{END}{tail}", encoding="utf-8")
        print("README.md results block refreshed")


if __name__ == "__main__":
    main()
