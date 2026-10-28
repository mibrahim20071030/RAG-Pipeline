"""Layer 8 tests for rag.pipeline and the frozen setup. No paid call and no database: search and rerank are
replaced by fakes, and the answer model by a fake Converse client. The cost log goes to a temporary folder."""
import pytest

from rag import config, costlog, generate, load, pipeline, rerank, search

QUESTION = "What is the maximum supply voltage of the LM35?"
HITS = [
    {"id": 1, "document": "lm35", "page": 4, "page_label": "3", "section": "6.1", "kind": "table",
     "text": "Supply voltage: MAX: 35 V", "score": 0.03},
    {"id": 2, "document": "esp32_datasheet_en", "page": 51, "page_label": "51", "section": None, "kind": "text",
     "text": "VDD 3.3 V", "score": 0.02},
]


class FakeConverse:
    def __init__(self, text: str):
        self.text, self.calls = text, []

    def converse(self, **request):
        self.calls.append(request)
        return {"output": {"message": {"content": [{"text": self.text}]}}, "stopReason": "end_turn",
                "usage": {"inputTokens": 100, "outputTokens": 10}, "metrics": {"latencyMs": 5}}


@pytest.fixture
def steps(monkeypatch, tmp_path):
    """Fake search and rerank that record their arguments; the cost log in tmp_path."""
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(config, "CORRECTIONS_ENABLED", False)  # notes have their own tests (test_corrections.py)
    seen = {"order": []}

    def fake_search(conn, question, mode="hybrid", n=None, strategy=None, ts_config=None, document=None,
                    embed_fn=None):
        seen["order"].append("search")
        seen["search"] = {"question": question, "mode": mode, "strategy": strategy, "document": document}
        return [hit for hit in seen.get("hits", HITS) if document in (None, hit["document"])]

    def fake_rerank(conn, question, candidates, k=None, enabled=None, client=None, **kwargs):
        seen["order"].append("rerank")
        seen["rerank"] = {"candidates": candidates, "k": k, "enabled": enabled}
        return [{**hit, "search_score": hit["score"]} for hit in candidates]

    monkeypatch.setattr(search, "search", fake_search)
    monkeypatch.setattr(rerank, "rerank", fake_rerank)
    return seen


def test_frozen_setup():
    # Chosen at Layer 8 from the Layer 7 results; a change here must be a deliberate decision.
    assert config.DEFAULT_STRATEGY == "fixed_400_prefix"
    assert config.SEARCH_MODE == "hybrid"
    assert config.KEYWORD_CONFIG == "english"
    assert config.N_CANDIDATES == 30
    assert config.TOP_K == 10
    assert config.RERANK_ENABLED is True
    assert config.DETECT_PART is False
    assert config.DEFAULT_STRATEGY in load.strategy_names()


def test_steps_run_in_order_with_the_frozen_setup(steps):
    client = FakeConverse("The maximum supply voltage is 35 V [1].")
    pipeline.answer_question(None, QUESTION, generate_client=client)
    assert steps["order"] == ["search", "rerank"]
    assert steps["search"] == {"question": QUESTION, "mode": config.SEARCH_MODE, "strategy": None, "document": None}
    # None: rerank takes k and its switch from config when called, so the frozen TOP_K applies.
    assert steps["rerank"]["k"] is None and steps["rerank"]["enabled"] is None
    assert steps["rerank"]["candidates"] == HITS
    assert len(client.calls) == 1
    assert "Supply voltage: MAX: 35 V" in client.calls[0]["messages"][0]["content"][0]["text"]


def test_result_has_answer_sources_and_timings(steps):
    result = pipeline.answer_question(None, QUESTION, generate_client=FakeConverse("35 V [1]."))
    assert result["question"] == QUESTION
    assert result["answer"] == "35 V [1]."
    assert [(s["document"], s["page"]) for s in result["sources"]] == [("lm35", 4)]
    assert result["not_contained"] is False and result["invalid"] == []
    assert [hit["id"] for hit in result["passages"]] == [1, 2]
    assert set(result["timings_ms"]) == {"search", "rerank", "generate"}
    assert all(value >= 0 for value in result["timings_ms"].values())


