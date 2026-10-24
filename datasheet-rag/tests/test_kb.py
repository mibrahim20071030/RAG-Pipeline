"""Cycle 2 step 8 tests for rag.kb: Managed Knowledge Base results in our hit shape, and the KB pipeline's answer.

No paid call and no database: the Retrieve client and the answer model are fakes, and the cost log is in tmp_path.
The result shape copies what the probe got back on 2026-10-23 (runs/kb_setup.md).
"""
import ast
from pathlib import Path

import pytest

from rag import config, costlog, kb, pipeline

URI = "https://datasheet-rag-kb-example.s3.amazonaws.com/kb/lm35.pdf"


def kb_result(text="Supply voltage: 4 V to 30 V", page=4.0, document="lm35", table=False, score=0.55, **meta):
    """One Retrieve result as the Managed KB returns it."""
    metadata = {"_source_uri": URI, "_chunk_id": f"chunk-{page}-{text[:5]}", "_excerpt_page_number": page,
                "_file_type": "PDF", "document": document, "part": "LM35", **meta}
    if table:
        metadata.update({"_table_id": "t1", "_table_section_title": "6.3 Recommended Operating Conditions"})
    if page is None:
        del metadata["_excerpt_page_number"]
    if document is None:
        del metadata["document"]
    return {"content": {"type": "TEXT", "text": text}, "location": {"type": "S3", "s3Location": {"uri": URI}},
            "metadata": metadata, "score": score}


class FakeKB:
    """The Retrieve call of the agent runtime client, answering with fixed results."""

    def __init__(self, results=None, error=None):
        self.results = [kb_result()] if results is None else results
        self.error, self.requests = error, []

    def retrieve(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return {"retrievalResults": self.results}


class FakeLLM:
    """The Converse call of the runtime client, with a fixed reply."""

    def __init__(self, text="It is 4 V to 30 V [1]."):
        self.text, self.requests = text, []

    def converse(self, **request):
        self.requests.append(request)
        return {"output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
                "stopReason": "end_turn", "usage": {"inputTokens": 900, "outputTokens": 20},
                "metrics": {"latencyMs": 500}}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "KB_ID", "KBTEST1234")
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")


# ---------------------------------------------------------------- results as hits

def test_result_maps_to_the_hit_shape():
    hit = kb.to_hit(kb_result())
    assert hit == {"id": "chunk-4.0-Suppl", "document": "lm35", "page": 4, "page_label": None, "section": None,
                   "kind": "text", "text": "Supply voltage: 4 V to 30 V", "score": 0.55}
    assert isinstance(hit["page"], int)  # AWS sends 4.0


def test_table_chunk_keeps_its_section():
    hit = kb.to_hit(kb_result(table=True))
    assert hit["kind"] == "table" and hit["section"] == "6.3 Recommended Operating Conditions"


def test_document_falls_back_to_the_file_name():
    assert kb.to_hit(kb_result(document=None))["document"] == "lm35"


def test_document_from_the_location_when_no_source_uri():
    result = kb_result(document=None)
    del result["metadata"]["_source_uri"]
    assert kb.to_hit(result)["document"] == "lm35"


def test_missing_page_is_kept_as_none():
    hit = kb.to_hit(kb_result(page=None))
    assert hit["page"] is None and hit["text"]


def test_no_metadata_at_all():
    hit = kb.to_hit({"content": {"text": "x"}})
    assert hit["document"] is None and hit["page"] is None and hit["text"] == "x"


# ---------------------------------------------------------------- the Retrieve request

def test_request_asks_for_top_k_with_the_managed_reranker():
    client = FakeKB()
    kb.retrieve("What is the supply voltage?", client=client)
    request = client.requests[0]
    assert request["knowledgeBaseId"] == "KBTEST1234"
    assert request["retrievalQuery"] == {"text": "What is the supply voltage?"}
    managed = request["retrievalConfiguration"]["managedSearchConfiguration"]
    assert managed == {"numberOfResults": config.TOP_K, "rerankingModelType": "MANAGED"}


def test_document_filter_only_when_a_document_is_given():
    client = FakeKB()
    kb.retrieve("q", document="lm35", k=5, client=client)
    managed = client.requests[0]["retrievalConfiguration"]["managedSearchConfiguration"]
    assert managed["filter"] == {"equals": {"key": "document", "value": "lm35"}}
    assert managed["numberOfResults"] == 5


def test_results_keep_the_kb_order():
    client = FakeKB([kb_result("a", 9.0, score=0.9), kb_result("b", 2.0, score=0.4)])
    assert [hit["page"] for hit in kb.retrieve("q", client=client)] == [9, 2]


def test_each_call_logs_one_cost_row():
    kb.retrieve("q", client=FakeKB())
    kb.retrieve("q", client=FakeKB([]))
    rows = costlog.read_rows()
    assert [(r["operation"], r["unit"], r["quantity"]) for r in rows] == [("kb_retrieve", "query", "1")] * 2
    assert sum(float(r["est_cost_usd"]) for r in rows) == pytest.approx(2 * config.KB_USD_PER_RETRIEVE)


def test_no_kb_id_raises_before_any_call(monkeypatch):
    monkeypatch.setattr(config, "KB_ID", "")
    client = FakeKB()
    with pytest.raises(RuntimeError, match="KB_ID"):
        kb.retrieve("q", client=client)
    assert client.requests == []


# ---------------------------------------------------------------- the answer

def test_answer_has_the_pipeline_shape():
    result = kb.answer_question("What is the supply voltage?", retrieve_client=FakeKB(), generate_client=FakeLLM())
    assert result["answer"] == "It is 4 V to 30 V [1]."
    assert result["sources"] == [{"label": 1, "id": "chunk-4.0-Suppl", "document": "lm35", "page": 4,
                                  "page_label": None}]
    assert set(result["timings_ms"]) == {"retrieve", "generate"}
    assert result["passages"][0]["page"] == 4 and not result["not_contained"]
    assert kb.warnings(result) == []
    assert "LM35 (lm35), page 4" in pipeline.format_answer(result, warnings_fn=kb.warnings)


def test_no_passages_gives_the_sentence_without_a_model_call():
    llm = FakeLLM()
    result = kb.answer_question("q", retrieve_client=FakeKB([]), generate_client=llm)
    assert result["not_contained"] and llm.requests == []
    assert kb.warnings(result) == ["The Knowledge Base returned no passages. Check that its data source is synced."]


def test_warns_about_passages_without_a_page():
    result = kb.answer_question("q", retrieve_client=FakeKB([kb_result(page=None), kb_result()]),
                                generate_client=FakeLLM("It is 4 V to 30 V [2] [7]."))
    found = kb.warnings(result)
    assert any("1 passage(s) came back without a datasheet or page" in w for w in found)
    assert any("do not exist: [7]" in w for w in found)


def test_kb_never_imports_the_database_module():
    tree = ast.parse(Path(kb.__file__).read_text(encoding="utf-8"))
    names = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
             for alias in node.names}
    assert "db" not in names and "psycopg" not in names


def test_passage_without_a_datasheet_or_page_does_not_crash_the_answer():
    # Review fix: generate.passage_label used to raise TypeError on document None.
    result = {"content": {"text": "orphan"}, "metadata": {"_chunk_id": "x"}}
    llm = FakeLLM("It is 4 V to 30 V [2].")
    answer = kb.answer_question("q", retrieve_client=FakeKB([result, kb_result()]), generate_client=llm)
    prompt = llm.requests[0]["messages"][0]["content"][0]["text"]
    assert "[1] unknown datasheet, page unknown" in prompt
    assert answer["sources"][0]["page"] == 4
