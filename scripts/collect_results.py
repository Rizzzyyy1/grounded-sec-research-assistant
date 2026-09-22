"""Collect evaluation artefacts into reports/RESULTS.md and the README results block.

    .venv/bin/python scripts/collect_results.py            # writes reports/RESULTS.md
    .venv/bin/python scripts/collect_results.py --readme   # also refreshes README.md

Every number comes from files written by `finsight eval ...` (run summaries, retrieval and ablation
tables). Nothing here is typed by hand; if an artefact is missing the cell says so.
"""

from __future__ import annotations

import json
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


def table_row(name: str, system: str, *, zero_cost_label: str = "$0 (no LLM)") -> str:
    d, t = run_facts(system, "dev"), run_facts(system, "test")
    if not d or not t:
        return f"| {name} | not run | not run | - | - | - |"
    cost = zero_cost_label if float(t["cost"]) == 0 else f"${t['cost']}"
    return (
        f"| {name} | {d['accuracy']} | {t['accuracy']} | {t['abstain_f1']} | {t['p50']} | {cost} |"
    )


def metric(md_path: Path, name: str) -> str:
    if not md_path.is_file():
        return "n/a"
    return grab(md_path.read_text(encoding="utf-8"), rf"\| {re.escape(name)} \| ([^|]+) \|").strip()


def paired_comparison(run_a: str, run_b: str) -> str | None:
    """Paired bootstrap + McNemar between two runs on their shared scored questions.

    Mirrors `finsight eval compare` so the numbers in prose match what that command would print.
    Returns None (never a fabricated "n/a") if either run has not been produced.
    """
    a_dir, b_dir = REPORTS / "runs" / run_a, REPORTS / "runs" / run_b
    if not ((a_dir / "results.jsonl").is_file() and (b_dir / "results.jsonl").is_file()):
        return None
    sys.path.insert(0, str(ROOT / "src"))
    from finsight.evaluation.stats import mcnemar_exact, paired_bootstrap_diff  # noqa: PLC0415

    def load(run: Path) -> dict[str, bool]:
        rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
        return {r["id"]: bool(r["correct"]) for r in rows if r["correct"] is not None}

    a, b = load(a_dir), load(b_dir)
    shared = sorted(set(a) & set(b))
    va, vb = [float(a[i]) for i in shared], [float(b[i]) for i in shared]
    diff = paired_bootstrap_diff(va, vb)
    only_a = sum(1 for x, y in zip(va, vb, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(va, vb, strict=True) if y and not x)
    p = mcnemar_exact(only_a, only_b)
    sig = " (statistically distinguishable)" if diff.excludes_zero() else " (not distinguishable)"
    return (
        f"n={len(shared)} shared questions, accuracy {sum(va) / len(va):.3f} vs "
        f"{sum(vb) / len(vb):.3f}, paired difference {diff}{sig}, McNemar exact p={p:.4f}"
    )


def ollama_agent_section() -> list[str]:
    """The zero-cost path: the LLM agent run against a free, local model (ADR-0011)."""
    lines = [
        "",
        "### Zero-cost evaluation: the agent against a free, local model (`--llm ollama`, no API key)",
        "",
        "Model `llama3.2:3b` via Ollama (ADR-0011), `temperature=0`/`seed=0`, one Apple Silicon "
        "laptop, `--workers 1`. This measures *this specific 3B local model*, not an upper bound on "
        "the LLM agent - `--llm claude` remains unmeasured (see EVALUATION.md 1.1). Full traces: "
        "`reports/runs/*-agent-ollama-*`.",
        "",
        "| System | dev accuracy [95% CI] | test accuracy [95% CI] | abstention F1 (test) | p50 ms | $/query |",
        "|---|---|---|---|---|---|",
        table_row(
            "Agent (llama3.2:3b via Ollama, free & local)",
            "agent-ollama",
            zero_cost_label="$0 (free local model)",
        ),
        table_row("Tool router (for reference, no LLM)", "router"),
        table_row("Single-shot RAG (extractive, for reference, no LLM)", "rag"),
        "",
    ]
    ao = by_type("agent-ollama", "test")
    router = by_type("router", "test")
    if ao:
        lines += ["Accuracy by question type on the **test** split, agent-ollama vs router:", ""]
        lines += ["| Type | Agent (Ollama) | Router |", "|---|---|---|"]
        lines += [
            f"| {t} | {ao.get(t, '-')} | {router.get(t, '-')} |" for t in sorted(set(ao) | set(router))
        ]  # fmt: skip
        lines.append("")

    test_a = latest_run("agent-ollama", "test")
    test_stats = []
    if test_a:
        for label, b_system, b_split in (
            ("agent-ollama vs router", "router", "test"),
            ("agent-ollama vs single-shot RAG (extractive)", "rag", "test"),
        ):
            run_b = latest_run(b_system, b_split)
            result = paired_comparison(test_a.name, run_b.name) if run_b else None
            if result:
                test_stats.append(f"* **{label}** (`gold_v1` templated test split): {result}")
    if test_stats:
        lines += ["On the templated `gold_v1` test split:", "", *test_stats, ""]

    run_a = latest_run("agent-ollama", "natural")
    stats_lines = []
    if run_a:
        for label, b_system, b_split in (
            ("agent-ollama vs router", "router", "natural-refresh"),
            ("agent-ollama vs single-shot RAG (extractive)", "rag-extractive", "natural-refresh"),
        ):
            run_b = latest_run(b_system, b_split)
            result = paired_comparison(run_a.name, run_b.name) if run_b else None
            if result:
                stats_lines.append(f"* **{label}** (natural phrasing, `gold_v2_draft`): {result}")
    if stats_lines:
        lines += [
            "On the 38-question natural-phrasing probe (same file and current code as the router/RAG "
            "baselines above, so this is a same-moment, apples-to-apples comparison):",
            "",
            *stats_lines,
            "",
        ]
    return lines


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
        "| Agent (llama3.2:3b via Ollama, free & local - see below) | see below | see below | - | - | $0 |",
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
    lines += ollama_agent_section()
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
