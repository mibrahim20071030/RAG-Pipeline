"""Tests for scripts/rescore_refusals.py: saved answers re-checked with the current refusal rule, for free. Every file
is in tmp_path; nothing calls a model."""
import importlib.util
from pathlib import Path

import pytest

from rag import config
from rag.eval import deployed_eval
from rag.eval.run_eval import SUMMARY_FIELDS, _read_csv, _write_csv

_SPEC = importlib.util.spec_from_file_location(
    "rescore_refusals", Path(__file__).resolve().parent.parent / "scripts" / "rescore_refusals.py")
rescore = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rescore)

REWORDED = "Sorry, I cannot provide the unit price, as it is not mentioned in the provided datasheets."
QUESTIONS = [{"id": "q01", "sources": [("lm35", 4)], "type": "single_value", "question": "a"},
             {"id": "q44", "sources": [], "type": "no_answer", "question": "b"},
             {"id": "q45", "sources": [], "type": "no_answer", "question": "c"}]
FIELDS = ["id", "type", "answer", "cited", "not_contained"]


def eval_rows():
    return [
        {"id": "q01", "type": "single_value", "answer": "It is 35 V [1].", "cited": "lm35:4", "not_contained": "False"},
        {"id": "q44", "type": "no_answer", "answer": REWORDED, "cited": "", "not_contained": "False"},
        {"id": "q45", "type": "no_answer", "answer": config.NOT_CONTAINED, "cited": "", "not_contained": "True"},
    ]


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    eval_dir, deployed_dir = tmp_path / "eval", tmp_path / "deployed"
    eval_dir.mkdir()
    deployed_dir.mkdir()
    _write_csv(eval_dir / "gen_x.csv", FIELDS, eval_rows())
    summary = {field: "" for field in SUMMARY_FIELDS}
    _write_csv(tmp_path / "results.csv", SUMMARY_FIELDS, [
        {**summary, "experiment": "gen_x", "rerank": "False"},
        {**summary, "experiment": "gen_x", "rerank": "True", "unanswerable_refused": "0.5",
         "answerable_refused": "0.0", "citation_hit_rate": "1.0"},
    ])
    monkeypatch.setattr(config, "RESULTS", tmp_path / "results.csv")
    monkeypatch.setattr(config, "RESULTS_DEPLOYED", tmp_path / "results_deployed.csv")
    monkeypatch.setattr(config, "EVAL_DIR", eval_dir)
    monkeypatch.setattr(config, "DEPLOYED_DIR", deployed_dir)
    return eval_dir, deployed_dir


def test_dry_run_reports_the_reworded_refusal_and_writes_nothing(dirs):
    eval_dir, _ = dirs
    report = rescore.rescore(QUESTIONS, write=False)
    [entry] = report.values()
    assert entry["ids"] == ["q44"]
    assert entry["before"]["unanswerable_refused"] == 0.5 and entry["after"]["unanswerable_refused"] == 1.0
    assert _read_csv(eval_dir / "gen_x.csv")[1]["not_contained"] == "False"


def test_write_updates_the_rows_and_only_the_generating_summary_row(dirs):
    eval_dir, _ = dirs
    rescore.rescore(QUESTIONS, write=True)
    assert [r["not_contained"] for r in _read_csv(eval_dir / "gen_x.csv")] == ["False", "True", "True"]
    search_row, generated_row = _read_csv(config.RESULTS)
    assert float(generated_row["unanswerable_refused"]) == 1.0
    assert generated_row["citation_hit_rate"] == "1.0"  # untouched
    assert search_row["unanswerable_refused"] == ""


def test_a_second_run_finds_nothing(dirs):
    rescore.rescore(QUESTIONS, write=True)
    assert rescore.rescore(QUESTIONS, write=True) == {}


def test_a_cited_answer_never_becomes_a_refusal():
    rows = [{"id": "q01", "answer": f"{REWORDED} It is 35 V [1].", "cited": "lm35:4", "not_contained": "False"}]
    assert rescore.rescore_rows(rows) == [] and rows[0]["not_contained"] == "False"


def test_an_old_label_only_refusal_that_kept_its_pages_is_left_alone():
    # Layer 7 rows of the "sentence, then only labels" kind kept the label pages in `cited`.
    rows = [{"id": "q42", "answer": f"{config.NOT_CONTAINED} [1] [3]", "cited": "lm35:13; lm35:4",
             "not_contained": "True"}]
    assert rescore.rescore_rows(rows) == []


def test_results_file_keeps_its_own_columns(dirs, tmp_path):
    rows = _read_csv(config.RESULTS)
    _write_csv(config.RESULTS, [*rows[0], "extra"], [{**row, "extra": "kept"} for row in rows])
    rescore.rescore(QUESTIONS, write=True)
    assert [row["extra"] for row in _read_csv(config.RESULTS)] == ["kept", "kept"]


def test_deployed_summary_is_rebuilt_with_deployed_evals_code(dirs):
    _, deployed_dir = dirs
    base = {"type": "no_answer", "pass": "first", "status": "200", "attempts": "1", "client_ms": "900"}
    _write_csv(deployed_dir / "first.csv", deployed_eval.ROW_FIELDS, [
        {**base, "id": "q01", "type": "single_value", "answer": "It is 35 V [1].", "cited": "lm35:4",
         "not_contained": "False"},
        {**base, "id": "q44", "answer": REWORDED, "cited": "", "not_contained": "False"},
        {**base, "id": "q45", "answer": config.NOT_CONTAINED, "cited": "", "not_contained": "True"},
    ])
    _write_csv(config.RESULTS_DEPLOYED, deployed_eval.SUMMARY_FIELDS,
               [{"pass": "first", "unanswerable_refused": "0.5", "aurora_usd_per_query": "0.0019"}])
    report = rescore.rescore(QUESTIONS, write=True)
    assert any(r["deployed"] and r["ids"] == ["q44"] for r in report.values())
    first = next(row for row in _read_csv(config.RESULTS_DEPLOYED) if row["pass"] == "first")
    assert float(first["unanswerable_refused"]) == 1.0 and float(first["citation_hit_rate"]) == 1.0
    assert first["aurora_usd_per_query"] == "0.0019"  # read by hand from the console, kept


def test_local_rates_count_every_answered_question():
    rows = [{"id": "q44", "answer": config.NOT_CONTAINED, "cited": "", "not_contained": "True"},
            {"id": "q01", "answer": "It is 35 V [1].", "cited": "lm35:4", "not_contained": "False"}]
    rates = rescore.local_rates(rows, {q["id"]: q["sources"] for q in QUESTIONS})
    assert rates == {"unanswerable_refused": 1.0, "answerable_refused": 0.0}
