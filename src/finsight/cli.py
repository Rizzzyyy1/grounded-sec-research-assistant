"""Command-line interface (``finsight ...``).

Only commands that actually work are registered. The roadmap in ``docs/ROADMAP.md`` lists the
ones that arrive with each phase (``ingest``, ``index``, ``ask``, ``eval``, ``serve``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from finsight import __version__
from finsight.config.settings import Settings, get_settings
from finsight.config.universe import load_universe
from finsight.core.exceptions import ConfigError, FinSightError
from finsight.core.schemas import FormType
from finsight.evaluation.datasets import GoldExample, load_gold

app = typer.Typer(
    name="finsight",
    help="Grounded, citation-first financial research assistant.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")
eval_app = typer.Typer(help="Evaluation harness.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")

console = Console()


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"finsight {__version__}")


@config_app.command("show")
def config_show() -> None:
    """Print the fully resolved settings as JSON (contains no secrets)."""
    settings = get_settings()
    console.print_json(json.dumps(settings.model_dump(mode="json"), default=str))


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


# extra name -> import module that proves it is installed
_EXTRAS: dict[str, tuple[str, ...]] = {
    "data": ("httpx", "duckdb", "pandas", "lxml", "bs4"),
    "ml": ("numpy", "fastembed", "qdrant_client", "bm25s"),
    "llm": ("anthropic",),
    "eval": ("scipy", "sklearn"),
    "api": ("fastapi", "uvicorn"),
    "ui": ("streamlit", "plotly"),
}


def run_checks() -> list[Check]:
    """Environment preflight. Pure function so it can be unit-tested."""
    settings = get_settings()
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(Check("python >= 3.11", py_ok, sys.version.split()[0]))

    try:
        settings.require_sec_user_agent()
        checks.append(Check("SEC user agent", True, "configured"))
    except ConfigError as exc:
        checks.append(Check("SEC user agent", False, str(exc), required=False))

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    checks.append(
        Check(
            "Anthropic credentials",
            has_key,
            "found in environment" if has_key else "not in environment (needed from Phase 4)",
            required=False,
        )
    )

    if importlib.util.find_spec("httpx") is not None:
        from finsight.generation.ollama import describe_status  # noqa: PLC0415

        ok, detail = describe_status(settings.ollama)
        checks.append(Check(f"Ollama ({settings.ollama.model})", ok, detail, required=False))

    for extra, modules in _EXTRAS.items():
        missing = [m for m in modules if importlib.util.find_spec(m) is None]
        # Name is "extra: x", not "extra [x]": rich would parse "[x]" as markup and drop it.
        checks.append(
            Check(
                f"extra: {extra}",
                not missing,
                "installed" if not missing else f"missing: {', '.join(missing)}",
                required=False,
            )
        )
    return checks


@app.command()
def doctor() -> None:
    """Check the environment: Python, credentials, optional dependency groups."""
    table = Table(title="finsight doctor")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")

    checks = run_checks()
    for c in checks:
        status = "[green]ok[/]" if c.ok else ("[red]FAIL[/]" if c.required else "[yellow]warn[/]")
        table.add_row(c.name, status, c.detail)
    console.print(table)

    if any(not c.ok and c.required for c in checks):
        raise typer.Exit(code=1)


@app.command()
def filings(
    ticker: Annotated[str, typer.Argument(help="Ticker from the universe, e.g. AAPL.")],
    form: Annotated[
        list[FormType] | None, typer.Option("--form", "-f", help="10-K and/or 10-Q.")
    ] = None,
    universe_path: Annotated[
        Path | None,
        typer.Option("--universe", help="Universe YAML (default: configs/universe.yaml)."),
    ] = None,
    all_years: Annotated[
        bool, typer.Option("--all-years", help="Ignore the universe year range.")
    ] = False,
) -> None:
    """List a company's filings on EDGAR with their derived fiscal periods (live SEC request)."""
    # Lazy: these pull in httpx (the `data` extra); the rest of the CLI must work without it.
    from finsight.ingestion.edgar.client import EdgarClient  # noqa: PLC0415
    from finsight.ingestion.edgar.filings import list_filings  # noqa: PLC0415

    settings = get_settings()
    try:
        universe = load_universe(universe_path or settings.configs_dir / "universe.yaml")
        try:
            company = universe.company(ticker)
        except KeyError:
            raise ConfigError(f"{ticker.upper()} is not in universe {universe.name!r}") from None
        with EdgarClient(settings) as client:
            refs = list_filings(
                client,
                company.ticker,
                fiscal_year_end=company.fiscal_year_end,
                forms=form or universe.forms,
                fiscal_years=None if all_years else universe.fiscal_years,
                cik=company.cik,
            )
    except FinSightError as exc:
        console.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"{company.name} ({company.ticker}) - FYE {company.fiscal_year_end}")
    for column in ("filing", "period end", "filed", "accession"):
        table.add_column(column)
    for ref in refs:
        table.add_row(ref.label, str(ref.period_of_report), str(ref.filed), ref.accession)
    console.print(table)


