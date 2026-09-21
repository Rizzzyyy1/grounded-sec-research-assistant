"""Build and execute the analysis notebooks (outputs are committed).

    .venv/bin/python scripts/build_notebooks.py

Each notebook reads only local artefacts produced by the pipeline (DuckDB fact store, chunks
Parquet), never the network, and every number it shows is computed in a cell.
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks"

STYLE = """\
import warnings
warnings.filterwarnings("ignore")
import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import display

plt.rcParams.update({
    "figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.25, "axes.titleweight": "bold", "axes.titlesize": 12,
})
pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 30)
"""


def md(text: str) -> nbf.NotebookNode:
    return nbf.v4.new_markdown_cell(text.strip())


def code(text: str) -> nbf.NotebookNode:
    return nbf.v4.new_code_cell(text.strip())


def build(name: str, cells: list[nbf.NotebookNode]) -> None:
    nb = nbf.v4.new_notebook(cells=cells)
    nb.metadata["kernelspec"] = {
        "name": "finsight",
        "display_name": "FinSight (.venv)",
        "language": "python",
    }
    NotebookClient(
        nb, timeout=600, kernel_name="finsight", resources={"metadata": {"path": str(ROOT)}}
    ).execute()
    nbf.write(nb, NB_DIR / name)
    print("wrote", name)


# --------------------------------------------------------------------------------------------
NB1 = [
    md("""
# 01 - Data quality of the XBRL fact store

**Question:** can an analyst trust the numbers FinSight states? This notebook audits the DuckDB
fact store built from SEC `companyfacts` for the 12-company universe: how complete it is, how often
reported values are restated, why SEC's own `fy` label cannot be used, and whether the balance sheet
balances.

*Skills shown: SQL (window/aggregate), data validation, financial-statement semantics.*
"""),
    code(
        STYLE
        + """
from finsight.config.settings import get_settings
from finsight.config.universe import load_universe
from finsight.ingestion.xbrl.store import FactStore
from finsight.ingestion.xbrl.quality import (
    check_accounting_identity, coverage_matrix, coverage_rate, identity_periods_checked, raw_availability,
    unexplained_gaps,
)

s = get_settings()
universe = load_universe(s.configs_dir / "universe.yaml")
store = FactStore(s.fact_db_path)
for table in ("facts", "fact_versions", "filings", "companies"):
    print(f"{table:14} {store.count(table):>8,} rows")
"""
    ),
    md(
        "## 1. Coverage: is every applicable value present?\n\nEach cell is one *(company, metric, fiscal year)*. A gap is either **n/a** (a bank has no gross profit), **not reported** (declared with a reason in `configs/universe.yaml`) or **missing** (a bug)."
    ),
    code("""
m = coverage_matrix(store, universe)
print(f"raw availability      : {raw_availability(m):.1%}   (value present / every cell except sector-n/a)")
print(f"unexplained gaps      : {len(unexplained_gaps(m))} cells")
print(m.status.value_counts().to_string())

order = {"reported": 0, "derived": 1, "not reported": 2, "n/a": 3, "missing": 4}
grid = m.assign(code=m.status.map(order)).groupby(["ticker", "metric"]).code.max().unstack("metric")
fig, ax = plt.subplots(figsize=(11, 4.6))
from matplotlib.colors import ListedColormap
cmap = ListedColormap(["#2a9d8f", "#8ab17d", "#e9c46a", "#d9d9d9", "#e63946"])
ax.imshow(grid.values, aspect="auto", cmap=cmap, vmin=0, vmax=4)
ax.set_xticks(range(len(grid.columns)), grid.columns, rotation=60, ha="right", fontsize=8)
ax.set_yticks(range(len(grid.index)), grid.index, fontsize=9)
ax.grid(False)
ax.set_title("Coverage by company x metric (worst status across FY2021-25)")
from matplotlib.patches import Patch
ax.legend(handles=[Patch(color=c, label=l) for c, l in zip(cmap.colors, order)], ncol=5, loc="upper center",
          bbox_to_anchor=(0.5, -0.32), frameon=False, fontsize=8)
