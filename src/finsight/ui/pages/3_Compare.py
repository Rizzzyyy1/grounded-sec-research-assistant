"""Compare page: peers side by side on one metric or ratio."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from finsight.ui.client import ApiClient, ApiError

st.set_page_config(page_title="Compare - FinSight", page_icon="📊", layout="wide")
st.title("Compare")

api = ApiClient()
try:
    tickers = [c["ticker"] for c in api.companies()]
except ApiError as exc:
    st.error(str(exc))
    st.stop()

chosen = st.sidebar.multiselect("Companies", tickers, default=tickers[:4])
ratio = st.sidebar.selectbox(
    "Ratio", ["net_margin", "operating_margin", "gross_margin", "roe", "roa", "current_ratio"]
)

if len(chosen) >= 2:
    rows, skipped = [], {}
    for t in chosen:
        series = api.ratios(t, [ratio])["ratios"][ratio]
        rows += [
            {"ticker": t, "fiscal_year": p["fiscal_year"], "value": p["value"]}
            for p in series["points"]
        ]
        if not series["points"]:
            skipped[t] = "not applicable / not reported"
    if rows:
        df = pd.DataFrame(rows)
        st.plotly_chart(
            px.line(df, x="fiscal_year", y="value", color="ticker", markers=True, title=ratio),
            use_container_width=True,
        )
        latest = df[df.fiscal_year == df.fiscal_year.max()].sort_values("value", ascending=False)
        st.subheader(f"Ranking, fiscal {int(df.fiscal_year.max())}")
        st.dataframe(latest.reset_index(drop=True), use_container_width=True)
    for t, why in skipped.items():
        st.info(f"{t}: {why}")
else:
    st.info("Pick at least two companies.")
