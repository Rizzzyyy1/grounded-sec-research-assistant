"""Streamlit pages render against a stubbed API client (no server, no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit
UI = Path(__file__).resolve().parents[3] / "src" / "finsight" / "ui"

ANSWER = {
    "answer": {
        "question": "q", "text": "Apple's revenue for fiscal 2024 was $391,035 million.", "abstained": False,
        "abstain_reason": None, "model": "router-v1", "query_type": "numeric", "latency_ms": 12.0,
        "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, "warnings": [],
        "citations": [{"source_id": "S1", "kind": "passage", "ticker": "AAPL", "form": "10-K", "fiscal_year": 2024,
                       "item": "7", "url": "https://sec.gov/x", "quote": "Net sales were $391,035 million.",
                       "metric": None}],
        "tool_calls": [{"name": "get_financial_metric", "arguments": {"ticker": "AAPL"}, "latency_ms": 3.0, "is_error": False}],
    },
    "mode": "router", "disclaimer": "Not investment advice.",
}  # fmt: skip


class StubClient:
    def __init__(self, *_a: Any, **_k: Any) -> None: ...

    def ready(self) -> dict[str, Any]:
        return {"status": "ready", "index_chunks": 23221, "embedding_model": "bge", "companies_with_facts": 12,
                "llm_credentials": False, "llm_provider": "none: llm_provider=auto but no Claude credentials",
                "default_mode": "router"}  # fmt: skip

    def query(self, question: str, mode: str = "auto") -> dict[str, Any]:
        return ANSWER

    def companies(self) -> list[dict[str, Any]]:
        return [
            {
                "ticker": t,
                "name": f"{t} Inc.",
                "sector": "IT",
                "fiscal_year_end": "09-30",
                "known_gaps": {},
            }
            for t in ("AAPL", "MSFT")
        ]

    def financials(
        self, ticker: str, metrics: list[str], years: str | None = None
    ) -> dict[str, Any]:
        pts = [
            {
                "fiscal_year": y,
                "fiscal_period": "FY",
                "value": 1e9 * y,
                "unit": "USD",
                "period_end": f"{y}-09-28",
                "xbrl_tag": "Revenues",
                "derived": False,
            }
            for y in (2023, 2024)
        ]
        return {"ticker": ticker, "metrics": dict.fromkeys(metrics, pts)}

    def ratios(self, ticker: str, names: list[str], years: str | None = None) -> dict[str, Any]:
        pts = [{"fiscal_year": y, "value": 0.4 + y / 1e5, "formatted": "40%"} for y in (2023, 2024)]
        return {
            "ticker": ticker,
            "ratios": {
                n: {"label": n, "formula": "a / b", "points": pts, "skipped": {}} for n in names
            },
        }


@pytest.fixture(autouse=True)
def _stub_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("finsight.ui.client.ApiClient", StubClient)


def run(script: str) -> AppTest:
    at = AppTest.from_file(str(UI / script), default_timeout=30)
    return at.run()


def test_home_shows_status_and_the_router_warning() -> None:
    at = run("app.py")
    assert not at.exception
    assert [m.value for m in at.metric][:2] == ["23,221", "12"]
    assert any("No LLM is available" in w.value and "--llm ollama" in w.value for w in at.warning)


def test_ask_page_shows_which_llm_serves_agent_mode() -> None:
    at = run("pages/1_Ask.py")
    assert any("Agent/auto LLM" in c.value for c in at.sidebar.caption)


def test_ask_page_renders_answer_sources_and_trace() -> None:
    at = run("pages/1_Ask.py")
    assert at.button[0].disabled  # nothing typed yet: asking is not possible
    at.text_area[0].set_value("What was Apple's revenue in fiscal 2024?").run()
    assert not at.button[0].disabled
    at.button[0].click().run()
    assert not at.exception
    assert any("$391,035 million" in m.value for m in at.markdown)
    assert any("[S1] AAPL 10-K FY2024" in e.label for e in at.expander)
    assert len(at.dataframe) == 1  # the tool trace


def test_ask_page_renders_a_fact_citation_without_a_form_or_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fact-kind Citation (agent/tools.py::_register_fact) has no form/item - only a passage
    does. Regression: the page unconditionally read c['form']/c['item'] and crashed on this."""
    fact_answer = {
        **ANSWER,
        "answer": {
            **ANSWER["answer"],
            "citations": [
                {
                    "source_id": "S1", "kind": "fact", "ticker": "AAPL", "fiscal_year": 2024,
                    "url": "https://sec.gov/x", "quote": "Revenue: $391,035 million (XBRL tag Revenues)",
                    "metric": "revenue", "form": None, "item": None,
                }
            ],
        },
    }  # fmt: skip

    class FactStubClient(StubClient):
        def query(self, question: str, mode: str = "auto") -> dict[str, Any]:
            return fact_answer

    monkeypatch.setattr("finsight.ui.client.ApiClient", FactStubClient)
    at = run("pages/1_Ask.py")
    at.text_area[0].set_value("What was Apple's revenue in fiscal 2024?").run()
    at.button[0].click().run()
    assert not at.exception
    assert any("[S1] AAPL FY2024 - revenue (XBRL)" in e.label for e in at.expander)


def test_explorer_and_compare_pages_render_charts() -> None:
    assert not run("pages/2_Company_Explorer.py").exception
    compare = run("pages/3_Compare.py")
    assert not compare.exception


def test_evaluation_page_handles_no_reports_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no reports/ directory here
    at = run("pages/4_Evaluation.py")
    assert not at.exception and len(at.info) == 2
