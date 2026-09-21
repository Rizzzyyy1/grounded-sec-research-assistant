"""Deterministic router and the LLM agent loop (scripted model)."""

from __future__ import annotations

from typing import Any

import pytest

from finsight.agent.orchestrator import AGENT_PROMPT_VERSION, AGENT_SYSTEM, ResearchAgent
from finsight.agent.router import ToolRouterAgent
from finsight.agent.tools import AgentContext
from finsight.config.settings import LLMSettings
from finsight.core.schemas import Answer, QueryType, Usage
from finsight.generation.llm import LLMResult, ToolUse
from finsight.generation.prompts import DECLINE_ADVICE

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ router
def fallback(question: str) -> Answer:
    return Answer(question=question, text="delegated to RAG", model="rag")


@pytest.fixture
def router(ctx: AgentContext) -> ToolRouterAgent:
    return ToolRouterAgent(ctx, fallback)


def test_numeric_question_is_answered_from_xbrl_with_provenance(router: ToolRouterAgent) -> None:
    a = router.answer("What was Apple's revenue in fiscal 2024?")
    assert "$391,035 million" in a.text and "fiscal 2024" in a.text
    assert "tag:revenue" in a.text and a.warnings == () and not a.abstained
    assert [c.name for c in a.tool_calls] == ["get_financial_metric"] and a.is_grounded
    assert a.query_type is QueryType.NUMERIC


def test_ratio_question_shows_formula_and_inputs(router: ToolRouterAgent) -> None:
    a = router.answer(
        "What was Apple's gross margin (gross profit divided by revenue) in fiscal 2024?"
    )
    assert "46.2%" in a.text and "gross_profit / revenue" in a.text and "$180,683 million" in a.text
    assert a.warnings == ()


def test_trend_question_reports_growth_between_the_two_years(router: ToolRouterAgent) -> None:
    a = router.answer(
        "By what percentage did Apple's revenue grow from fiscal 2022 to fiscal 2024?"
    )
    assert "17.6%" in a.text  # 1.0 / 0.85 - 1
    assert a.query_type is QueryType.TREND and a.warnings == ()


def test_comparison_names_the_winner_and_its_value(router: ToolRouterAgent) -> None:
    a = router.answer(
        "Which company had the higher gross margin in fiscal 2024, Apple or Microsoft, and what was it?"
    )
    assert a.text.startswith("Microsoft had the higher gross margin") and "69.8%" in a.text
    assert "Apple 46.2%" in a.text


def test_advice_is_declined_without_any_tool_call(router: ToolRouterAgent) -> None:
    a = router.answer("Should I buy Apple stock?")
    assert a.abstained and a.text == DECLINE_ADVICE and a.tool_calls == ()


def test_non_numeric_questions_are_delegated(router: ToolRouterAgent) -> None:
    a = router.answer("What manufacturing risks does Apple describe?")
    assert a.text == "delegated to RAG" and a.tool_calls == ()


@pytest.mark.parametrize(
    "question",
    [
        "What was Apple's revenue in fiscal 2015?",  # not in the store
        "What was JPM's gross margin in fiscal 2024?",  # bank: not applicable
        "What was Apple's revenue?",  # no year given
    ],
)
def test_unanswerable_numeric_questions_abstain_with_the_reason(
    router: ToolRouterAgent, question: str
) -> None:
    a = router.answer(question)
    assert a.abstained and a.abstain_reason == "tool_error"
    assert any(c.is_error for c in a.tool_calls) or "year" in a.text


# ------------------------------------------------------------------ orchestrator
class ScriptedAgentLLM:
    """Plays back a fixed list of LLMResults, recording every request it receives."""

    def __init__(self, *turns: LLMResult) -> None:
        self.turns, self.requests = list(turns), []

    def complete(
        self,
        *,
        system: str,
        messages: Any,
        tools: Any = None,
        model: Any = None,
        max_tokens: Any = None,
    ) -> LLMResult:
        self.requests.append(
            {"system": system, "messages": [dict(m) for m in messages], "tools": tools}
        )
        return self.turns.pop(0)


def turn(text: str = "", *uses: ToolUse, tokens: int = 100) -> LLMResult:
    raw = [{"type": "tool_use", "id": u.id, "name": u.name, "input": u.input} for u in uses] or [
        {"type": "text", "text": text}
    ]
    return LLMResult(text=text, stop_reason="tool_use" if uses else "end_turn", model="claude-opus-5",
                     usage=Usage(input_tokens=tokens, output_tokens=10, cost_usd=0.01), tool_uses=uses, raw_content=raw)  # fmt: skip


def agent(ctx: AgentContext, llm: ScriptedAgentLLM, **kw: Any) -> ResearchAgent:
    return ResearchAgent(llm, ctx, LLMSettings(), **kw)


