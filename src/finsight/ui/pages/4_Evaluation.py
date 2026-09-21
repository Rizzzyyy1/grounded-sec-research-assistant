"""Evaluation dashboard: run summaries and the retrieval ablation, straight from reports/."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Evaluation - FinSight", page_icon="📊", layout="wide")
st.title("Evaluation")
st.caption(
    "Everything here is read from files written by `finsight eval ...`; nothing is typed by hand."
)

REPORTS = Path("reports")
ablations = sorted(REPORTS.glob("ablation*.md"))
runs = sorted((REPORTS / "runs").glob("*/summary.md"), reverse=True)

st.subheader("Retrieval ablations")
if ablations:
    for f in ablations:
        st.markdown(f"**{f.name}**")
        st.markdown(f.read_text(encoding="utf-8"))
else:
    st.info("None yet - run `finsight eval ablate --write reports/ablation_A1.md`.")

st.subheader("End-to-end runs")
if runs:
    choice = st.selectbox("Run", runs, format_func=lambda p: p.parent.name)
    st.markdown(choice.read_text(encoding="utf-8"))
else:
    st.info("None yet - run `finsight eval run`.")