@app.command()
def ingest(
    tickers: Annotated[
        str | None, typer.Option(help="Comma-separated subset, e.g. AAPL,MSFT (default: all).")
    ] = None,
    filings_only: Annotated[bool, typer.Option("--filings-only", help="Skip XBRL facts.")] = False,
    facts_only: Annotated[
        bool, typer.Option("--facts-only", help="Skip filing downloads.")
    ] = False,
    force: Annotated[bool, typer.Option(help="Re-download filings already on disk.")] = False,
    universe_path: Annotated[Path | None, typer.Option("--universe")] = None,
) -> None:
    """Download filings and load XBRL facts for the universe (live SEC requests, resumable)."""
    from finsight.ingestion.edgar.client import EdgarClient  # noqa: PLC0415
    from finsight.ingestion.pipeline import run_ingestion  # noqa: PLC0415
    from finsight.ingestion.xbrl.store import FactStore  # noqa: PLC0415

    settings = get_settings()
    try:
        universe = load_universe(universe_path or settings.configs_dir / "universe.yaml")
        settings.ensure_dirs()
        with EdgarClient(settings) as client, FactStore(settings.fact_db_path) as store:
            report = run_ingestion(
                universe,
                client=client,
                store=store,
                raw_dir=settings.raw_dir,
                tickers=tickers.split(",") if tickers else None,
                download=not facts_only,
                load_facts=not filings_only,
                force=force,
                on_progress=lambda msg: console.print(f"[dim]{escape(msg)}[/]"),
            )
    except FinSightError as exc:
        console.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"filings downloaded: {report.filings_downloaded}, skipped: {report.filings_skipped}; "
        f"facts loaded for {len(report.facts_loaded)} companies "
        f"({sum(report.facts_loaded.values())} facts)"
    )
    for where, message in report.failures:
        console.print(f"[red]failed[/] {escape(where)}: {escape(message)}")
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def coverage(
    write: Annotated[Path | None, typer.Option(help="Write the Markdown report here.")] = None,
    universe_path: Annotated[Path | None, typer.Option("--universe")] = None,
) -> None:
    """Report XBRL coverage (company x metric x year) and accounting-identity checks."""
    from finsight.ingestion.xbrl.quality import (  # noqa: PLC0415
        check_accounting_identity,
        coverage_by_ticker,
        coverage_matrix,
        coverage_rate,
        identity_periods_checked,
        render_coverage_markdown,
        unexplained_gaps,
    )
    from finsight.ingestion.xbrl.store import FactStore  # noqa: PLC0415

    settings = get_settings()
    try:
        universe = load_universe(universe_path or settings.configs_dir / "universe.yaml")
    except FinSightError as exc:
        console.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc
    if not settings.fact_db_path.is_file():
        console.print("[red]error:[/] no fact store yet - run `finsight ingest` first")
        raise typer.Exit(code=1)

    with FactStore(settings.fact_db_path) as store:
        matrix = coverage_matrix(store, universe)
        violations = check_accounting_identity(store)
        checked = identity_periods_checked(store)

    table = Table(title=f"XBRL coverage - overall {coverage_rate(matrix):.1%}")
    table.add_column("ticker")
    table.add_column("coverage", justify="right")
    for row in coverage_by_ticker(matrix).itertuples():
        table.add_row(str(row.ticker), f"{row.coverage:.1%}")
    console.print(table)
    console.print(
        f"accounting identity: {len(violations)} violations (>1%) in {checked} periods checked; "
        f"unexplained gaps: {len(unexplained_gaps(matrix))} cells"
    )
    if write:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(render_coverage_markdown(matrix, violations, checked), encoding="utf-8")
        console.print(f"wrote {write}")