plt.tight_layout(); plt.show()
"""),
    md(
        "## 2. Restatements: how often does a reported value change later?\n\nThe same annual figure is re-reported in later filings. Comparing values **within the same XBRL tag** shows how often a company revised (or recast) a number."
    ),
    code("""
restated = store.sql('''
    WITH v AS (
        SELECT ticker, metric, tag, end_date, value FROM fact_versions
        WHERE start_date IS NOT NULL AND date_diff('day', start_date, end_date) BETWEEN 350 AND 380
    ), g AS (
        SELECT ticker, metric, tag, end_date, min(value) lo, max(value) hi, count(DISTINCT value) n_values, count(*) n_filings
        FROM v GROUP BY ALL
    )
    SELECT *, (hi - lo) / abs(NULLIF(lo, 0)) AS rel_change FROM g
''')
multi = restated[restated.n_filings > 1]
changed = multi[multi.n_values > 1]
print(f"annual periods reported in more than one filing : {len(multi):,}")
print(f"of which the value CHANGED between filings      : {len(changed):,} ({len(changed)/len(multi):.1%})")
display(changed.sort_values("rel_change", ascending=False).head(10)[["ticker", "metric", "tag", "end_date", "lo", "hi", "rel_change"]]
        .assign(rel_change=lambda d: (d.rel_change * 100).round(1).astype(str) + "%"))
by_metric = changed.groupby("metric").size().sort_values(ascending=False).head(8)
ax = by_metric[::-1].plot.barh(figsize=(7, 3.2), color="#457b9d")
ax.set_title("Which metrics get restated most often (annual periods)"); ax.set_xlabel("# of restated company-periods")
plt.tight_layout(); plt.show()
"""),
    md(
        "**Policy consequence:** `facts` keeps the *latest filed* value per period; `fact_versions` keeps every reported version, so 'as originally reported' remains answerable."
    ),
    md(
        "## 3. Why SEC's `fy` label cannot be trusted\n\nEvery 10-K reports several fiscal years side by side (comparatives). SEC tags all of them with the *filing's* fiscal year. FinSight derives the fiscal year from the period end date instead. Here is how many distinct annual periods each 10-K actually carries for revenue:"
    ),
    code("""
per_filing = store.sql('''
    SELECT ticker, accession, form, count(DISTINCT end_date) AS annual_periods_in_filing
    FROM fact_versions
    WHERE metric = 'revenue' AND form = '10-K' AND start_date IS NOT NULL
      AND date_diff('day', start_date, end_date) BETWEEN 350 AND 380
    GROUP BY ALL
''')
print(per_filing.annual_periods_in_filing.describe().round(2).to_string())
ax = per_filing.annual_periods_in_filing.value_counts().sort_index().plot.bar(color="#e76f51", figsize=(6, 3))
ax.set_title("Annual periods per 10-K (revenue)"); ax.set_xlabel("distinct fiscal years in one filing"); ax.set_ylabel("# of 10-Ks")
plt.tight_layout(); plt.show()
mean_periods = per_filing.annual_periods_in_filing.mean()
print(f"=> a 10-K carries {mean_periods:.1f} fiscal years on average, so labelling every value with the filing's year "
      f"would be wrong for {1 - 1 / mean_periods:.0%} of them.")
"""),
    md(
        "## 4. Does the balance sheet balance?\n\n`Assets = Liabilities + Total equity (+ redeemable NCI)`, using **reported** liabilities only (a derived value satisfies the identity by construction, which would prove nothing)."
    ),
    code("""
