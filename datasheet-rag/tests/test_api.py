"""Layer 8b tests for rag.api: the web page's JSON, without HTTP. No paid call and no database: the answer step
and the document list are fakes, and PDF_DIR is a temporary folder."""
import psycopg
import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from rag import api, config, db

DOCS = [
    {"id": 1, "name": "esp32_datasheet_en", "file": "esp32_datasheet_en.pdf", "part": "ESP32", "revision": "Version 5.3"},
    {"id": 2, "name": "lm35", "file": "lm35.pdf", "part": "LM35", "revision": "SNIS159H"},
]


def result(**fields) -> dict:
    """What pipeline.answer_question returns, for an answer citing LM35 page 4 (printed page 3)."""
    base = {
        "question": "q", "answer": "35 V [1].", "not_contained": False, "invalid": [], "cut_off": False,
        "sources": [{"label": 1, "id": 7, "document": "lm35", "page": 4, "page_label": "3"}],
        "passages": [{"id": 7, "document": "lm35", "page": 4, "text": "Supply voltage: MAX: 35 V"}],
        "timings_ms": {"search": 40.0, "rerank": 3.0, "generate": 900.0},
    }
    return {**base, **fields}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    """Documents from a fake list; only lm35.pdf exists on disk; the cost log in tmp_path."""
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "lm35.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(config, "PDF_DIR", pdfs)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(db, "list_documents", lambda conn: DOCS)


def fake_answer(returned=None, raises=None, seen=None):
    def answer(conn, question, document=None):
        if seen is not None:
            seen.update(question=question, document=document)
        if raises:
            raise raises
        return returned or result()
    return answer


# ---------------------------------------------------------------- documents

def test_documents_for_the_filter():
    docs = api.documents(None)
    assert [d["name"] for d in docs] == ["esp32_datasheet_en", "lm35"]
    assert docs[1] == {"name": "lm35", "part": "LM35", "revision": "SNIS159H", "pdf_url": "/pdfs/lm35.pdf"}
    assert docs[0]["pdf_url"] is None  # its PDF is not on this machine


# ---------------------------------------------------------------- a good answer

def test_answer_shape():
    status, body = api.ask(None, {"question": "  What is the max supply voltage?  "}, fake_answer())
    assert status == 200
    assert body["question"] == "What is the max supply voltage?"
    assert body["answer"] == "35 V [1]."
    assert body["not_contained"] is False
    assert body["warnings"] == []
    assert body["timings_ms"] == {"search": 40.0, "rerank": 3.0, "generate": 900.0}
    assert body["setup"]["strategy"] == config.DEFAULT_STRATEGY and body["setup"]["passages"] == config.TOP_K
    assert body["sources"] == [{
        "label": 1, "document": "lm35", "part": "LM35", "page": 4, "page_label": "3",
        "title": "LM35 (lm35), page 4, printed page 3", "pdf_url": "/pdfs/lm35.pdf#page=4",
    }]


def test_source_without_a_local_pdf_has_no_link():
    answer = result(sources=[{"label": 1, "id": 9, "document": "esp32_datasheet_en", "page": 51, "page_label": "51"}])
    status, body = api.ask(None, {"question": "q"}, fake_answer(answer))
    assert status == 200 and body["sources"][0]["pdf_url"] is None


def test_question_is_trimmed_and_document_passed_through():
    seen = {}
    api.ask(None, {"question": " q ", "document": "lm35"}, fake_answer(seen=seen))
    assert seen == {"question": "q", "document": "lm35"}


def test_empty_document_means_all():
    seen = {}
    api.ask(None, {"question": "q", "document": ""}, fake_answer(seen=seen))
    assert seen["document"] is None


def test_not_contained_and_warnings_are_passed_on():
    answer = result(answer=config.NOT_CONTAINED, not_contained=True, sources=[], passages=[])
    status, body = api.ask(None, {"question": "q"}, fake_answer(answer))
    assert status == 200 and body["not_contained"] is True and body["sources"] == []
    assert len(body["warnings"]) == 1 and "no chunks" in body["warnings"][0]