@app.command()
def process() -> None:
    """Parse every downloaded filing into sections and chunks (writes chunks.parquet)."""
    from finsight.ingestion.xbrl.store import FactStore  # noqa: PLC0415
    from finsight.processing.pipeline import process_corpus  # noqa: PLC0415

    settings = get_settings()
    if not settings.fact_db_path.is_file():
        console.print("[red]error:[/] no filings catalogue yet - run `finsight ingest` first")
        raise typer.Exit(code=1)
    out = settings.processed_dir / "chunks.parquet"
    with FactStore(settings.fact_db_path) as store:
        report = process_corpus(
            store.filings(),
            settings.chunking,
            out,
            on_progress=lambda m: console.print(f"[dim]{escape(m)}[/]"),
        )
    console.print(f"{report.n_chunks} chunks from {len(report.stats)} filings -> {out}")
    console.print(f"core Items found in {report.core_detection_rate:.0%} of filings")
    for where, message in report.failures:
        console.print(f"[red]failed[/] {escape(where)}: {escape(message)}")
    if report.failures:
        raise typer.Exit(code=1)


@app.command()
def index(
    limit: Annotated[
        int | None, typer.Option(help="Index only the first N chunks (smoke test).")
    ] = None,
    index_dir: Annotated[Path | None, typer.Option(help="Where to write the index.")] = None,
) -> None:
    """Build the BM25 + vector indexes from chunks.parquet (resumable; embedding is slow)."""
    from finsight.indexing.builder import build_indexes  # noqa: PLC0415
    from finsight.indexing.embeddings import make_embedder  # noqa: PLC0415
    from finsight.indexing.sparse_index import SparseIndex  # noqa: PLC0415
    from finsight.processing.pipeline import read_chunks  # noqa: PLC0415
    from finsight.stack import open_vector_store  # noqa: PLC0415

    settings = get_settings()
    chunks_path = settings.processed_dir / "chunks.parquet"
    if not chunks_path.is_file():
        console.print("[red]error:[/] no chunks.parquet - run `finsight process` first")
        raise typer.Exit(code=1)
    target = index_dir or settings.index_dir
    chunks = read_chunks(chunks_path)[:limit]
    embedder = make_embedder(settings.embedding)
    store = open_vector_store(settings, embedder.dim)
    try:
        manifest = build_indexes(
            chunks,
            embedder,
            store,
            SparseIndex(),
            index_dir=target,
            chunking=settings.chunking.model_dump(),
            on_progress=lambda m: console.print(f"[dim]{escape(m)}[/]"),
        )
    finally:
        store.close()
    console.print(
        f"indexed {manifest.n_chunks:,} chunks with {manifest.embedding_model} "
        f"(dim {manifest.embedding_dim}) -> {target}"
    )


@eval_app.command("gold")
def eval_gold(
    out: Annotated[
        Path | None, typer.Option(help="Output JSONL (default data/eval/gold_v1.jsonl).")
    ] = None,
    seed: Annotated[
        int, typer.Option(help="Sampling seed (recorded in the file's provenance).")
    ] = 7,
) -> None:
    """Build the programmatic gold set from the XBRL fact store and the chunked corpus."""
    import pyarrow.parquet as pq  # noqa: PLC0415

    from finsight.evaluation.datasets import summarize, write_gold  # noqa: PLC0415
    from finsight.evaluation.gold_builder import build_gold  # noqa: PLC0415
    from finsight.ingestion.xbrl.store import FactStore  # noqa: PLC0415

    settings = get_settings()
    chunks_path = settings.processed_dir / "chunks.parquet"
    if not (settings.fact_db_path.is_file() and chunks_path.is_file()):
        console.print("[red]error:[/] run `finsight ingest` and `finsight process` first")
        raise typer.Exit(code=1)
    universe = load_universe(settings.configs_dir / "universe.yaml")
    table = pq.read_table(chunks_path, columns=["ticker", "fiscal_year", "item"]).to_pylist()
    sections = {(r["ticker"], r["fiscal_year"], r["item"]) for r in table}
    with FactStore(settings.fact_db_path) as store:
        examples = build_gold(store, universe, available_sections=sections, seed=seed)
    target = out or settings.eval_dir / "gold_v1.jsonl"
    write_gold(examples, target)
    console.print(f"wrote {len(examples)} examples -> {target}")
    for key, counts in summarize(examples).items():
        console.print(f"  {key}: {counts}")


