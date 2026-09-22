"""Ask page: a question, a cited answer, the tool trace and the exact source passages."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from finsight.ui.client import ApiClient, ApiError

st.set_page_config(page_title="Ask - FinSight", page_icon="📊", layout="wide")
st.title("Ask")

EXAMPLES = [
    "What was Apple's revenue in fiscal 2024?",
    "What was Microsoft's gross margin (gross profit divided by revenue) in fiscal 2024?",
    "By what percentage did Nvidia's revenue grow from fiscal 2023 to fiscal 2025?",
    "Which company had the higher net margin in fiscal 2024, Coca-Cola or Walmart, and what was it?",
    "What supply chain risks does Apple describe in fiscal 2024?",
    "Should I buy Tesla stock?",
]
try:
    llm_provider = ApiClient().ready().get("llm_provider", "unknown")
except ApiError:
    llm_provider = "unknown (API unreachable)"
mode = st.sidebar.selectbox(
    "Mode", ["auto", "router", "rag", "agent"],
    help="'agent' and 'auto' use whichever LLM `finsight serve` was started with.",
)  # fmt: skip
st.sidebar.caption(f"Agent/auto LLM: **{llm_provider}**")
example = st.sidebar.selectbox("Examples", ["", *EXAMPLES])
question = st.text_area(
    "Your question", value=example, height=90, placeholder="Ask about a covered company..."
)

if st.button("Ask", type="primary", disabled=len(question.strip()) < 3):
    with st.spinner("Researching..."):
        try:
            result = ApiClient().query(question.strip(), mode)
        except ApiError as exc:
            st.error(str(exc))
            st.stop()

    answer = result["answer"]
    if answer["abstained"]:
        st.warning(f"Declined / no evidence ({answer['abstain_reason']})")
    st.markdown(answer["text"])
    for w in answer["warnings"]:
        st.warning(w)

    c1, c2, c3 = st.columns(3)
    c1.metric("Latency", f"{answer['latency_ms']:.0f} ms")
    c2.metric("Tokens", answer["usage"]["input_tokens"] + answer["usage"]["output_tokens"])
    c3.metric("Cost", f"${answer['usage']['cost_usd']:.4f}")
    st.caption(
        f"mode: {result['mode']} - model: {answer['model'] or 'none'} - question type: {answer['query_type']}"
    )

    if answer["citations"]:
        st.subheader("Sources")
        for c in answer["citations"]:
            with st.expander(
                f"[{c['source_id']}] {c['ticker']} {c['form']} FY{c['fiscal_year']} - Item {c['item']}"
            ):
                st.write(c["quote"])
                st.markdown(f"[Open the filing]({c['url']})")
    if answer["tool_calls"]:
        st.subheader("Tool trace")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "tool": t["name"],
                        "arguments": str(t["arguments"]),
                        "ms": round(t["latency_ms"]),
                        "error": t["is_error"],
                    }
                    for t in answer["tool_calls"]
                ]
            ),
            use_container_width=True,
        )
    st.caption(result["disclaimer"])
