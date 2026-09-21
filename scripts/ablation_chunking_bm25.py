"""Ablations A2 (chunk size) and A3 (overlap), BM25 half.

    .venv/bin/python scripts/ablation_chunking_bm25.py

Re-chunking is cheap (parse each filing once, re-chunk per configuration) and BM25 rebuilds in
seconds, unlike the dense index (~35 min to re-embed per configuration) - so only the lexical half
is measured here; the dense half is deferred and said so in the report. Scored on the dev split at
section level, so changing chunk boundaries cannot leak into the labels.
"""

from __future__ import annotations

import time
from pathlib import Path

from finsight.config.settings import ChunkingSettings, RetrievalSettings, get_settings
from finsight.config.universe import load_universe
from finsight.evaluation.ablation import AblationResult, render_ablation_markdown
from finsight.evaluation.datasets import load_gold
from finsight.evaluation.runner import run_retrieval_eval
from finsight.indexing.sparse_index import SparseIndex
from finsight.ingestion.xbrl.store import FactStore
from finsight.processing.chunking import chunk_section
from finsight.processing.html_parser import parse_filing_html
from finsight.processing.pipeline import ref_from_row
from finsight.processing.sections import split_sections
from finsight.retrieval.query_analysis import QueryAnalyzer
from finsight.retrieval.retriever import Retriever
from finsight.retrieval.sparse import SparseRetriever

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "chunk200_overlap15": ChunkingSettings(target_tokens=200, overlap_ratio=0.15),
    "chunk400_overlap15 (default)": ChunkingSettings(target_tokens=400, overlap_ratio=0.15),
    "chunk800_overlap15": ChunkingSettings(target_tokens=800, overlap_ratio=0.15),
    "chunk400_overlap0": ChunkingSettings(target_tokens=400, overlap_ratio=0.0),
    "chunk400_overlap30": ChunkingSettings(target_tokens=400, overlap_ratio=0.30),
}


def main() -> None:
    settings = get_settings()
    started = time.time()
    with FactStore(settings.fact_db_path) as store:
        catalogue_rows = store.filings()
    parsed = []  # (FilingRef, sections) - parse once, chunk many times
    for row in catalogue_rows.itertuples():
        ref = ref_from_row(row)
        parsed.append((ref, split_sections(parse_filing_html(Path(row.local_path).read_bytes()))))
    print(f"parsed {len(parsed)} filings in {time.time() - started:.0f}s")

    analyzer = QueryAnalyzer(load_universe(settings.configs_dir / "universe.yaml"))
    gold = [
        g for g in load_gold(settings.eval_dir / "gold_v1.jsonl", split="dev") if g.gold_sources
    ]
    rs = RetrievalSettings(mode="sparse", rerank=False)
    results: list[AblationResult] = []
    sizes: dict[str, int] = {}
    for name, cfg in CONFIGS.items():
        chunks = [c for ref, secs in parsed for s in secs for c in chunk_section(s, ref, cfg)]
        sizes[name] = len(chunks)
        index = SparseIndex()
        index.build(chunks)
        retriever = Retriever(
            {c.id: c for c in chunks}, rs, sparse=SparseRetriever(index), analyzer=analyzer
        )
        results.append(AblationResult(name, rs, run_retrieval_eval(retriever, gold)))
        print(f"{name}: {len(chunks):,} chunks")

    table = render_ablation_markdown(results, baseline="chunk400_overlap15 (default)")
    counts = "\n".join(f"* `{n}`: {c:,} chunks" for n, c in sizes.items())
    out = (
        f"BM25-only, dev split (n={len(gold)}), contextual header on, section-level scoring.\n\n{table}\n\n"
        f"Index size:\n\n{counts}\n\nThe dense half (embeddings) was **not** measured: re-embedding costs "
        "~35 minutes per configuration.\n"
    )
    (ROOT / "reports" / "ablation_A2_A3_bm25_dev.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
