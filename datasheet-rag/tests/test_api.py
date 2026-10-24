"""Layer 8b and Cycle 2 step 7b tests for rag.api: the web page's JSON, without HTTP. No paid call, no database
and no DynamoDB: the answer step, the document list, the connection and the daily counter are fakes, and PDF_DIR is
a temporary folder."""
import contextlib
from datetime import date

import psycopg
import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from rag import api, config, costlog, db, uploads

DOCS = [
    {"id": 1, "name": "esp32_datasheet_en", "file": "esp32_datasheet_en.pdf", "part": "ESP32", "revision": "Version 5.3"},
    {"id": 2, "name": "lm35", "file": "lm35.pdf", "part": "LM35", "revision": "SNIS159H"},
]
REAL_DOCUMENTS = config.DOCUMENTS  # kept before the setup fixture replaces it
# The vendors' addresses, as in config.DOCUMENTS; the STM32 one is None, as for a revision the vendor no longer has.
VENDOR = {
    "esp32_datasheet_en": {"part": "ESP32", "revision": "Version 5.3", "url": "https://vendor.example/esp32.pdf"},
    "lm35": {"part": "LM35", "revision": "SNIS159H", "url": "https://vendor.example/lm35.pdf"},
    "DS_CD00237391": {"part": "STM32F205/207", "revision": "DS6329 Rev 18", "url": None},
}


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
    """Documents from a fake list; only lm35.pdf exists on disk; vendor addresses from VENDOR; the cost log in
    tmp_path."""
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "lm35.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(config, "PDF_DIR", pdfs)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(db, "list_documents", lambda conn: DOCS)
    monkeypatch.setattr(db, "delete_expired_uploads", lambda conn: 0)
    monkeypatch.setattr(config, "DOCUMENTS", VENDOR)


class Opener:
    """The `open_conn` of api.ask: counts how often a connection was asked for, or fails like a sleeping database."""

    def __init__(self, error=None):
        self.opened, self.error = 0, error

    def __call__(self):
        self.opened += 1
        if self.error:
            raise self.error
        return contextlib.nullcontext(None)


def ask(payload, answer_fn, counter=None, opener=None):
    return api.ask(payload, opener or Opener(), answer_fn, counter)


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
    assert docs[0]["pdf_url"] == "https://vendor.example/esp32.pdf"  # its PDF is not on this machine


def test_documents_without_a_local_pdf_or_vendor_address(monkeypatch):
    monkeypatch.setattr(db, "list_documents", lambda conn: [
        {"id": 3, "name": "DS_CD00237391", "file": "DS_CD00237391.pdf", "part": "STM32F205/207", "revision": "r"},
        {"id": 4, "name": "unknown", "file": None, "part": None, "revision": None},
    ])
    assert [d["pdf_url"] for d in api.documents(None)] == [None, None]