checked = identity_periods_checked(store)
bad = check_accounting_identity(store, tolerance=0.01)
print(f"periods checked: {checked}    violations > 1%: {len(bad)}")
resid = store.sql('''
    SELECT a.ticker, a.fiscal_year, a.fiscal_period,
           1e4 * (a.value - (l.value + e.value + COALESCE(m.value, 0))) / a.value AS residual_bps
    FROM facts a
    JOIN facts l ON l.ticker=a.ticker AND l.fiscal_year=a.fiscal_year AND l.fiscal_period=a.fiscal_period AND l.metric='total_liabilities' AND NOT l.derived
    JOIN facts e ON e.ticker=a.ticker AND e.fiscal_year=a.fiscal_year AND e.fiscal_period=a.fiscal_period AND e.metric='total_equity'
    LEFT JOIN facts m ON m.ticker=a.ticker AND m.fiscal_year=a.fiscal_year AND m.fiscal_period=a.fiscal_period AND m.metric='mezzanine_equity'
    WHERE a.metric='total_assets'
''')
print(resid.residual_bps.abs().describe().round(4).to_string())
ax = resid.residual_bps.plot.hist(bins=40, color="#2a9d8f", figsize=(7, 3))
ax.set_title("Accounting-identity residual (basis points of assets)"); ax.set_xlabel("bps")
plt.tight_layout(); plt.show()
"""),
    md("""
## Findings

* The store is **complete where it can be**: every absent value is either not applicable to the
  sector or carries a written reason - none are unexplained.
* Restatements are **real and common enough to matter**, which is why the policy (latest wins, history kept) is explicit.
* Fiscal-year labels **must** be derived from dates; the SEC field describes the filing.
* The identity holds to well under a basis point once **total equity and redeemable NCI** are modelled.
  An earlier version that used parent-only equity flagged 48 'violations' - all of them
  non-controlling interests (Tesla, Exxon) - which is why parent and total equity are separate metrics.
"""),
]

# --------------------------------------------------------------------------------------------
NB2 = [
    md("""
# 02 - Corpus EDA: what does the chunked 10-K corpus look like?

Before trusting retrieval numbers, look at the corpus: how big are chunks, how much of it is
tables, which Items dominate, and where the section splitter struggles.

*Skills shown: exploratory data analysis, text statistics, pandas.*
"""),
    code(
        STYLE
        + """
import pyarrow.parquet as pq
df = pq.read_table("data/processed/chunks.parquet").to_pandas()
print(f"{len(df):,} chunks from {df.accession.nunique()} filings, {df.ticker.nunique()} companies")
print(f"text chunks {(df.chunk_type=='text').sum():,}   table chunks {(df.chunk_type=='table').sum():,}")
"""
    ),
    md(
        "## 1. Chunk size (regex tokens)\n\nText chunks target 400 tokens; tables are kept whole up to 2x that. Small text chunks are captions and short sections that survived."
    ),
    code("""
fig, axes = plt.subplots(1, 2, figsize=(11, 3.4), sharey=False)
for ax, (kind, color) in zip(axes, [("text", "#457b9d"), ("table", "#e76f51")]):
    d = df[df.chunk_type == kind].token_count
    d.plot.hist(bins=50, ax=ax, color=color)
    ax.axvline(d.median(), color="k", ls="--", lw=1)
    ax.set_title(f"{kind} chunks  (median {int(d.median())}, p95 {int(d.quantile(.95))})"); ax.set_xlabel("tokens")
plt.tight_layout(); plt.show()
print(df.groupby('chunk_type').token_count.describe().round(0).to_string())
"""),
    md("## 2. Which Items hold the text?"),
    code("""
by_item = df.groupby("item").agg(chunks=("id", "size"), tokens=("token_count", "sum"), tables=("chunk_type", lambda s: (s == "table").mean()))
by_item["share_of_tokens"] = by_item.tokens / by_item.tokens.sum()
top = by_item.sort_values("tokens", ascending=False).head(10)
display(top.assign(share_of_tokens=(top.share_of_tokens * 100).round(1), tables=(top.tables * 100).round(0)).rename(columns={"tables": "% table chunks", "share_of_tokens": "% of all tokens"}))
ax = (top.tokens[::-1] / 1e6).plot.barh(figsize=(7, 3.6), color="#2a9d8f")
ax.set_xlabel("million tokens"); ax.set_title("Where the corpus lives, by 10-K Item")
plt.tight_layout(); plt.show()
"""),
    md(
        "## 3. Company by company: an anomaly worth understanding\n\nJPMorgan's 10-K is structured differently: Items 7 and 8 are short cross-references, and the MD&A and financial statements sit in an appended annual report that falls under **Item 15**."
    ),
    code("""