def test_document_filter_is_passed_to_search(steps):
    result = pipeline.answer_question(None, QUESTION, document="lm35", generate_client=FakeConverse("35 V [1]."))
    assert steps["search"]["document"] == "lm35"
    assert [hit["document"] for hit in result["passages"]] == ["lm35"]


def test_no_hits_gives_not_contained_without_a_model_call(steps):
    steps["hits"] = []
    client = FakeConverse("should not be called")
    result = pipeline.answer_question(None, QUESTION, generate_client=client)
    assert client.calls == []
    assert result["answer"] == config.NOT_CONTAINED
    assert result["not_contained"] is True and result["sources"] == []
    # An empty search is flagged, so it is not mistaken for a question the datasheets cannot answer.
    assert "WARNING: The search found no chunks" in pipeline.format_answer(result)


def test_a_real_refusal_is_not_flagged_as_an_empty_search(steps):
    result = pipeline.answer_question(None, QUESTION, generate_client=FakeConverse(config.NOT_CONTAINED))
    assert result["not_contained"] is True
    assert "WARNING" not in pipeline.format_answer(result)


def test_unreadable_cost_log_does_not_stop_the_answer(steps, monkeypatch, capsys):
    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def locked():
        raise PermissionError("cost_log.csv is open in another program")

    monkeypatch.setattr(pipeline.db, "connect", FakeConn)
    monkeypatch.setattr(costlog, "read_rows", locked)
    real = pipeline.answer_question
    monkeypatch.setattr(pipeline, "answer_question",
                        lambda conn, question, document=None: real(conn, question, document,
                                                                   generate_client=FakeConverse("35 V [1].")))
    pipeline.main([QUESTION])
    out = capsys.readouterr().out
    assert "35 V [1]." in out and "Could not read the cost log" in out


def test_refusal_with_labels_shows_no_sources(steps):
    result = pipeline.answer_question(None, QUESTION, generate_client=FakeConverse(config.NOT_CONTAINED + " [1] [2]"))
    assert result["not_contained"] is True and result["sources"] == []
    assert "Sources: none" in pipeline.format_answer(result)


def test_format_answer_lists_document_and_page(steps):
    result = pipeline.answer_question(None, QUESTION, generate_client=FakeConverse("35 V [1]; ESP32 3.3 V [2]."))
    text = pipeline.format_answer(result)
    assert text.startswith("35 V [1]; ESP32 3.3 V [2].")
    assert "  [1] " + generate.passage_label(HITS[0]) in text
    assert "LM35 (lm35), page 4, printed page 3" in text
    assert "  [2] ESP32 (esp32_datasheet_en), page 51" in text
    assert "WARNING" not in text


def test_format_answer_warns_on_invalid_label_and_cut_off():
    result = {"answer": "35 V [7].", "sources": [], "invalid": [7], "cut_off": True}
    text = pipeline.format_answer(result)
    assert "Sources: none" in text
    assert "do not exist: [7]" in text
    assert f"cut off at {config.GENERATE_MAX_TOKENS} tokens" in text


def test_main_prints_answer_and_spend(steps, monkeypatch, capsys):
    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(pipeline.db, "connect", FakeConn)
    real = pipeline.answer_question
    monkeypatch.setattr(pipeline, "answer_question",
                        lambda conn, question, document=None: real(conn, question, document,
                                                                   generate_client=FakeConverse("35 V [1].")))
    pipeline.main([QUESTION])
    out = capsys.readouterr().out
    assert "35 V [1]." in out
    assert "[1] LM35 (lm35), page 4" in out
    # The fake answer logged 100 input and 10 output tokens to the temporary cost log.
    spent = 100 / 1000 * config.GENERATE_USD_PER_1K_INPUT + 10 / 1000 * config.GENERATE_USD_PER_1K_OUTPUT
    assert f"Spent: ${spent:.4f}" in out