def _load_gold_split(settings: Settings, split: str, gold: Path | None = None) -> list[GoldExample]:
    if split not in {"dev", "test", "all"}:
        raise ConfigError(f"--split must be dev, test or all, got {split!r}")
    chosen: Literal["dev", "test"] | None = None if split == "all" else split  # type: ignore[assignment]
    return load_gold(gold or settings.eval_dir / "gold_v1.jsonl", split=chosen)


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]error:[/] {escape(message)}")
    return typer.Exit(code=1)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Your question about the covered companies.")],
    llm: Annotated[
        str,
        typer.Option(
            help="'claude' (needs an API key), 'ollama' (free, local, needs `ollama serve`), "
            "or 'extractive' (offline, no model)."
        ),
    ] = "claude",
    system: Annotated[
        str, typer.Option(help="'rag' (single-shot), 'router' (no LLM) or 'agent' (tool loop).")
    ] = "rag",
    sources: Annotated[bool, typer.Option("--sources", help="Show the cited passages.")] = False,
) -> None:
    """Ask a question; get a cited, validated answer."""
    from finsight.ingestion.xbrl.store import FactStore  # noqa: PLC0415
    from finsight.stack import load_stack, make_llm  # noqa: PLC0415

    settings = get_settings()
    try:
        facts = FactStore(settings.fact_db_path) if settings.fact_db_path.is_file() else None
        with load_stack(settings) as stack:
            answer = stack.system(system, make_llm(llm, settings), facts)(question)
    except FinSightError as exc:
        raise _fail(str(exc)) from exc

    console.print(f"\n{escape(answer.text)}\n")
    if answer.abstained:
        console.print(f"[yellow]abstained[/] ({answer.abstain_reason})")
    for c in answer.citations:
        console.print(
            f"[cyan][{c.source_id}][/] {c.ticker} {c.form.value} FY{c.fiscal_year}, Item {c.item}"
        )
        if sources:
            console.print(f"    [dim]{escape(c.quote)}[/]")
    for w in answer.warnings:
        console.print(f"[yellow]warning:[/] {escape(w)}")
    console.print(
        f"[dim]{answer.model or '-'} | {answer.latency_ms:.0f} ms | "
        f"{answer.usage.input_tokens + answer.usage.output_tokens} tokens | "
        f"${answer.usage.cost_usd:.4f}[/]"
    )


@eval_app.command("retrieval")
def eval_retrieval(
    split: Annotated[str, typer.Option(help="dev, test or all.")] = "dev",
    no_filters: Annotated[
        bool, typer.Option("--no-filters", help="Disable query-derived filters.")
    ] = False,
    write: Annotated[Path | None, typer.Option(help="Also write the table as Markdown.")] = None,
    gold: Annotated[
        Path | None, typer.Option(help="Gold JSONL (default data/eval/gold_v1.jsonl).")
    ] = None,
) -> None:
    """Score retrieval (no LLM) with the configured retrieval settings."""
    from finsight.evaluation.runner import (  # noqa: PLC0415
        latency_percentiles,
        run_retrieval_eval,
        summarize_retrieval,
    )
    from finsight.stack import load_stack  # noqa: PLC0415

    settings = get_settings()
    try:
        examples = _load_gold_split(settings, split, gold)
        with load_stack(settings) as stack:
            rows = run_retrieval_eval(stack.retriever(), examples, auto_filters=not no_filters)
    except FinSightError as exc:
        raise _fail(str(exc)) from exc

    table = Table(
        title=f"retrieval ({split}, n={len(rows)}, filters {'off' if no_filters else 'on'})"
    )
    table.add_column("metric")
    table.add_column("mean [95% CI]")
    for metric, ci in summarize_retrieval(rows).items():
        table.add_row(metric, str(ci))
    console.print(table)
    p50, p95 = latency_percentiles([r.latency_ms for r in rows])
    console.print(f"latency: p50 {p50:.0f} ms, p95 {p95:.0f} ms")
    if write:
        rt = settings.retrieval
        lines = [
            f"Retrieval, split `{split}`, n={len(rows)} questions, filters "
            f"{'off' if no_filters else 'on'}, mode `{rt.mode}`, rerank={rt.rerank}.",
            "",
            "| metric | mean [95% CI] |",
            "|---|---|",
            *[f"| {m} | {ci} |" for m, ci in summarize_retrieval(rows).items()],
            "",
            f"Latency: p50 {p50:.0f} ms, p95 {p95:.0f} ms.",
        ]
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text("\n".join(lines) + "\n", encoding="utf-8")