per = df.groupby("ticker").agg(chunks=("id", "size"), tables=("chunk_type", lambda s: (s == "table").mean()))
per["chunks_per_filing"] = (per.chunks / df.groupby("ticker").accession.nunique()).round(0)
display(per.assign(tables=(per.tables * 100).round(0)).rename(columns={"tables": "% table chunks"}).sort_values("chunks", ascending=False))
jpm = df[df.ticker == "JPM"].groupby("item").token_count.sum().sort_values(ascending=False)
print("JPM tokens by Item:", (jpm / jpm.sum()).round(3).head(4).to_dict())
print("=> ~%.0f%% of JPM's text is filed under Item 15, so 'Item 7' filters alone would miss its MD&A (documented in docs/LIMITATIONS.md)." % (100 * jpm.get('15', 0) / jpm.sum()))
"""),
    md(
        "## 4. What the retriever actually indexes\n\nEvery chunk's indexed text is prefixed with a contextual header so near-identical boilerplate from different companies stays distinguishable."
    ),
    code("""
ex = df[(df.ticker == "AAPL") & (df.item == "7") & (df.fiscal_year == 2024)].iloc[2]
print(ex.embed_text[:600])
print("\\n--- header stripped (what the LLM reads) ---\\n" + ex.text[:300])
"""),
    md("""
## Findings

* Text chunks stay within the budget (see the histogram and the table above); tables are a large
  minority of chunks, which is why retrieval is evaluated **per chunk type**.
* The Item table above shows where the volume sits: the financial-statement Items are large but are
  mostly tables, while risk factors and MD&A are prose - the parts questions tend to be about.
* Bank filings need special handling: the JPM share printed above is measured, not assumed.
"""),
]

# --------------------------------------------------------------------------------------------
NB3 = [
    md("""
# 03 - Peer analysis: margins, returns and what drives ROE

A financial-analyst view of the 12-company universe using FinSight's own tested analytics
(`finsight.analytics`): margin trends, DuPont decomposition with driver attribution, peer
percentiles and growth. Every figure comes from the XBRL fact store.

*Skills shown: ratio analysis, DuPont, peer benchmarking, data visualisation.*
"""),
    code(
        STYLE
        + """
import math
from finsight.analytics.dupont import attribute_change, dupont_3
from finsight.analytics.peers import peer_table
from finsight.analytics.ratios import RATIOS, RatioError, compute_ratio
from finsight.analytics.trends import series_cagr
from finsight.config.settings import get_settings
from finsight.config.universe import load_universe
from finsight.ingestion.xbrl.store import FactStore

s = get_settings()
universe = load_universe(s.configs_dir / "universe.yaml")
store = FactStore(s.fact_db_path)
YEARS = list(universe.fiscal_years)

def value(t, metric, y):
    f = store.get_fact(t, metric, y)
    return None if f is None else f.value

def ratio(t, name, y):
    spec = RATIOS[name]
    try:
        cur = {m: value(t, m, y) for m in spec.inputs}
        prior = {m: value(t, m, y - 1) for m in spec.prior_inputs}
        if any(v is None for v in (*cur.values(), *prior.values())):
            return None
        return compute_ratio(name, cur, prior)
    except RatioError:
        return None

def frame(name):
    return pd.DataFrame({t: {y: ratio(t, name, y) for y in YEARS} for t in universe.tickers})
"""
    ),
    md("## 1. Profitability across the universe"),
    code("""
op, net = frame("operating_margin"), frame("net_margin")
fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), sharex=True)
for ax, data, title in [(axes[0], op, "Operating margin"), (axes[1], net, "Net margin")]:
    for t in data.columns:
        ax.plot(data.index, data[t] * 100, marker="o", lw=1.4, label=t)
    ax.set_title(title); ax.set_ylabel("%"); ax.set_xticks(YEARS)