def test_deployed_never_links_to_a_local_copy(monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED", True)
    docs = api.documents(None)
    assert docs[1]["pdf_url"] == "https://vendor.example/lm35.pdf"  # lm35.pdf is on disk, but not served deployed


def test_every_configured_vendor_address_is_https():
    assert REAL_DOCUMENTS
    for name, entry in REAL_DOCUMENTS.items():
        assert "url" in entry, name
        assert entry["url"] is None or entry["url"].startswith("https://"), name


# ---------------------------------------------------------------- a good answer

def test_answer_shape():
    status, body = ask({"question": "  What is the max supply voltage?  "}, fake_answer())
    assert status == 200
    assert body["question"] == "What is the max supply voltage?"
    assert body["answer"] == "35 V [1]."
    assert body["not_contained"] is False
    assert body["warnings"] == []
    assert body["timings_ms"] == {"search": 40.0, "rerank": 3.0, "generate": 900.0}
    assert body["setup"]["strategy"] == config.DEFAULT_STRATEGY and body["setup"]["passages"] == config.TOP_K
    assert body["sources"] == [{
        "label": 1, "document": "lm35", "part": "LM35", "page": 4, "page_label": "3",
        "title": "LM35 (lm35), page 4, printed page 3", "pdf_url": "/pdfs/lm35.pdf#page=4", "upload": False,
    }]


def test_source_without_a_local_pdf_links_to_the_vendor_page():
    answer = result(sources=[{"label": 1, "id": 9, "document": "esp32_datasheet_en", "page": 51, "page_label": "51"}])
    status, body = ask({"question": "q"}, fake_answer(answer))
    assert status == 200 and body["sources"][0]["pdf_url"] == "https://vendor.example/esp32.pdf#page=51"


def test_source_without_any_pdf_has_no_link(monkeypatch):
    monkeypatch.setitem(VENDOR, "esp32_datasheet_en", {**VENDOR["esp32_datasheet_en"], "url": None})
    answer = result(sources=[{"label": 1, "id": 9, "document": "esp32_datasheet_en", "page": 51, "page_label": "51"}])
    status, body = ask({"question": "q"}, fake_answer(answer))
    assert status == 200 and body["sources"][0]["pdf_url"] is None


def test_question_is_trimmed_and_document_passed_through():
    seen = {}
    ask({"question": " q ", "document": "lm35"}, fake_answer(seen=seen))
    assert seen == {"question": "q", "document": "lm35"}


def test_empty_document_means_all():
    seen = {}
    ask({"question": "q", "document": ""}, fake_answer(seen=seen))
    assert seen["document"] is None


def test_not_contained_and_warnings_are_passed_on():
    answer = result(answer=config.NOT_CONTAINED, not_contained=True, sources=[], passages=[])
    status, body = ask({"question": "q"}, fake_answer(answer))
    assert status == 200 and body["not_contained"] is True and body["sources"] == []
    assert len(body["warnings"]) == 1 and "no chunks" in body["warnings"][0]


def test_spend_is_read_from_the_cost_log():
    from rag.costlog import log_cost

    def answer(conn, question, document=None):
        log_cost("generate", "m", "input_tokens", 100, 0.0006)
        return result()

    _, body = ask({"question": "q"}, answer)
    assert body["spent_usd"] == pytest.approx(0.0006)


def test_answer_parts_mark_the_citations():
    _, body = ask({"question": "q"}, fake_answer())
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
    status, body = ask({"question": "q", "document": document},
                           fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and "datasheet" in body["error"]


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "   "}, {"question": 5}, [], "q", None])
def test_no_question(payload):
    status, body = ask(payload, fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and body["error"]


def test_question_too_long():
    status, body = ask({"question": "x" * (config.API_MAX_QUESTION_CHARS + 1)},
                           fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and str(config.API_MAX_QUESTION_CHARS) in body["error"]


def test_unknown_document():
    status, body = ask({"question": "q", "document": "lm358"},
                           fake_answer(raises=AssertionError("must not be called")))
    assert status == 400 and "datasheet" in body["error"]


# ---------------------------------------------------------------- failures of the steps

def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "no"}}, "Rerank")


def test_throttling_is_429():
    status, body = ask({"question": "q"}, fake_answer(raises=client_error("ThrottlingException")))
    assert status == 429 and "minute" in body["error"]


def test_access_denied_is_502_with_the_permission():
    status, body = ask({"question": "q"}, fake_answer(raises=client_error("AccessDeniedException")))
    assert status == 502 and "bedrock:Rerank" in body["error"]


def test_other_aws_error_names_its_code():
    status, body = ask({"question": "q"}, fake_answer(raises=client_error("ValidationException")))
    assert status == 502 and "ValidationException" in body["error"]


def test_no_credentials():
    status, body = ask({"question": "q"}, fake_answer(raises=NoCredentialsError()))
    assert status == 502 and "credentials" in body["error"]


def test_step_runtime_error():
    status, body = ask({"question": "q"}, fake_answer(raises=RuntimeError("the model stopped")))
    assert status == 502 and "the model stopped" in body["error"]


def test_database_lost(monkeypatch):
    def down(conn):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(db, "list_documents", down)
    status, body = ask({"question": "q"}, fake_answer())
    assert status == 503 and "docker start rag-pg" in body["error"]


