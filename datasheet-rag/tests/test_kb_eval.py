"""Cycle 2 step 8c tests for rag.eval.kb_eval: the KB run in run_eval's columns. No paid call and no database: the
Retrieve client and the answer model are fakes, and every output file is in tmp_path."""
import pytest

from rag import config
from rag.eval import kb_eval, run_eval

QUESTIONS = [
    {"id": "q01", "question": "LM35 supply voltage?", "sources": [("lm35", 4)], "type": "single_value"},
    {"id": "q02", "question": "ESP32 SRAM?", "sources": [("esp32_datasheet_en", 2)], "type": "table_lookup"},
    {"id": "q03", "question": "LM35 Bluetooth?", "sources": [], "type": "no_answer"},
]
# What the fake KB returns per question: (document, page) of each passage, best first.
RETRIEVED = {
    "LM35 supply voltage?": [("lm35", 1), ("lm35", 4)],  # correct at rank 2
    "ESP32 SRAM?": [("esp32_datasheet_en", 9)],  # missed
    "LM35 Bluetooth?": [("lm35", 1)],
}
REPLIES = {"LM35 supply voltage?": "4 V to 30 V [2].", "ESP32 SRAM?": config.NOT_CONTAINED,
           "LM35 Bluetooth?": config.NOT_CONTAINED}


class FakeKB:
    def __init__(self):
        self.calls = 0

    def retrieve(self, **request):
        self.calls += 1
        return {"retrievalResults": [
            {"content": {"text": f"text of {doc} page {page}"},
             "metadata": {"document": doc, "_excerpt_page_number": float(page), "_chunk_id": f"{doc}-{page}"},
             "score": 0.5}
            for doc, page in RETRIEVED[request["retrievalQuery"]["text"]]]}


class FakeLLM:
    def converse(self, **request):
        question = request["messages"][0]["content"][0]["text"].rsplit("Question: ", 1)[-1].strip()
        text = next(reply for q, reply in REPLIES.items() if q in question)
        return {"output": {"message": {"content": [{"text": text}]}}, "stopReason": "end_turn",
                "usage": {"inputTokens": 3000, "outputTokens": 20}, "metrics": {"latencyMs": 400}}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "KB_ID", "KBTEST1234")
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(config, "RESULTS", tmp_path / "results.csv")
    monkeypatch.setattr(config, "RESULTS_BY_TYPE", tmp_path / "results_by_type.csv")


def test_plan_counts_one_retrieve_and_one_answer_per_question():
    p = kb_eval.plan(49)
    assert p["retrieve_calls"] == p["generate_calls"] == 49
    assert p["est_cost_usd"]["retrieve"] == pytest.approx(49 * config.KB_USD_PER_RETRIEVE)
    assert p["est_cost_usd"]["total"] == pytest.approx(p["est_cost_usd"]["retrieve"] + p["est_cost_usd"]["generate"])


def test_run_gives_rows_and_one_summary_in_run_evals_columns():
    client = FakeKB()
    result = kb_eval.run(QUESTIONS, retrieve_client=client, generate_client=FakeLLM())
    assert client.calls == 3
    rows = {row["id"]: row for row in result["rows"]}
    assert rows["q01"]["rerank_rank"] == 2 and rows["q01"]["cited"] == ["lm35:4"]
    assert rows["q02"]["rerank_rank"] is None and rows["q02"]["not_contained"]
    assert rows["q01"]["search_hits"] == ["lm35:1", "lm35:4"]

    [summary] = result["summary"]
    assert set(summary) == set(run_eval.SUMMARY_FIELDS)
    assert summary["experiment"] == "kb_managed" and summary["rerank"] is True
    assert summary["answerable"] == 2
    assert summary["recall@1"] == 0.0 and summary["recall@3"] == 0.5
    assert summary["mrr@10"] == pytest.approx(0.25)
    assert summary["citation_hit_rate"] == 0.5
    assert summary["unanswerable_refused"] == 1.0 and summary["answerable_refused"] == 0.5
    expected = (config.KB_USD_PER_RETRIEVE + 3000 / 1000 * config.GENERATE_USD_PER_1K_INPUT
                + 20 / 1000 * config.GENERATE_USD_PER_1K_OUTPUT)
    assert summary["cost_per_query_usd"] == pytest.approx(expected)


def test_results_are_written_with_run_evals_writers(tmp_path):
    result = kb_eval.run(QUESTIONS, retrieve_client=FakeKB(), generate_client=FakeLLM())
    run_eval.write_results(result["summary"])
    run_eval.write_by_type(result)
    run_eval.write_rows(result["rows"], tmp_path / "kb_managed.csv")
    assert "kb_managed" in config.RESULTS.read_text(encoding="utf-8")
    by_type = config.RESULTS_BY_TYPE.read_text(encoding="utf-8")
    assert "kb_managed,True,single_value" in by_type and "no_answer" not in by_type
    assert "lm35:1; lm35:4" in (tmp_path / "kb_managed.csv").read_text(encoding="utf-8")
