"""Run artefacts and Markdown reports.

Every run writes ``reports/runs/<run_id>/`` with the resolved config (no secrets: Settings holds
none), per-example results and a human-readable ``summary.md``. Numbers quoted in the README are
copied from these files, never typed by hand.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finsight.evaluation.runner import GenerationRow, GenerationSummary


def git_state(cwd: Path) -> dict[str, Any]:
    """Short SHA and dirty flag, recorded with every run so results can be reproduced."""

    def run(*args: str) -> str:
        command = ["git", *args]
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command, cwd=cwd, capture_output=True, text=True, check=False, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip()

    return {
        "sha": run("rev-parse", "--short", "HEAD") or "uncommitted",
        "dirty": bool(run("status", "--porcelain")),
    }


def new_run_dir(runs_dir: Path, name: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = runs_dir / f"{stamp}-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _citation_kind_line(s: GenerationSummary) -> list[str]:
    """Diagnostic breakdown, not part of the hygiene rate itself - see runner.py's
    `GenerationSummary.clean_citation_kinds` docstring for why this line exists."""
    if not s.clean_citation_kinds:
        return []
    parts = ", ".join(f"{k}: {v}" for k, v in sorted(s.clean_citation_kinds.items()))
    return [f"  - of the clean answers, citation kind used ({parts})"]


def render_generation_summary(name: str, s: GenerationSummary, config: dict[str, Any]) -> str:
    lines = [
        f"# Evaluation run: {name}",
        "",
        f"* questions: {s.n} (errors: {s.errors})",
        f"* **rule-based accuracy** (numeric / names / abstention): {s.accuracy}",
        f"* abstention: precision {s.abstention_precision:.2f}, "
        f"recall {s.abstention_recall:.2f}, F1 {s.abstention_f1:.2f}",
        "* citation hygiene (answers with valid citations and no flagged claims/figures): "
        + ("n/a" if s.citation_clean_rate is None else f"{s.citation_clean_rate:.1%}"),
        *_citation_kind_line(s),
        f"* latency: p50 {s.p50_latency_ms:.0f} ms, p95 {s.p95_latency_ms:.0f} ms",
        f"* cost: ${s.total_cost_usd:.4f} total, ${s.cost_per_query_usd:.5f} per query",
        "",
        "## Accuracy by question type",
        "",
        "| Type | Accuracy [95% CI] |",
        "|---|---|",
        *[f"| {t} | {ci} |" for t, ci in s.accuracy_by_type.items()],
        "",
        "Per-type intervals are wide (few questions each): treat them as exploratory.",
        "",
        "## Configuration",
        "",
        "```json",
        json.dumps(config, indent=2, default=str),
        "```",
    ]
    return "\n".join(lines)


def write_generation_run(
    run_dir: Path, name: str, rows: Sequence[GenerationRow], summary: GenerationSummary,
    config: dict[str, Any],
) -> None:  # fmt: skip
    (run_dir / "config.json").write_text(
        json.dumps(config, indent=2, default=str), encoding="utf-8"
    )
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({
                "id": r.example.id, "type": r.example.type.value, "question": r.example.question,
                "correct": r.correct, "error": r.error,
                "answer": None if r.answer is None else json.loads(r.answer.model_dump_json()),
            }) + "\n")  # fmt: skip
    (run_dir / "summary.md").write_text(
        render_generation_summary(name, summary, config), encoding="utf-8"
    )
