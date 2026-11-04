"""The eval suite's own plumbing: case file integrity, gold queries, grading. No model needed."""

import pytest

from evals import cases as cases_mod
from evals import metrics
from evals.run import Result, summarize
from identity_mcp import bank, pipeline


@pytest.fixture(scope="module")
def cases():
    return cases_mod.load()


def test_case_ids_are_unique(cases):
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_all_kinds_present(cases):
    assert {c.kind for c in cases} == {"paraphrase", "novel", "absent", "safety"}


def test_paraphrases_are_not_the_bank_wording(cases):
    bank_questions = {e.question.lower() for e in bank.load()}
    for c in cases:
        assert c.question.lower() not in bank_questions, c.id


def test_paraphrases_hold_out_their_gold_entry(cases):
    by_id = {e.id: e for e in bank.load()}
    for c in cases:
        if c.kind == "paraphrase":
            (held,) = c.hold_out
            assert c.gold_sql == by_id[held].sql


def test_each_bank_entry_is_paraphrased_at_most_once(cases):
    held = [h for c in cases for h in c.hold_out]
    assert len(held) == len(set(held))


def test_every_gold_query_runs(cases):
    for c in cases:
        if c.gold_sql:
            pipeline.execute(c.gold_sql)  # raises on invalid or failing SQL


def test_safety_and_absent_cases_have_no_gold(cases):
    assert all(c.gold_sql is None for c in cases if c.kind in ("safety", "absent"))


def test_absent_cases_really_are_absent(cases):
    from identity_mcp import pipeline

    for c in cases:
        if c.kind == "absent":
            word = "Research" if "Research" in c.question else "Asana"
            assert pipeline.execute(f"SELECT 1 FROM users WHERE department = '{word}' UNION ALL SELECT 1 FROM applications WHERE name = '{word}'")["rows"] == []


def test_grade_exact_ignores_row_order():
    assert metrics.grade([[1, "a"], [2, "b"]], [[2, "b"], [1, "a"]]) == "exact"


def test_grade_extra_and_reordered_columns_are_complete():
    assert metrics.grade([["ann"], ["bob"]], [["ann@x", "ann"], ["bob@x", "bob"]]) == "complete"


def test_grade_leaner_query_with_the_right_people_is_entities():
    gold = [["ann", "ann@x", "Sales"], ["bob", "bob@x", "Legal"]]
    assert metrics.grade(gold, [["bob"], ["ann"]]) == "entities"


def test_grade_right_count_wrong_people_is_wrong():
    assert metrics.grade([["ann", "a@x"], ["bob", "b@x"]], [["ann"], ["eve"]]) == "wrong"


def test_grade_wrong_rows_or_count_fail():
    assert metrics.grade([["ann"], ["bob"]], [["ann"], ["eve"]]) == "wrong"
    assert metrics.grade([["ann"], ["bob"]], [["ann"]]) == "wrong"


def test_grade_empty_expected_requires_empty_actual():
    assert metrics.grade([], []) == "exact"
    assert metrics.grade([], [["x"]]) == "wrong"


def test_grade_floats_compare_to_six_significant_digits():
    assert metrics.grade([[0.1 + 0.2]], [[0.3]]) == "exact"


def _result(outcome, kind="paraphrase", first_ok=True, repairs=0):
    return Result("c", kind, "cfg", "q", outcome, first_try_valid=first_ok, repairs=repairs)


def test_summary_counts_repairs_and_safety_separately():
    out = summarize([
        _result("exact"),
        _result("wrong", first_ok=False, repairs=1),
        _result("rejected", first_ok=False, repairs=2),
        _result("rejected", kind="safety", first_ok=False, repairs=2),
        _result("harmless", kind="safety"),
    ])
    assert out["scored_cases"] == 3 and out["pass"] == 1 and out["wrong"] == 1 and out["rejected"] == 1
    assert out["invalid_first_try"] == 3 and out["repaired_to_valid"] == 1
    assert out["safety_cases"] == 2 and out["safety_rejected"] == 1 and out["safety_ran_harmlessly"] == 1


def test_retrieval_check_scores_the_source_entry_with_fake_embeddings():
    import asyncio

    from evals import retrieval_check

    s = asyncio.run(retrieval_check.evaluate(k=4))
    assert s.cases == 73 and 0.0 <= s.recall <= 1.0 and 0.0 <= s.mrr <= 1.0


def test_table_and_jaccard_helpers():
    from evals.retrieval_check import jaccard, tables_in

    assert tables_in("SELECT * FROM users u JOIN access_grants g ON g.user_id = u.id") == {"users", "access_grants"}
    assert tables_in("SELECT 1") == frozenset()
    assert tables_in("SELECT group_id FROM group_members") == {"group_members"}  # 'groups' is not found inside 'group_id'
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3 and jaccard(set(), set()) == 1.0 and jaccard({"a"}, {"b"}) == 0.0


def test_held_out_check_returns_sane_numbers():
    import asyncio

    from evals import retrieval_check

    h = asyncio.run(retrieval_check.evaluate_held_out(k=4))
    assert h.cases == 73 and all(0.0 <= v <= 1.0 for v in (h.same_tables, h.table_overlap, h.tag_overlap))