def test_tool_loop_runs_tools_returns_results_in_one_turn_and_validates(ctx: AgentContext) -> None:
    llm = ScriptedAgentLLM(
        turn("", ToolUse("t1", "get_financial_metric", {"ticker": "AAPL", "metric": "revenue", "fiscal_years": [2024]}),
             ToolUse("t2", "compute_ratio", {"ticker": "AAPL", "ratio": "gross_margin", "fiscal_year": 2024})),
        turn("Apple's fiscal 2024 revenue was $391,035 million and its gross margin was 46.2%."),
    )  # fmt: skip
    a = agent(ctx, llm).answer("What was Apple's revenue and gross margin in fiscal 2024?")
    assert a.text.startswith("Apple's fiscal 2024 revenue") and a.warnings == ()
    assert [c.name for c in a.tool_calls] == ["get_financial_metric", "compute_ratio"]
    assert a.usage.input_tokens == 200 and a.usage.cost_usd == pytest.approx(
        0.02
    )  # summed across steps
    assert a.prompt_version == AGENT_PROMPT_VERSION and a.model == "claude-opus-5"
    second = llm.requests[1]["messages"]
    assert (
        second[1]["role"] == "assistant" and second[1]["content"][0]["type"] == "tool_use"
    )  # echoed unchanged
    results = second[2]["content"]
    assert second[2]["role"] == "user" and [r["tool_use_id"] for r in results] == [
        "t1",
        "t2",
    ]  # ONE turn, in order
    assert all(r["type"] == "tool_result" and not r["is_error"] for r in results)
    assert llm.requests[0]["system"] == AGENT_SYSTEM and llm.requests[0]["tools"]


def test_a_failing_tool_is_reported_to_the_model_and_the_run_continues(ctx: AgentContext) -> None:
    llm = ScriptedAgentLLM(
        turn("", ToolUse("t1", "get_financial_metric", {"ticker": "ZZZ", "metric": "revenue"})),
        turn("INSUFFICIENT_EVIDENCE: that company is not covered."),
    )
    a = agent(ctx, llm).answer("What was ZZZ revenue in 2024?")
    result = llm.requests[1]["messages"][2]["content"][0]
    assert result["is_error"] is True and "unknown company" in result["content"]
    assert (
        a.tool_calls[0].is_error
        and a.abstained
        and a.abstain_reason == "agent_insufficient_evidence"
    )


def test_passages_get_citations_and_a_figure_not_in_any_tool_result_is_flagged(
    ctx: AgentContext,
) -> None:
    llm = ScriptedAgentLLM(
        turn(
            "",
            ToolUse(
                "t1",
                "search_filings",
                {
                    "query": "China mainland manufacturing",
                    "tickers": ["AAPL"],
                    "fiscal_years": [2024],
                },
            ),
        ),
        turn(
            "Apple relies on outsourcing partners in China mainland for manufacturing, about 90% of its output [S1]."
        ),
    )
    a = agent(ctx, llm).answer("What manufacturing risks does Apple describe?")
    assert [c.source_id for c in a.citations] == ["S1"] and a.citations[0].ticker == "AAPL"
    assert any("unverified figure: 90%" in w for w in a.warnings)  # invented statistic is caught


def test_invalid_label_and_uncited_claim_are_flagged(ctx: AgentContext) -> None:
    llm = ScriptedAgentLLM(
        turn("Apple manufactures most of its products through partners in Asia [S9].")
    )
    a = agent(ctx, llm).answer("Where does Apple manufacture products?")
    assert any("unknown source S9" in w for w in a.warnings)


def test_step_budget_forces_a_final_answer_without_tools(ctx: AgentContext) -> None:
    metric = ToolUse(
        "t", "get_financial_metric", {"ticker": "AAPL", "metric": "revenue", "fiscal_years": [2024]}
    )
    llm = ScriptedAgentLLM(
        turn("", metric),
        turn("", metric),
        turn("Apple's revenue was $391,035 million in fiscal 2024."),
    )
    a = agent(ctx, llm, max_steps=2).answer("What was Apple's revenue in fiscal 2024?")
    assert len(llm.requests) == 3 and llm.requests[2]["tools"] is None
    assert "used all tool steps" in llm.requests[2]["messages"][-1]["content"]
    assert "$391,035 million" in a.text and len(a.tool_calls) == 2


def test_advice_is_declined_before_the_loop(ctx: AgentContext) -> None:
    llm = ScriptedAgentLLM()
    a = agent(ctx, llm).answer("Is Apple a good investment?")
    assert a.abstained and a.abstain_reason == "out_of_scope" and llm.requests == []


def test_labels_do_not_leak_between_runs(ctx: AgentContext) -> None:
    search = ToolUse("t", "search_filings", {"query": "Azure cloud demand", "tickers": ["MSFT"]})
    for _ in range(2):
        llm = ScriptedAgentLLM(
            turn("", search), turn("Microsoft cloud revenue grew on Azure demand [S1].")
        )
        a = agent(ctx, llm).answer("What drove Microsoft cloud growth?")
        assert [c.source_id for c in a.citations] == ["S1"]  # each run starts again at S1


def test_empty_model_reply_is_treated_as_no_answer(ctx: AgentContext) -> None:
    a = agent(ctx, ScriptedAgentLLM(turn(""))).answer("What was Apple's revenue in fiscal 2024?")
    assert a.abstained