def test_database_lost_during_the_answer():
    status, _ = ask({"question": "q"}, fake_answer(raises=psycopg.OperationalError("gone")))
    assert status == 503


# ---------------------------------------------------------------- the daily question cap (Cycle 2 steps 5 and 7b)

class FakeCounter:
    """rag.usage with the counts in memory; `calls` counts how often a place was asked for."""

    def __init__(self):
        self.calls, self.asks, self.error, self.days = 0, 0, None, []

    def take(self, kind, limit, day=None):
        assert kind == "asks"
        self.days.append(day)
        self.calls += 1
        if self.error:
            raise self.error
        if self.asks >= limit:
            return None
        self.asks += 1
        return self.asks

    def release(self, kind, day=None):
        self.days.append(day)
        self.asks = max(0, self.asks - 1)


@pytest.fixture
def counter(monkeypatch):
    """A fake counter with a limit of 2."""
    monkeypatch.setattr(config, "API_DAILY_ASK_LIMIT", 2)
    return FakeCounter()


def test_no_limit_never_counts(monkeypatch, counter):
    monkeypatch.setattr(config, "API_DAILY_ASK_LIMIT", 0)
    for _ in range(3):
        assert ask({"question": "q"}, fake_answer(), counter)[0] == 200
    assert counter.calls == 0


def test_reached_limit_answers_429_without_connecting(counter):
    assert ask({"question": "q"}, fake_answer(), counter)[0] == 200
    assert ask({"question": "q"}, fake_answer(), counter)[0] == 200
    seen, opener = {}, Opener()
    status, body = ask({"question": "q"}, fake_answer(seen=seen), counter, opener)
    assert status == 429 and body == {"error": api.DAILY_LIMIT, "limit": "daily"}
    assert seen == {} and opener.opened == 0


def test_aws_throttling_gives_the_place_back(counter):
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Rerank")
    status, body = ask({"question": "q"}, fake_answer(raises=throttled), counter)
    assert status == 429 and "limit" not in body
    assert counter.asks == 0


def test_other_aws_failures_keep_the_place(counter):
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Converse")
    assert ask({"question": "q"}, fake_answer(raises=denied), counter)[0] == 502
    assert counter.asks == 1


@pytest.mark.parametrize("payload", [{"question": ""}, {"question": "x" * 501}, {"question": "q", "document": "nope"},
                                     "not a dict"])
def test_invalid_requests_neither_count_nor_connect(counter, payload):
    opener = Opener()
    assert ask(payload, fake_answer(), counter, opener)[0] == 400
    assert counter.calls == 0 and opener.opened == 0


def test_database_that_does_not_answer_gives_the_place_back(counter):
    seen = {}
    status, _ = ask({"question": "q"}, fake_answer(seen=seen), counter, Opener(psycopg.OperationalError("timeout")))
    assert status == 503 and seen == {}
    assert counter.calls == 1 and counter.asks == 0


def test_counter_that_does_not_answer_refuses_without_connecting(counter):
    counter.error = ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no table"}}, "UpdateItem")
    seen, opener = {}, Opener()
    status, body = ask({"question": "q"}, fake_answer(seen=seen), counter, opener)
    assert status == 503 and body["error"] == api.BUSY
    assert seen == {} and opener.opened == 0


def test_documents_are_checked_without_the_database(monkeypatch):
    def not_called(conn):
        raise AssertionError("the database must not be read before the checks pass")

    monkeypatch.setattr(db, "list_documents", not_called)
    assert api.check_ask({"question": " q ", "document": "lm35"}) == ("q", "lm35", None)
    assert api.check_ask({"question": "q", "document": ""}) == ("q", None, None)
    assert api.check_ask({"question": "q", "document": "lm358"})[2][0] == 400


def test_database_lost_during_the_answer_keeps_the_place(counter):
    # Bedrock may already have been paid for, so the place stays counted.
    status, _ = ask({"question": "q"}, fake_answer(raises=psycopg.OperationalError("gone")), counter)
    assert status == 503 and counter.asks == 1