axes[1].legend(ncol=4, fontsize=7, frameon=False, loc="upper left")
plt.tight_layout(); plt.show()
latest = net.loc[max(YEARS)].dropna().sort_values(ascending=False)
print("net margin FY%d, best to worst:" % max(YEARS)); print((latest * 100).round(1).to_string())
"""),
    md(
        "## 2. Peer ranking with percentiles (FY2024 return on equity)\n\nROE here is `net income / year-end parent shareholders' equity` - a definition stated explicitly because Apple's buybacks make it huge while a bank's is modest."
    ),
    code("""
roe = {t: ratio(t, "roe", 2024) for t in universe.tickers}
roe = {t: v for t, v in roe.items() if v is not None}
rows = peer_table(roe)
tbl = pd.DataFrame([{"ticker": r.ticker, "ROE %": round(r.value * 100, 1), "rank": r.rank, "percentile": round(r.percentile), "vs median (pp)": round(r.vs_median * 100, 1)} for r in rows])
display(tbl)
ax = tbl.set_index("ticker")["ROE %"][::-1].plot.barh(figsize=(7, 4), color="#457b9d")
ax.set_title("Return on equity, FY2024"); ax.set_xlabel("%")
plt.tight_layout(); plt.show()
"""),
    md(
        "## 3. DuPont: *why* did ROE move?\n\nROE = net margin x asset turnover x equity multiplier. Because it is a product, the change decomposes **exactly** into per-factor log contributions - the analyst's answer to 'what drove it'."
    ),
    code("""
def dp(t, y):
    ni, rev, assets, eq = (value(t, m, y) for m in ("net_income", "revenue", "total_assets", "shareholders_equity"))
    return dupont_3(ni, rev, assets, eq)

cases = [("AAPL", 2023, 2024), ("MSFT", 2023, 2024), ("NVDA", 2024, 2025), ("KO", 2023, 2024)]
recs = []
for t, y0, y1 in cases:
    try:
        contrib = attribute_change(dp(t, y0), dp(t, y1))
    except RatioError:
        continue
    total = math.log(dp(t, y1).roe / dp(t, y0).roe)
    assert abs(sum(contrib.values()) - total) < 1e-9          # the decomposition is exact
    recs.append({"case": f"{t} FY{y0}->FY{y1}", **{k: v for k, v in contrib.items()}, "ROE before %": dp(t, y0).roe * 100, "ROE after %": dp(t, y1).roe * 100})
dfc = pd.DataFrame(recs).set_index("case")
display(dfc.round(3))
ax = dfc[["net_margin", "asset_turnover", "equity_multiplier"]].plot.bar(stacked=True, figsize=(8, 3.6), color=["#2a9d8f", "#e9c46a", "#e76f51"])
ax.axhline(0, color="k", lw=0.8); ax.set_ylabel("log-contribution to ROE change"); ax.set_title("What drove the change in ROE?")
plt.xticks(rotation=15); plt.tight_layout(); plt.show()
"""),
    md("## 4. Growth: revenue CAGR FY2021-FY2025"),
    code("""
cagr = {}
for t in universe.tickers:
    series = {y: value(t, "revenue", y) for y in YEARS if value(t, "revenue", y)}
    if len(series) >= 2:
        cagr[t] = series_cagr(series)
g = pd.Series(cagr).sort_values(ascending=False) * 100
ax = g[::-1].plot.barh(figsize=(7, 4), color="#457b9d")
ax.set_title("Revenue CAGR, FY2021-FY2025"); ax.set_xlabel("% per year")
plt.tight_layout(); plt.show()
print(g.round(1).to_string())
"""),
    md("""
## Findings

* Margin dispersion is **structural** (software and consumer brands vs. retail and energy), not cyclical.
* The DuPont attribution above shows **the same headline can have opposite causes**. Apple's FY2024 ROE
  rose because of the *equity-multiplier* term (shrinking equity from buybacks) while its margin and turnover
  actually fell - a capital-structure effect. NVIDIA's FY2025 jump was *operational* (margin and turnover
  both up) with leverage a drag. Ranking companies on ROE alone would hide that difference.
* The attribution is exact by construction - a property tested in `tests/unit/analytics` with
  property-based tests, not just here.
"""),
]

# --------------------------------------------------------------------------------------------
NB4 = [
    md("""