def test_spend_is_read_from_the_cost_log():
    from rag.costlog import log_cost

    def answer(conn, question, document=None):
        log_cost("generate", "m", "input_tokens", 100, 0.0006)
        return result()

    _, body = api.ask(None, {"question": "q"}, answer)
    assert body["spent_usd"] == pytest.approx(0.0006)


def test_answer_parts_mark_the_citations():
    _, body = api.ask(None, {"question": "q"}, fake_answer())
    assert body["answer_parts"] == [{"text": "35 V "}, {"text": "[1]", "labels": [1]}, {"text": "."}]
    assert "".join(part["text"] for part in body["answer_parts"]) == body["answer"]


def test_answer_parts_leave_footnotes_and_invalid_labels_as_text():
    passages = [{"id": 1, "document": "esp32_datasheet_en", "page": 9, "text": "Pin GPIO16[2] is used for PSRAM."},
                {"id": 2, "document": "esp32_datasheet_en", "page": 10, "text": "Other pins."}]
    answer = "GPIO16[2] drives PSRAM [1]; see also [9] and [1-2]."
    parts = api.answer_parts(answer, passages)
    assert [part for part in parts if "labels" in part] == [{"text": "[1]", "labels": [1]},
                                                            {"text": "[1-2]", "labels": [1, 2]}]
    assert "".join(part["text"] for part in parts) == answer


def test_answer_parts_of_plain_text():
    assert api.answer_parts("No citation here.", []) == [{"text": "No citation here."}]
    assert api.answer_parts("", []) == []


# ---------------------------------------------------------------- bad requests

@pytest.mark.parametrize("document", [["lm35"], {"name": "lm35"}, 5, False, 0])
def test_document_that_is_not_text(document):
    status, body = api.ask(None, {"question": "q", "document": document},
                           fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and "datasheet" in body["error"]


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "   "}, {"question": 5}, [], "q", None])
def test_no_question(payload):
    status, body = api.ask(None, payload, fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and body["error"]


def test_question_too_long():
    status, body = api.ask(None, {"question": "x" * (config.API_MAX_QUESTION_CHARS + 1)},
                           fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and str(config.API_MAX_QUESTION_CHARS) in body["error"]


def test_unknown_document():
    status, body = api.ask(None, {"question": "q", "document": "lm358"},
                           fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and "datasheet" in body["error"]


# ---------------------------------------------------------------- failures of the steps

def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "no"}}, "Rerank")


def test_throttling_is_429():
    status, body = api.ask(None, {"question": "q"}, fake_answer(raises=client_error("ThrottlingException")))
    assert status == 429 and "minute" in body["error"]


def test_access_denied_is_502_with_the_permission():
    status, body = api.ask(None, {"question": "q"}, fake_answer(raises=client_error("AccessDeniedException")))
    assert status == 502 and "bedrock:Rerank" in body["error"]


def test_other_aws_error_names_its_code():
    status, body = api.ask(None, {"question": "q"}, fake_answer(raises=client_error("ValidationException")))
    assert status == 502 and "ValidationException" in body["error"]


def test_no_credentials():
    status, body = api.ask(None, {"question": "q"}, fake_answer(raises=NoCredentialsError()))
    assert status == 502 and "credentials" in body["error"]


def test_step_runtime_error():
    status, body = api.ask(None, {"question": "q"}, fake_answer(raises=RuntimeError("the model stopped")))
    assert status == 502 and "the model stopped" in body["error"]


def test_database_lost(monkeypatch):
    def down(conn):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(db, "list_documents", down)
    status, body = api.ask(None, {"question": "q"}, fake_answer())
    assert status == 503 and "docker start rag-pg" in body["error"]


def test_database_lost_during_the_answer():
    status, _ = api.ask(None, {"question": "q"}, fake_answer(raises=psycopg.OperationalError("gone")))
    assert status == 503