def test_a_place_goes_back_to_the_day_it_was_taken_from(counter, monkeypatch):
    days = iter([date(2026, 10, 23), date(2026, 10, 24)])
    monkeypatch.setattr(api.usage, "today", lambda: next(days))
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Rerank")
    ask({"question": "q"}, fake_answer(raises=throttled), counter)
    assert counter.days == [date(2026, 10, 23), date(2026, 10, 23)]


def test_datasheet_not_stored_gives_400_and_the_place_back(counter, monkeypatch):
    # Configured in config.DOCUMENTS but not loaded in this database (for example a partial reload).
    monkeypatch.setattr(db, "list_documents", lambda conn: [DOCS[1]])
    seen = {}
    status, body = ask({"question": "q", "document": "esp32_datasheet_en"}, fake_answer(seen=seen), counter)
    assert status == 400 and "datasheet" in body["error"]
    assert seen == {} and counter.asks == 0


def test_counter_without_a_table_refuses_with_503(counter):
    counter.error = RuntimeError("USAGE_TABLE is not set")
    opener = Opener()
    status, body = ask({"question": "q"}, fake_answer(), counter, opener)
    assert status == 503 and body["error"] == api.BUSY and opener.opened == 0


# ---------------------------------------------------------------- the Knowledge Base pipeline (Cycle 2 step 8)

def kb_result(**fields) -> dict:
    """What kb.answer_question returns: as `result`, with the KB's timings and a passage id from AWS."""
    base = result(timings_ms={"retrieve": 300.0, "generate": 900.0},
                  sources=[{"label": 1, "id": "chunk-a", "document": "lm35", "page": 4, "page_label": None}],
                  passages=[{"id": "chunk-a", "document": "lm35", "page": 4, "text": "Supply voltage: 35 V"}])
    return {**base, **fields}


def fake_kb(returned=None, raises=None, seen=None):
    def answer(question, document=None):
        if seen is not None:
            seen.update(question=question, document=document)
        if raises:
            raise raises
        return returned or kb_result()
    return answer


class KindCounter:
    """rag.usage in memory, per kind."""

    def __init__(self):
        self.counts, self.taken = {}, []

    def take(self, kind, limit, day=None):
        self.taken.append(kind)
        if self.counts.get(kind, 0) >= limit:
            return None
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return self.counts[kind]

    def release(self, kind, day=None):
        self.counts[kind] = max(0, self.counts.get(kind, 0) - 1)


def ask_kb(payload, kb_fn, counter=None, opener=None):
    opener = opener or Opener()
    status, body = api.ask(payload, opener, fake_answer(raises=AssertionError("ours must not run")), counter, kb_fn)
    assert opener.opened == 0  # the Knowledge Base pipeline never opens a database connection
    return status, body


def test_kb_answer_shape():
    seen = {}
    status, body = ask_kb({"question": " q ", "document": "lm35", "pipeline": "kb"}, fake_kb(seen=seen))
    assert status == 200 and seen == {"question": "q", "document": "lm35"}
    assert body["setup"]["pipeline"] == "kb" and body["timings_ms"] == {"retrieve": 300.0, "generate": 900.0}
    assert body["sources"][0]["pdf_url"] == "/pdfs/lm35.pdf#page=4"  # the local copy, on this machine
    assert body["answer_parts"] == [{"text": "35 V "}, {"text": "[1]", "labels": [1]}, {"text": "."}]
    assert body["warnings"] == []