# 04 - Retrieval error analysis: where and why does retrieval miss?

Aggregate retrieval scores say *how often* it fails. This notebook says *how*: it runs the real
retriever (hybrid, no reranker, filters on) over every gold question that has gold sources and
sorts each result into a failure bucket, so fixes can be aimed at a cause instead of a number.

Relevance is defined at **(company, fiscal year, Item)** level, so a "miss" means none of the top-8
chunks came from the right section of the right filing.

Caveat carried through the whole notebook: `gold_v1` questions are templated and auto-derived
(docs/EVALUATION.md 2.1), and only 44 have gold sources. Read every per-group number as an
indication, not an estimate.

*Skills shown: error analysis, evaluation diagnostics, pandas.*
"""),
    code(
        STYLE
        + """
import numpy as np
from finsight.config.settings import get_settings
from finsight.core.filters import RetrievalFilters
from finsight.evaluation.datasets import load_gold
from finsight.evaluation.metrics.retrieval import matches_source, score_ranking
from finsight.evaluation.runner import run_retrieval_eval
from finsight.evaluation.stats import bootstrap_ci
from finsight.stack import load_stack

settings = get_settings()
gold = [e for e in load_gold(settings.eval_dir / "gold_v1.jsonl") if e.gold_sources]
print(f"{len(gold)} questions with gold sources "
      f"(dev {sum(e.split == 'dev' for e in gold)}, test {sum(e.split == 'test' for e in gold)}); "
      f"retrieval mode={settings.retrieval.mode}, rerank={settings.retrieval.rerank}, k=8")
"""
    ),
    md("""
## 1. Run the retriever and keep the evidence

For each question keep: the gold sections, the top-8 `(ticker, year, Item)` triples, the filters the
query analyser derived, and the rank of the first relevant chunk. As a *diagnostic only* (never a
tuning input), each miss is re-run with the **Item filter removed** to see whether the filter or the
ranking was at fault.
"""),
    code("""
K = 8
records = []
with load_stack(settings) as stack:
    retriever = stack.retriever()
    ref_rows = {r.example_id: r for r in run_retrieval_eval(retriever, gold)}
    for ex in gold:
        res = retriever.retrieve(ex.question, k=K)
        ranked = [r.chunk for r in res.chunks]
        sc = score_ranking(ranked, ex.gold_sources)
        first = next((i for i, c in enumerate(ranked, 1) if any(matches_source(c, s) for s in ex.gold_sources)), None)
        gold_filings = {(s.ticker, s.fiscal_year) for s in ex.gold_sources}
        retrieved = [(c.metadata.ticker, c.metadata.fiscal_year, c.metadata.item) for c in ranked]
        right_filing = any((t, y) in gold_filings for t, y, _ in retrieved)
        item_filter = tuple(res.filters.items)
        gold_items = {s.item for s in ex.gold_sources}
        # Diagnostic (misses only): pin the search to the gold company-year and look 50 deep. Where
        # does the gold Item rank *inside its own filing*, and which Item wins instead?
        depth_rank, winner = None, None
        if first is None:
            src = ex.gold_sources[0]
            pinned = RetrievalFilters(tickers=(src.ticker,), fiscal_years=(src.fiscal_year,))
            deep = [r.chunk for r in retriever.retrieve(ex.question, k=50, filters=pinned).chunks]
            depth_rank = next((i for i, c in enumerate(deep, 1) if any(matches_source(c, s) for s in ex.gold_sources)), None)
            winner = deep[0].metadata.item if deep else None
        records.append(dict(
            id=ex.id, split=ex.split, type=ex.type.value, question=ex.question,
            ticker=ex.gold_sources[0].ticker, year=ex.gold_sources[0].fiscal_year,
            gold_items="/".join(sorted(str(i) for i in gold_items)), first_rank=first,
            hit1=sc.hit[1], hit8=sc.hit[8], recall8=sc.recall[8], mrr=sc.mrr,
            right_filing=right_filing, item_filter="/".join(item_filter) or "-",
            filter_excluded_gold=bool(item_filter) and not (gold_items & set(item_filter)),
            rank_in_own_filing=depth_rank, winning_item=winner,
            top_items=", ".join(f"{t} {y} It{i}" for t, y, i in retrieved[:4]),
        ))
