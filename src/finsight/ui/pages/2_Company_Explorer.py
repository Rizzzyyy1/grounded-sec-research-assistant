"""Company explorer: reported financials and ratio series straight from the XBRL store."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from finsight.ui.client import ApiClient, ApiError

st.set_page_config(page_title="Company Explorer - FinSight", page_icon="📊", layout="wide")
st.title("Company Explorer")

api = ApiClient()
try:
    companies = api.companies()
except ApiError as exc:
    st.error(str(exc))
    st.stop()

by_ticker = {c["ticker"]: c for c in companies}
ticker = st.sidebar.selectbox(
    "Company", list(by_ticker), format_func=lambda t: f"{t} - {by_ticker[t]['name']}"
)
company = by_ticker[ticker]
st.caption(f"{company['sector']} - fiscal year ends {company['fiscal_year_end']}")

METRICS = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "total_assets",
]
RATIOS = ["gross_margin", "operating_margin", "net_margin", "roe", "roa", "current_ratio"]
metrics = st.sidebar.multiselect("Metrics", METRICS, default=["revenue", "net_income"])
ratios = st.sidebar.multiselect("Ratios", RATIOS, default=["operating_margin", "net_margin"])

if metrics:
    data = api.financials(ticker, metrics)["metrics"]
    rows = [
        {
            "metric": m,
            "fiscal_year": p["fiscal_year"],
            "value_usd_m": p["value"] / 1e6,
            "xbrl_tag": p["xbrl_tag"],
        }
        for m, pts in data.items()
        for p in pts
    ]
    if rows:
        df = pd.DataFrame(rows)
        st.subheader("Reported financials (USD millions)")
        st.plotly_chart(
            px.line(df, x="fiscal_year", y="value_usd_m", color="metric", markers=True),
            use_container_width=True,
        )
        st.dataframe(
            df.pivot(index="fiscal_year", columns="metric", values="value_usd_m").round(0),
            use_container_width=True,
        )
    missing = [m for m, pts in data.items() if not pts]
    for m in missing:
        reason = company["known_gaps"].get(m)
        st.info(f"{m}: no data" + (f" - {reason}" if reason else ""))

if ratios:
    series = api.ratios(ticker, ratios)["ratios"]
    rows = [
        {"ratio": n, "fiscal_year": p["fiscal_year"], "value": p["value"]}
        for n, s in series.items()
        for p in s["points"]
    ]
    if rows:
        st.subheader("Ratios")
        st.plotly_chart(
            px.line(pd.DataFrame(rows), x="fiscal_year", y="value", color="ratio", markers=True),
            use_container_width=True,
        )
    for s in series.values():
        st.caption(
            f"**{s['label']}** = {s['formula']}"
            + (f" - not computable for: {', '.join(s['skipped'])}" if s["skipped"] else "")
        )
