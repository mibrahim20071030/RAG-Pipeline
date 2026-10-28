"""Cycle 2 step 9b tests for rag.eval.correction_eval: the case file and the scoring. No paid call, no database."""
import pytest

from rag import config
from rag.eval import correction_eval
from rag.eval.questions import load_questions


def test_the_case_file_is_valid_and_fixes_frozen_questions():
    cases = correction_eval.load_cases()
    frozen = {q["id"]: q for q in load_questions()}
    assert len({case["id"] for case in cases}) == len(cases) == 12
    for case in cases:
        assert case["fixes"] in frozen
        assert all(document in config.DOCUMENTS for document, _ in case["sources"])
        if case["kind"] == "original":
            assert case["question"] == frozen[case["fixes"]]["question"]
            assert case["sources"] == frozen[case["fixes"]]["sources"]
    by_fix = {}
    for case in cases:
        by_fix.setdefault(case["fixes"], []).append(case["kind"])
    assert all(sorted(kinds) == ["negative", "original", "paraphrase", "paraphrase"] for kinds in by_fix.values())


def test_a_bad_case_is_refused(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id": "x", "fixes": "q08", "kind": "maybe", "question": "q", "must_contain": [], '
                    '"sources": []}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        correction_eval.load_cases(path)


CASE = {"id": "c", "fixes": "q08", "kind": "original", "question": "q", "must_contain": ["±0.5°C", "±1°C"],
        "sources": [("lm35", 5)]}


def answer(text, cited=(("lm35", 5),), notes=("n1",), refused=False):
    return {"answer": text, "not_contained": refused,
            "sources": [{"document": d, "page": p} for d, p in cited],
            "passages": [{"correction_id": n} for n in notes]}


def test_an_answer_passes_with_the_values_and_a_cited_page():
    row = correction_eval.score(CASE, answer("LM35A ±0.5 °C, LM35 ± 1 °C [1]"), "ours", "after")
    assert row["passed"] and row["notes_used"] == "n1" and row["cited"] == "lm35:5"
    assert not correction_eval.score(CASE, answer("LM35A ±0.5 °C [1]"), "ours", "after")["passed"]
    assert not correction_eval.score(CASE, answer("±0.5 °C ±1 °C", cited=[("lm35", 9)]), "ours", "after")["passed"]


def test_a_negative_passes_only_without_a_note():
    negative = {**CASE, "kind": "negative", "must_contain": []}
    assert correction_eval.score(negative, answer("56 µA", notes=()), "kb", "after")["passed"]
    assert not correction_eval.score(negative, answer("56 µA"), "kb", "after")["passed"]


def test_summary_counts_by_pipeline_state_and_kind():
    rows = [correction_eval.score(CASE, answer("±0.5°C ±1°C"), "ours", "after"),
            correction_eval.score(CASE, answer("no", refused=True, notes=()), "ours", "before")]
    summary = correction_eval.summarise(rows)
    assert summary == [
        {"pipeline": "ours", "state": "after", "kind": "original", "cases": 1, "passed": 1, "notes_used": 1,
         "refused": 0},
        {"pipeline": "ours", "state": "before", "kind": "original", "cases": 1, "passed": 0, "notes_used": 0,
         "refused": 1}]


def test_the_summary_keeps_a_state_run_on_another_day(tmp_path):
    def row(state, passed):
        return {"id": "c08a", "fixes": "q08", "kind": "original", "pipeline": "ours", "state": state,
                "notes_used": "n1" if passed else "", "contains_all": passed, "cites_source": passed,
                "not_contained": not passed, "passed": passed, "cited": "", "answer": "x"}

    correction_eval._write(tmp_path / "ours_before.csv", [row("before", False)])
    correction_eval._write(tmp_path / "ours_after.csv", [row("after", True)])
    summary = correction_eval.summarise(correction_eval.saved_rows(tmp_path))
    assert [(r["state"], r["passed"], r["refused"]) for r in summary] == [("after", 1, 0), ("before", 0, 1)]