df = pd.DataFrame(records)
# The notebook's scoring must equal the harness's, or one of them is wrong.
for split in ("dev", "test"):
    mine = df[df.split == split].recall8.mean()
    ref = np.mean([ref_rows[i].scores["recall@8"] for i in df[df.split == split].id])
    assert abs(mine - ref) < 1e-9, (split, mine, ref)
print("notebook scoring == evaluation harness scoring on both splits")
"""),
    md(
        "## 2. Headline: dev vs test\n\nThe dev split was used to choose the retrieval defaults; test was not. The gap between them is the honest generalisation estimate."
    ),
    code("""
rows = []
for split, g in df.groupby("split"):
    for metric in ("hit1", "hit8", "recall8", "mrr"):
        ci = bootstrap_ci(g[metric].tolist())
        rows.append(dict(split=split, n=len(g), metric=metric, mean=round(ci.mean, 3), ci_low=round(ci.lo, 3), ci_high=round(ci.hi, 3)))
tbl = pd.DataFrame(rows).pivot(index="metric", columns="split", values=["mean", "ci_low", "ci_high"])
display(tbl)
d, t = df[df.split == "dev"].recall8.mean(), df[df.split == "test"].recall8.mean()
print(f"recall@8: dev {d:.3f} vs test {t:.3f} (gap {d - t:+.3f}); the intervals above show how much of that gap is noise at this n.")
"""),
    md(
        "## 3. Failure buckets\n\nEvery question lands in exactly one bucket: found at rank 1, found at ranks 2-8, missed but the **right filing** is in the top 8 (a section-level error), or missed with **no chunk from the right filing** (a filing-level error - the more serious kind)."
    ),
    code("""
def bucket(r):
    if pd.isna(r.first_rank): pass
    elif r.first_rank == 1: return "1 found at rank 1"
    else: return "2 found at rank 2-8"
    return "3 miss: right filing, wrong section" if r.right_filing else "4 miss: wrong filing"
df["bucket"] = df.apply(bucket, axis=1)
counts = df.groupby(["bucket", "split"]).size().unstack(fill_value=0)
counts["all"] = counts.sum(axis=1)
display(counts)
ax = counts[["dev", "test"]].T.plot.barh(stacked=True, figsize=(8, 2.8), colormap="viridis")
ax.set_xlabel("questions"); ax.set_title("Where each gold question lands"); ax.legend(loc="center left", bbox_to_anchor=(1, .5), fontsize=8)
plt.tight_layout(); plt.show()
n_miss = int(df.first_rank.isna().sum())
n_sec = int((df.bucket == "3 miss: right filing, wrong section").sum())
assert n_miss == int(df.bucket.str.startswith(("3", "4")).sum()) == int((df.hit8 == 0).sum())  # buckets agree with the metric
print(f"{n_miss} misses in total; {n_sec} retrieved the right filing but the wrong section, {n_miss - n_sec} retrieved no chunk from the right filing.")
"""),
    md("## 4. Who fails? By company, question type and gold Item"),
    code("""
def hit_table(col):
    g = df.groupby(col).agg(n=("id", "size"), hit8=("hit8", "mean"), recall8=("recall8", "mean"), mrr=("mrr", "mean")).round(3)
    return g.sort_values("hit8")
for col in ("ticker", "type", "gold_items"):
    print(f"--- by {col}"); display(hit_table(col).head(8))
"""),
    md(
        "## 5. Every miss, with the evidence\n\n`item_filter` is what the query analyser inferred from the wording; `top_items` are the sections that actually came back."
    ),
    code("""
misses = df[df.first_rank.isna()][["id", "split", "ticker", "year", "gold_items", "item_filter", "rank_in_own_filing", "winning_item", "top_items", "question"]]
with pd.option_context("display.max_colwidth", 70):
    display(misses.reset_index(drop=True))
