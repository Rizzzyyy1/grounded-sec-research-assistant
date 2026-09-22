"""FinSight - Streamlit entrypoint (home page + navigation via ``ui/pages``)."""

from __future__ import annotations

import streamlit as st

from finsight.ui.client import ApiClient, ApiError

st.set_page_config(page_title="FinSight", page_icon="📊", layout="wide")

st.title("FinSight")
st.caption(
    "Grounded financial research over SEC filings - numbers from XBRL, explanations with citations."
)

try:
    info = ApiClient().ready()
except ApiError as exc:
    st.error(f"The API is not reachable or not ready: {exc}")
    st.info("Start it with `finsight serve`, then reload this page.")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Indexed passages", f"{info['index_chunks']:,}")
col2.metric("Companies with data", info["companies_with_facts"])
col3.metric("Answer mode", info["default_mode"])
col4.metric("LLM provider", info.get("llm_provider", "unknown").split(":")[0])

st.markdown(
    """
**Pages**

* **Ask** - questions with cited answers and a visible tool trace.
* **Company Explorer** - reported financials and ratio time series.
* **Compare** - peers side by side on any metric or ratio.
* **Evaluation** - how well the system does, with confidence intervals.

*Not investment advice. Verify every figure against the cited source.*
"""
)
if info["default_mode"] == "router":
    st.warning(
        f"No LLM is available ({info.get('llm_provider', 'unknown')}), so answers use the "
        "deterministic XBRL tool router (numeric questions) and an extractive passage quoter (text "
        "questions). For the tool-using agent, restart the API with either `finsight serve --llm "
        "ollama` (free, local - needs `ollama serve` and a pulled model) or `ANTHROPIC_API_KEY` set."
    )