def test_kb_deployed_links_to_the_vendor(monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED", True)
    _, body = ask_kb({"question": "q", "pipeline": "kb"}, fake_kb())
    assert body["sources"][0]["pdf_url"] == "https://vendor.example/lm35.pdf#page=4"


def test_ours_is_the_default_and_names_itself():
    status, body = ask({"question": "q"}, fake_answer())
    assert status == 200 and body["setup"]["pipeline"] == "ours"
    status, body = ask({"question": "q", "pipeline": "ours"}, fake_answer())
    assert status == 200 and body["setup"]["pipeline"] == "ours"


@pytest.mark.parametrize("name", ["KB", "bedrock", 3, ["kb"]])
def test_unknown_pipeline_is_400_before_anything_runs(name, monkeypatch):
    monkeypatch.setattr(config, "KB_DAILY_ASK_LIMIT", 2)
    counter = KindCounter()
    status, body = ask_kb({"question": "q", "pipeline": name}, fake_kb(raises=AssertionError("must not run")),
                          counter)
    assert status == 400 and "pipeline" in body["error"] and counter.taken == []


def test_kb_counts_on_its_own_cap(monkeypatch):
    monkeypatch.setattr(config, "API_DAILY_ASK_LIMIT", 5)
    monkeypatch.setattr(config, "KB_DAILY_ASK_LIMIT", 1)
    counter = KindCounter()
    assert ask_kb({"question": "q", "pipeline": "kb"}, fake_kb(), counter)[0] == 200
    status, body = ask_kb({"question": "q", "pipeline": "kb"}, fake_kb(), counter)
    assert status == 429 and body["limit"] == "daily"
    assert ask({"question": "q"}, fake_answer(), counter)[0] == 200  # ours still has places
    assert counter.counts == {"kb_asks": 1, "asks": 1}


def test_kb_throttle_gives_the_place_back(monkeypatch):
    monkeypatch.setattr(config, "KB_DAILY_ASK_LIMIT", 2)
    counter = KindCounter()
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Retrieve")
    status, _ = ask_kb({"question": "q", "pipeline": "kb"}, fake_kb(raises=throttled), counter)
    assert status == 429 and counter.counts["kb_asks"] == 0


@pytest.mark.parametrize("error, status", [
    (ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Retrieve"), 502),
    (RuntimeError("KB_ID is not set"), 502),
])
def test_kb_failures(error, status):
    assert ask_kb({"question": "q", "pipeline": "kb"}, fake_kb(raises=error))[0] == status


def test_kb_warnings_reach_the_page():
    unpaged = kb_result(passages=[{"id": "x", "document": "lm35", "page": None, "text": "t"}], sources=[],
                        answer="No idea.")
    _, body = ask_kb({"question": "q", "pipeline": "kb"}, fake_kb(unpaged))
    assert any("without a datasheet or page" in w for w in body["warnings"])


def test_kb_source_without_a_page_links_the_datasheet_not_a_page():
    unpaged = kb_result(sources=[{"label": 1, "id": "x", "document": "lm35", "page": None, "page_label": None}])
    _, body = ask_kb({"question": "q", "pipeline": "kb"}, fake_kb(unpaged))
    assert body["sources"][0]["pdf_url"] == "/pdfs/lm35.pdf" and body["sources"][0]["title"].endswith("page unknown")


@pytest.mark.parametrize("name", [False, 0, "", {}])
def test_a_falsy_pipeline_is_refused_not_read_as_ours(name):
    assert ask({"question": "q", "pipeline": name}, fake_answer(raises=AssertionError("must not run")))[0] == 400


def paid_kb(raises):
    """A KB answer that pays for a Retrieve (one cost row) and then fails."""
    def answer(question, document=None):
        from rag import costlog
        costlog.log_cost("kb_retrieve", "bedrock-managed-kb", "query", 1, config.KB_USD_PER_RETRIEVE)
        raise raises
    return answer


def test_kb_place_is_kept_when_something_was_paid(monkeypatch):
    monkeypatch.setattr(config, "KB_DAILY_ASK_LIMIT", 2)
    counter = KindCounter()
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
    status, _ = ask_kb({"question": "q", "pipeline": "kb"}, paid_kb(throttled), counter)
    assert status == 429 and counter.counts["kb_asks"] == 1  # the Retrieve was paid, so the place stays


def test_kb_place_goes_back_when_nothing_was_paid(monkeypatch):
    monkeypatch.setattr(config, "KB_DAILY_ASK_LIMIT", 2)
    counter = KindCounter()
    status, _ = ask_kb({"question": "q", "pipeline": "kb"}, fake_kb(raises=RuntimeError("KB_ID is not set")), counter)
    assert status == 502 and counter.counts["kb_asks"] == 0


# ---------------------------------------------------------------- uploads (Cycle 2 step 9a)

UPLOAD_ID = "0123456789abcdef"
UPLOAD_DOC = "upload-" + UPLOAD_ID


def upload_store(**fields):
    """A memory store holding one upload, ready on our pipeline unless `fields` say otherwise."""
    store = uploads.MemoryStore()
    store.create({**uploads.new_record(UPLOAD_ID, "My Sensor.pdf", kb=True), "status": "ready", "ours": "ready",
                  "kb": "syncing", "pages": 3, "chunks": 9, **fields})
    return store


def upload_result():
    return result(sources=[{"label": 1, "id": 9, "document": UPLOAD_DOC, "page": 2, "page_label": "2"}],
                  passages=[{"id": 9, "document": UPLOAD_DOC, "page": 2, "text": "VDD 5 V"}])


def test_question_about_an_upload_searches_that_upload_only():
    seen, opener = {}, Opener()
    status, body = api.ask({"question": "Supply voltage?", "upload_id": UPLOAD_ID}, opener,
                           fake_answer(upload_result(), seen=seen), upload_store=upload_store())
    assert status == 200 and seen["document"] == UPLOAD_DOC and opener.opened == 1
    source = body["sources"][0]
    assert source["upload"] is True and source["pdf_url"] is None and source["part"] == "My Sensor"


def test_upload_named_my_own_datasheet_file_never_links_a_local_pdf():
    # lm35.pdf is on disk in this test; an upload with that file name must still not link to it.
    store = upload_store(filename="lm35.pdf")
    status, body = api.ask({"question": "q", "upload_id": UPLOAD_ID}, Opener(), fake_answer(upload_result()),
                           upload_store=store)
    assert status == 200 and body["sources"][0]["pdf_url"] is None


def test_kb_question_about_an_upload_filters_on_its_name():
    seen = {}
    status, _ = api.ask({"question": "q", "upload_id": UPLOAD_ID, "pipeline": "kb"}, Opener(),
                           kb_answer_fn=fake_kb(seen=seen), upload_store=upload_store(kb="ready"))
    assert status == 200 and seen["document"] == UPLOAD_DOC


@pytest.mark.parametrize(("fields", "pipeline_name", "status", "words"), [
    ({"status": "ingesting", "ours": "waiting"}, "ours", 409, "not ready"),
    ({}, "kb", 409, "not ready"),  # KB still syncing
    ({"kb": "off"}, "kb", 409, "Custom pipeline"),
    ({"kb": "failed"}, "kb", 409, "Custom pipeline"),
    ({"status": "failed", "ours": "failed", "kb": "off", "error": "The PDF is scanned."}, "ours", 409, "scanned"),
])
def test_upload_not_ready_is_refused_before_counting_or_connecting(fields, pipeline_name, status, words):
    counter, opener = KindCounter(), Opener()
    got, body = api.ask({"question": "q", "upload_id": UPLOAD_ID, "pipeline": pipeline_name}, opener,
                        fake_answer(), counter, fake_kb(), upload_store=upload_store(**fields))
    assert got == status and words in body["error"]
    assert counter.taken == [] and opener.opened == 0


@pytest.mark.parametrize("upload_id", ["ffffffffffffffff", "not-an-id", 12])
def test_unknown_or_malformed_upload_is_refused(upload_id):
    opener = Opener()
    status, body = api.ask({"question": "q", "upload_id": upload_id}, opener, fake_answer(),
                           upload_store=upload_store())
    assert status in (400, 404) and "not known" in body["error"] and opener.opened == 0


def test_upload_and_datasheet_together_are_refused():
    status, body = api.ask({"question": "q", "upload_id": UPLOAD_ID, "document": "lm35"}, Opener(), fake_answer(),
                           upload_store=upload_store())
    assert status == 400 and "not both" in body["error"]


def test_upload_store_that_does_not_answer_gives_503():
    class Broken:
        def get(self, upload_id):
            raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no table"}}, "GetItem")

    status, body = api.ask({"question": "q", "upload_id": UPLOAD_ID}, Opener(), fake_answer(), upload_store=Broken())
    assert status == 503 and body["error"] == api.BUSY


def test_question_without_an_upload_never_reads_the_store():
    class Untouchable:
        def get(self, upload_id):
            raise AssertionError("read")

    status, _ = api.ask({"question": "q"}, Opener(), fake_answer(), upload_store=Untouchable())
    assert status == 200


# ---- starting an upload

def presign(upload_id):
    return {"post": {"url": "https://bucket.s3.amazonaws.com/", "fields": {"key": f"incoming/{upload_id}.pdf"}}}


@pytest.mark.parametrize(("payload", "status", "words"), [
    (["x"], 400, "JSON object"),
    ({"filename": "notes.txt", "size": 10}, 400, ".pdf"),
    ({"filename": "s.pdf", "size": 0}, 400, "empty"),
    ({"filename": "s.pdf", "size": "10"}, 400, "empty"),
    ({"filename": "s.pdf", "size": True}, 400, "empty"),
    ({"filename": "s.pdf", "size": 11 * 1024 * 1024}, 413, "larger than 10 MB"),
])
def test_start_upload_refuses_bad_requests_without_counting(payload, status, words, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DAILY_LIMIT", 5)
    counter, store = KindCounter(), uploads.MemoryStore()
    got, body = api.start_upload(payload, store, presign, counter)
    assert got == status and words in body["error"] and counter.taken == []
    assert store.take_lock("free", 1)  # never taken


def test_start_upload_makes_a_record_and_a_presigned_post(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DAILY_LIMIT", 5)
    counter, store = KindCounter(), uploads.MemoryStore()
    status, body = api.start_upload({"filename": r"C:\fakepath\My Sensor.pdf", "size": 2000}, store, presign,
                                    counter, to_kb=True)
    assert status == 200 and uploads.valid_id(body["upload_id"])
    assert body["filename"] == "My Sensor.pdf" and body["post"]["fields"]["key"] == f"incoming/{body['upload_id']}.pdf"
    assert body["limits"]["max_bytes"] == config.UPLOAD_MAX_BYTES
    record = store.get(body["upload_id"])
    assert record["status"] == "waiting" and record["kb"] == "waiting"
    assert counter.counts == {"uploads": 1}
    assert not store.take_lock("next-upload", 1)  # one at a time


def test_second_upload_while_one_is_processed_is_refused_and_not_counted(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DAILY_LIMIT", 5)
    counter, store = KindCounter(), uploads.MemoryStore()
    assert api.start_upload({"filename": "a.pdf", "size": 10}, store, presign, counter)[0] == 200
    status, body = api.start_upload({"filename": "b.pdf", "size": 10}, store, presign, counter)
    assert status == 429 and body["error"] == api.UPLOAD_BUSY and "limit" not in body
    assert counter.counts == {"uploads": 1}  # the refused one gave its place back


def test_daily_upload_limit(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DAILY_LIMIT", 1)
    counter = KindCounter()
    assert api.start_upload({"filename": "a.pdf", "size": 10}, uploads.MemoryStore(), presign, counter)[0] == 200
    status, body = api.start_upload({"filename": "b.pdf", "size": 10}, uploads.MemoryStore(), presign, counter)
    assert status == 429 and body["limit"] == "daily" and body["error"] == api.UPLOAD_DAILY_LIMIT


def test_no_upload_limit_never_counts(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DAILY_LIMIT", 0)
    counter = KindCounter()
    assert api.start_upload({"filename": "a.pdf", "size": 10}, uploads.MemoryStore(), presign, counter)[0] == 200
    assert counter.taken == []


def test_presign_failure_gives_the_lock_and_place_back(monkeypatch, capsys):
    monkeypatch.setattr(config, "UPLOAD_DAILY_LIMIT", 5)

    def broken(upload_id):
        raise RuntimeError("UPLOAD_BUCKET is not set.")

    counter, store = KindCounter(), uploads.MemoryStore()
    status, _ = api.start_upload({"filename": "a.pdf", "size": 10}, store, broken, counter)
    assert status == 503 and counter.counts == {"uploads": 0} and store.take_lock("free", 1)


# ---- an upload's status

def test_upload_status_shows_the_public_record():
    status, body = api.upload_status(UPLOAD_ID, upload_store(kb_job="JOB1234567"))
    assert status == 200 and body["upload_id"] == UPLOAD_ID and body["ours"] == "ready"
    assert "kb_job" not in body


@pytest.mark.parametrize("upload_id", ["ffffffffffffffff", "../x", ""])
def test_upload_status_of_unknown_upload_is_404(upload_id):
    assert api.upload_status(upload_id, upload_store())[0] == 404


@pytest.mark.parametrize(("job", "failed", "kb_state"), [
    ("IN_PROGRESS", 0, "syncing"), ("COMPLETE", 0, "ready"), ("COMPLETE", 1, "failed"), ("FAILED", 0, "failed"),
    ("STOPPED", 0, "failed"),
])
def test_upload_status_follows_the_kb_job(job, failed, kb_state):
    store, asked = upload_store(kb_job="JOB1234567"), []

    def kb_status(job_id):
        asked.append(job_id)
        return job, failed

    status, body = api.upload_status(UPLOAD_ID, store, kb_status)
    assert status == 200 and body["kb"] == kb_state and asked == ["JOB1234567"]
    assert store.get(UPLOAD_ID)["kb"] == kb_state


def test_upload_status_does_not_ask_the_kb_once_it_is_settled():
    def kb_status(job_id):
        raise AssertionError("asked")

    assert api.upload_status(UPLOAD_ID, upload_store(kb="ready", kb_job="JOB1234567"), kb_status)[0] == 200


def test_upload_status_keeps_syncing_when_the_kb_does_not_answer(capsys):
    def kb_status(job_id):
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "GetIngestionJob")

    status, body = api.upload_status(UPLOAD_ID, upload_store(kb_job="JOB1234567"), kb_status)
    assert status == 200 and body["kb"] == "syncing"


def test_a_kb_question_refreshes_a_syncing_upload_when_the_page_stopped_polling():
    store = upload_store(kb_job="JOB1234567")
    status, _ = api.ask({"question": "q", "upload_id": UPLOAD_ID, "pipeline": "kb"}, Opener(),
                        kb_answer_fn=fake_kb(), upload_store=store, kb_status=lambda job: ("COMPLETE", 0))
    assert status == 200 and store.get(UPLOAD_ID)["kb"] == "ready"


def test_a_kb_question_on_a_still_syncing_upload_is_409():
    status, body = api.ask({"question": "q", "upload_id": UPLOAD_ID, "pipeline": "kb"}, Opener(),
                           kb_answer_fn=fake_kb(), upload_store=upload_store(kb_job="JOB1234567"),
                           kb_status=lambda job: ("IN_PROGRESS", 0))
    assert status == 409 and "not ready" in body["error"]


def test_expired_uploads_are_deleted_when_a_question_connects(monkeypatch):
    deleted = []
    monkeypatch.setattr(db, "delete_expired_uploads", lambda conn: deleted.append(conn) or 0)
    assert ask({"question": "q"}, fake_answer())[0] == 200
    assert len(deleted) == 1


def test_an_upload_embedding_in_the_background_is_not_a_questions_cost(monkeypatch):
    def answer(conn, question, document=None):
        costlog.log_cost(costlog.UPLOAD_OPERATION + "0123456789abcdef", "titan", "input_tokens", 1000, 0.5)
        costlog.log_cost("generate", "nova", "input_tokens", 10, 0.001)
        return result()

    status, body = ask({"question": "q"}, answer)
    assert status == 200 and body["spent_usd"] == pytest.approx(0.001)