"""),
    md(
        "## 6. Diagnosis: was it the filter, or the ranking?\\n\\nTwo checks on the misses. (a) Did the query analyser apply an Item filter that could have excluded the gold section? (b) With the search pinned to the gold company-year and 50 results deep, where does the gold Item rank *inside its own filing*, and which Item wins instead? A large `rank_in_own_filing` means the section is hard to reach; `None` means it is not in the top 50 of its own filing at all."
    ),
    code("""
miss = df[df.first_rank.isna()]
print(f"(a) misses where an Item filter was applied: {int((miss.item_filter != '-').sum())} of {len(miss)}"
      f" -> the Item filter is {'the cause' if (miss.filter_excluded_gold).any() else 'not the cause of any miss'}")
r = miss.rank_in_own_filing
print(f"(b) gold Item found within its own filing's top 50: {int(r.notna().sum())} of {len(miss)}; "
      f"median rank when found {r.median() if r.notna().any() else float('nan')}")
print("    Item that beat the gold Item most often:", miss.winning_item.value_counts().to_dict())
display(miss[["id", "ticker", "year", "gold_items", "winning_item", "rank_in_own_filing"]].reset_index(drop=True))
"""),
    md(
        "## 7. The bank layout effect (JPM)\n\nJPMorgan files its MD&A and statements in an appended annual report that sits under **Item 15**, so an `Item 7` filter cannot reach them (see notebook 02). Here is how JPM questions fare compared with everyone else."
    ),
    code("""
df["is_jpm"] = df.ticker == "JPM"
cmp = df.groupby("is_jpm").agg(n=("id", "size"), hit8=("hit8", "mean"), recall8=("recall8", "mean"), item_filter_excluded_gold=("filter_excluded_gold", "mean")).round(3)
cmp.index = ["other 11 companies", "JPM"]
display(cmp)
print("JPM questions:"); display(df[df.is_jpm][["id", "split", "gold_items", "item_filter", "hit8", "top_items"]].reset_index(drop=True))
"""),
    md(
        "Are the JPM misses really wrong, or is the right text just filed under a different Item? Read what the top three chunks actually say:"
    ),
    code("""
with load_stack(settings) as stack:
    retriever = stack.retriever()
    for _, row in df[df.is_jpm & (df.hit8 == 0)].iterrows():
        print(f"{row.id} (gold Item {row.gold_items}): {row.question}")
        for r in retriever.retrieve(row.question, k=3).chunks:
            m = r.chunk.metadata
            print(f"   Item {m.item}: {r.chunk.text[:210].replace(chr(10), ' ')!r}")
        print()
"""),
    md("""
## What to take from this (and what not to)

* The tables and printed counts above are computed from the live index on every build. The
  sentences below describe the build committed with this notebook: re-read them against the
  outputs if the index or the gold set changes.
* **Every miss retrieved the right filing, and the gold section was reachable inside it** (section 6).
  The failures are section-level ranking errors, most often MD&A (Item 7) outranking the gold Item.
* **Some misses are partly a scoring artifact.** Relevance is judged by Item label, and JPM files
  the relevant text under Item 15. The JPM cell above shows the retrieved chunks - read them to
  judge how many "misses" were actually the right text under a different label. It is not a
  clean win either way, which is why the project treats section-level hit rate as a coarse proxy.
* **Section-level misses are cheap to fix and expensive to over-fit.** A per-company Item map or a
  wider Item filter would raise these numbers, but the fixes would be derived from the very
  questions being scored. The project deliberately did *not* tune against the test failures; the
  honest next step is a fresh, human-verified gold set (`gold_v2`) to measure any such change on.
* The judged unit (company-year-Item) is coarser than an answer span. A section hit does not
  prove the answer sentence was retrieved; passage-level gold labels would be the stricter test.
"""),
]

if __name__ == "__main__":
    NB_DIR.mkdir(exist_ok=True)
    build("01_data_quality.ipynb", NB1)
    build("02_corpus_eda.ipynb", NB2)
    build("03_peer_analysis.ipynb", NB3)
    build("04_retrieval_error_analysis.ipynb", NB4)