@eval_app.command("ablate")
def eval_ablate(
    split: Annotated[str, typer.Option(help="dev, test or all.")] = "dev",
    presets: Annotated[
        Path | None, typer.Option(help="Presets YAML (default configs/retrieval.yaml).")
    ] = None,
    baseline: Annotated[
        str, typer.Option(help="Preset the others are compared against.")
    ] = "dense_only",
    write: Annotated[Path | None, typer.Option(help="Write the Markdown table here.")] = None,
    no_filters: Annotated[bool, typer.Option("--no-filters")] = False,
    gold: Annotated[
        Path | None, typer.Option(help="Gold JSONL (default data/eval/gold_v1.jsonl).")
    ] = None,
) -> None:
    """Ablation A1: dense vs BM25 vs hybrid vs hybrid+rerank, with paired significance."""
    from finsight.config.settings import RetrievalSettings  # noqa: PLC0415
    from finsight.config.universe import load_presets  # noqa: PLC0415
    from finsight.evaluation.ablation import (  # noqa: PLC0415
        render_ablation_markdown,
        run_retrieval_ablation,
    )
    from finsight.stack import load_stack  # noqa: PLC0415

    settings = get_settings()
    try:
        examples = _load_gold_split(settings, split, gold)
        preset_map = load_presets(
            presets or settings.configs_dir / "retrieval.yaml", RetrievalSettings
        )
        with load_stack(settings) as stack:
            results = run_retrieval_ablation(
                stack.retriever, preset_map, examples, auto_filters=not no_filters,
                on_progress=lambda m: console.print(f"[dim]{escape(m)}[/]"),
            )  # fmt: skip
    except FinSightError as exc:
        raise _fail(str(exc)) from exc
    markdown = render_ablation_markdown(results, baseline=baseline)
    console.print(markdown)
    if write:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(markdown + "\n", encoding="utf-8")
        console.print(f"wrote {write}")


@eval_app.command("run")
def eval_run(
    system: Annotated[
        str, typer.Option(help="rag, router (XBRL tools, no LLM) or agent (Claude).")
    ] = "rag",
    llm: Annotated[
        str,
        typer.Option(
            help="'claude', 'ollama' (free, local) or 'extractive' (LLM used by rag/agent)."
        ),
    ] = "extractive",
    split: Annotated[str, typer.Option(help="dev, test or all.")] = "dev",
    workers: Annotated[int, typer.Option(help="Concurrent questions.")] = 4,
    name: Annotated[str | None, typer.Option(help="Run name (default: the system).")] = None,
    gold: Annotated[
        Path | None, typer.Option(help="Gold JSONL (default data/eval/gold_v1.jsonl).")
    ] = None,
) -> None:
    """Score a system end to end on the gold set; writes reports/runs/<id>/."""
    from finsight.evaluation.report import (  # noqa: PLC0415
        git_state,
        new_run_dir,
        write_generation_run,
    )
    from finsight.evaluation.runner import (  # noqa: PLC0415
        run_generation_eval,
        summarize_generation,
    )
    from finsight.ingestion.xbrl.store import FactStore  # noqa: PLC0415
    from finsight.stack import load_stack, make_llm  # noqa: PLC0415

    settings = get_settings()
    run_name = name or (system if system == "router" else f"{system}-{llm}")
    try:
        examples = _load_gold_split(settings, split, gold)
        facts = FactStore(settings.fact_db_path) if settings.fact_db_path.is_file() else None
        with load_stack(settings) as stack:
            answer_fn = stack.system(system, make_llm(llm, settings), facts)
            rows = run_generation_eval(
                answer_fn, examples, workers=workers,
                on_progress=lambda d, t: console.print(f"[dim]{d}/{t}[/]", end="\r"),
            )  # fmt: skip
            manifest = stack.manifest
    except FinSightError as exc:
        raise _fail(str(exc)) from exc

    summary = summarize_generation(rows)
    config = {
        "system": system, "llm": llm, "split": split, "n_questions": len(examples),
        "retrieval": settings.retrieval.model_dump(), "llm_settings": settings.llm.model_dump(),
        "index": {"embedding": manifest.embedding_model, "n_chunks": manifest.n_chunks,
                  "corpus_hash": manifest.corpus_hash},
        "git": git_state(settings.base_dir),
    }  # fmt: skip
    run_dir = new_run_dir(settings.runs_dir, run_name)
    write_generation_run(run_dir, run_name, rows, summary, config)
    console.print(
        f"\n{run_name}: accuracy {summary.accuracy} | abstention F1 {summary.abstention_f1:.2f} | "
        f"errors {summary.errors} | cost ${summary.total_cost_usd:.4f}"
    )
    console.print(f"wrote {run_dir}")


