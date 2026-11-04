"""Answer verification: the checks as plain functions, and the graph sending a wrong answer back once."""

import asyncio
import json

from langchain_core.messages import HumanMessage, ToolMessage

from identity_mcp.agent import graph as agent_graph
from identity_mcp.agent import verify
from test_graph import ScriptedModel, call, final


def result(rows, columns=("id", "name"), **extra):
    return {"scope": "identity", "columns": list(columns), "rows": rows, "row_count": len(rows), "truncated": False, **extra}


GROUPS = result([[1, "All Employees"], [2, "Engineering"], [3, "Sales"], [4, "Marketing"], [5, "Finance"], [6, "Legal"]])


def test_numbers_ignore_dates_and_list_numbering():
    assert verify.numbers_in("1. Ava\n2. Bo\nSince 2026-11-01 there were 1,200 events (3.5 avg)") == {"1200", "3.5"}


def test_a_correct_answer_has_no_problems():
    answer = "Elena is in 6 groups:\n- All Employees\n- Engineering\n- Sales\n- Marketing\n- Finance\n- Legal"
    assert verify.verify_answer(answer, [GROUPS]) == []


def test_a_total_that_is_not_in_the_data_is_reported():
    problems = verify.verify_answer("There are 99 groups.", [GROUPS])
    assert len(problems) == 1 and "99" in problems[0]


def test_counts_from_the_question_the_row_count_and_distinct_counts_are_allowed():
    assert verify.verify_answer("No activity in the last 90 days: 6 groups.", [GROUPS], asked="no activity in the last 90 days") == []
    names = result([[1, "Ava"], [2, "Ava"], [3, "Bo"]])  # three rows, two different names
    assert verify.verify_answer("3 rows, 2 different people", [names]) == []


def test_an_invented_list_item_is_reported_but_sentences_are_not():
    answer = "Groups:\n- Engineering\n- Quantum Council\n- Results may be incomplete because the list was cut off."
    problems = verify.verify_answer(answer, [GROUPS])
    assert len(problems) == 1 and "Quantum Council" in problems[0]


def test_a_list_that_loses_rows_is_reported():
    answer = "She is in: All Employees, Engineering, Sales and Marketing."
    problems = verify.verify_answer(answer, [GROUPS])
    assert len(problems) == 1 and "2 of the 6" in problems[0] and "Finance" in problems[0] and "Legal" in problems[0]


def test_a_summary_that_names_none_or_few_is_not_flagged():
    assert verify.verify_answer("She is in 6 groups.", [GROUPS]) == []
    assert verify.verify_answer("She is in 6 groups, including Sales.", [GROUPS]) == []  # fewer than half named: a summary


def test_a_cut_off_result_is_not_checked_for_omissions():
    cut = result([[1, "A one"], [2, "B two"]], row_count=40, truncated=True)
    assert verify.verify_answer("A one and B two, plus others.", [cut]) == []


def test_without_results_there_is_nothing_to_check():
    assert verify.verify_answer("Anything 123", []) == []


def test_results_of_skips_errors_and_text():
    good = ToolMessage(json.dumps(GROUPS), tool_call_id="1", name="t")
    bad = ToolMessage(json.dumps({"error": "boom", "rows": []}), tool_call_id="2", name="t")
    text = ToolMessage("Error: boom", tool_call_id="3", name="t")
    assert verify.results_of([HumanMessage("q"), good, bad, text]) == [GROUPS]


# ---- in the graph ---------------------------------------------------------------------------------------------------

def group_tools():
    from langchain_core.tools import StructuredTool

    async def run_tool(**kwargs):
        return json.dumps(GROUPS)
    return [StructuredTool(name="query_identity", description="x", coroutine=run_tool, args_schema={
        "type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]})]


def run_graph(replies):
    model = ScriptedModel(replies=replies)
    graph = agent_graph.build_graph(model, group_tools(), 8)
    nodes = []
    answer = asyncio.run(agent_graph.run(graph, "what groups is she in?", lambda node, update: nodes.append(node)))
    return answer, nodes, model


def test_a_wrong_answer_is_sent_back_once_and_the_fixed_answer_is_used():
    full = "She is in All Employees, Engineering, Sales, Marketing, Finance and Legal."
    answer, nodes, model = run_graph([call("query_identity", question="groups"), final("She is in All Employees, Engineering, Sales and Marketing."), final(full)])
    assert answer == full
    assert nodes.count("revise") == 1 and nodes[-1] == "verify"
    shown = model.seen[-1][-1]
    assert "Finance" in shown.content and "final_answer" in shown.content  # the model was told exactly what it left out


def test_an_answer_that_stays_wrong_goes_out_with_a_caveat():
    wrong = "She is in All Employees, Engineering, Sales and Marketing."
    answer, nodes, _ = run_graph([call("query_identity", question="groups"), final(wrong), final(wrong)])
    assert answer.startswith(wrong) and "could not be fully checked" in answer and "Finance" in answer
    assert nodes.count("revise") == 1  # one chance, not a loop


def test_a_correct_answer_passes_straight_through():
    answer, nodes, _ = run_graph([call("query_identity", question="groups"), final("She is in 6 groups.")])
    assert answer == "She is in 6 groups." and "revise" not in nodes