@eval_app.command("compare")
def eval_compare(
    run_a: Annotated[Path, typer.Argument(help="First run directory.")],
    run_b: Annotated[Path, typer.Argument(help="Second run directory (the reference).")],
) -> None:
    """Paired comparison of two runs on the questions both scored (bootstrap CI + McNemar)."""
    import json  # noqa: PLC0415

    from finsight.evaluation.stats import mcnemar_exact, paired_bootstrap_diff  # noqa: PLC0415

    def load(run: Path) -> dict[str, tuple[str, bool]]:
        rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
        return {r["id"]: (r["type"], bool(r["correct"])) for r in rows if r["correct"] is not None}

    try:
        a, b = load(run_a), load(run_b)
    except FileNotFoundError as exc:
        raise _fail(f"not a run directory: {exc.filename}") from exc
    shared = sorted(set(a) & set(b))
    va, vb = [float(a[i][1]) for i in shared], [float(b[i][1]) for i in shared]
    diff = paired_bootstrap_diff(va, vb)
    only_a = sum(1 for x, y in zip(va, vb, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(va, vb, strict=True) if y and not x)
    console.print(f"{run_a.name}  vs  {run_b.name}   (n={len(shared)} shared scored questions)")
    console.print(f"accuracy: {sum(va) / len(va):.3f} vs {sum(vb) / len(vb):.3f}")
    console.print(f"paired difference (A - B): {diff}" + ("  *" if diff.excludes_zero() else ""))
    p_value = mcnemar_exact(only_a, only_b)
    console.print(
        f"only A correct: {only_a}, only B correct: {only_b}, McNemar exact p = {p_value:.4f}"
    )
    by_type: dict[str, list[tuple[float, float]]] = {}
    for i in shared:
        by_type.setdefault(a[i][0], []).append((float(a[i][1]), float(b[i][1])))
    table = Table(title="by question type (exploratory)")
    for col in ("type", "n", "A", "B"):
        table.add_column(col)
    for t, pairs in sorted(by_type.items()):
        table.add_row(
            t,
            str(len(pairs)),
            f"{sum(p[0] for p in pairs) / len(pairs):.2f}",
            f"{sum(p[1] for p in pairs) / len(pairs):.2f}",
        )
    console.print(table)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Auto-reload (development).")] = False,
) -> None:
    """Run the HTTP API (needs the `api` extra and built indexes)."""
    import uvicorn  # noqa: PLC0415

    settings = get_settings()
    uvicorn.run(
        "finsight.api.main:app_factory",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port.")] = 8501,
    api_url: Annotated[
        str, typer.Option(help="Where the API is running.")
    ] = "http://127.0.0.1:8000",
) -> None:
    """Run the Streamlit app (needs the `ui` extra and a running API: `finsight serve`)."""
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    script = Path(__file__).parent / "ui" / "app.py"
    env = {**os.environ, "FINSIGHT_API_URL": api_url}
    command = [
        sys.executable, "-m", "streamlit", "run", str(script),
        "--server.port", str(port),
        # Streamlit's first-run prompt asks for an email on stdin and BLOCKS without a terminal;
        # headless mode skips it. Usage statistics are never sent.
        "--browser.gatherUsageStats", "false",
        "--server.headless", "false" if sys.stdin.isatty() else "true",
    ]  # fmt: skip
    console.print(f"FinSight UI -> http://localhost:{port}  (API: {api_url})")
    raise typer.Exit(subprocess.call(command, env=env))  # noqa: S603 - argv built from our paths


if __name__ == "__main__":  # pragma: no cover
    app()
