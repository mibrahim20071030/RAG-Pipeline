"""Cycle 2 step 9a tests for rag.ingest: an uploaded PDF checked, parsed, chunked, embedded at a pace and stored, then
handed to the Knowledge Base. PDFs are built in memory (conftest.make_pdf). No paid call: fake embedder, S3 and KB
clients. The tests that store chunks need the local Postgres and skip without it."""
import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from conftest import make_pdf
from rag import config, db, ingest, load, search, uploads

ID = "0123456789abcdef"
LINES = "Supply voltage VDD: 2.7 V to 5.5 V\nOperating temperature: -40 C to 125 C\nQuiescent current: 12 uA"


def pdf(pages: int = 3) -> bytes:
    return make_pdf([f"Page {n} of the DEMO1234 sensor\n{LINES}" for n in range(1, pages + 1)])


def opener(conn):
    """The `connect` of ingest_pdf for the test schema's connection, which the test keeps open."""
    return lambda: contextlib.nullcontext(conn)


class FakeEmbedder:
    """Stands in for embed.embed_texts: a vector from the text's length, and a record of each batch."""

    def __init__(self):
        self.batches = []

    def __call__(self, texts, label=""):
        self.batches.append((list(texts), label))
        return [[(len(text) % 97 + 1) / 100] + [0.0] * (config.EMBED_DIMENSIONS - 1) for text in texts]


# ---------------------------------------------------------------- checks before parsing

@pytest.mark.parametrize(("data", "message"), [
    (b"", "empty"),
    (b"hello, this is text", "not a PDF"),
    (b"\x89PNG\r\n" + b"0" * 2000, "not a PDF"),
])
def test_check_pdf_refuses(data, message):
    with pytest.raises(ingest.UploadRejected, match=message):
        ingest.check_pdf(data)


def test_check_pdf_refuses_a_file_over_the_limit(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_MAX_BYTES", 100)
    with pytest.raises(ingest.UploadRejected, match="larger than"):
        ingest.check_pdf(b"%PDF-1.4" + b"0" * 200)


def test_check_pdf_accepts_a_header_after_some_bytes():
    ingest.check_pdf(b"\n\n%PDF-1.7\n...")


def test_parse_upload_names_the_document_after_the_upload():
    parsed = ingest.parse_upload(pdf(), ID, "demo sensor.pdf")
    assert parsed["document"] == uploads.document_name(ID)
    assert parsed["page_count"] == 3
    assert parsed["file"] == "demo sensor.pdf" and parsed["part"] == "demo sensor"
    assert "Supply voltage VDD" in parsed["pages"][0]["text"]


def test_parse_upload_refuses_too_many_pages(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_MAX_PAGES", 2)
    with pytest.raises(ingest.UploadRejected, match="3 pages; the limit is 2"):
        ingest.parse_upload(pdf(3), ID, "s.pdf")


def test_parse_upload_refuses_a_broken_pdf():
    with pytest.raises(ingest.UploadRejected, match="could not be read"):
        ingest.parse_upload(b"%PDF-1.4\nthis is not really a pdf", ID, "s.pdf")


def test_parse_upload_refuses_a_pdf_with_no_text():
    with pytest.raises(ingest.UploadRejected, match="no text"):
        ingest.parse_upload(make_pdf(["", ""]), ID, "s.pdf")


def test_chunks_are_embedded_with_the_files_title_as_their_header():
    parsed = ingest.parse_upload(pdf(), ID, "demo sensor.pdf")
    chunks, inputs = load.strategy_inputs(parsed, config.UPLOAD_STRATEGY)
    assert chunks and all(c["document"] == uploads.document_name(ID) for c in chunks)
    assert inputs[0].startswith(f"demo sensor ({uploads.document_name(ID)})")


# ---------------------------------------------------------------- pacing

class FakeTime:
    """A clock that moves only when slept, plus a fixed cost per embedding batch."""

    def __init__(self, batch_seconds=2.0):
        self.now, self.slept, self.batch_seconds = 0.0, [], batch_seconds

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_paced_embed_keeps_to_the_rate_and_reports_progress(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_EMBED_BATCH", 20)
    monkeypatch.setattr(config, "UPLOAD_EMBED_PER_MIN", 40)
    fake_time, embedder, done = FakeTime(), FakeEmbedder(), []

    def slow_embed(texts, label=""):
        fake_time.now += fake_time.batch_seconds
        return embedder(texts, label)

    vectors = ingest.paced_embed([f"t{i}" for i in range(50)], "upload/x", slow_embed, done.append,
                                 fake_time.sleep, fake_time.clock)
    assert len(vectors) == 50
    assert [len(batch) for batch, _ in embedder.batches] == [20, 20, 10]
    assert done == [20, 40, 50]
    # 20 texts at 40 a minute is 30 s a batch; each batch took 2 s, so 28 s of waiting, and none after the last.
    assert fake_time.slept == [28.0, 28.0]
    assert 50 / (fake_time.now / 60) <= 60  # under Titan's 60 a minute, counted over the whole run


def test_paced_embed_does_not_wait_when_a_batch_is_slow(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_EMBED_BATCH", 10)
    fake_time = FakeTime(batch_seconds=40.0)

    def slow_embed(texts, label=""):
        fake_time.now += fake_time.batch_seconds
        return FakeEmbedder()(texts, label)

    ingest.paced_embed(["a"] * 30, "x", slow_embed, None, fake_time.sleep, fake_time.clock)
    assert fake_time.slept == []


# ---------------------------------------------------------------- storing (needs Postgres)

def test_ingest_pdf_stores_the_upload_and_reports_progress(conn):
    seen = []
    result = ingest.ingest_pdf(opener(conn), pdf(), ID, "demo sensor.pdf", embed_fn=FakeEmbedder(),
                               progress=lambda **fields: seen.append(fields), sleep=lambda s: None)
    name = uploads.document_name(ID)
    assert result["document"] == name and result["pages"] == 3 and result["chunks"] > 0
    assert db.count_chunks(conn, config.UPLOAD_STRATEGY, name) == result["chunks"]
    assert seen[0] == {"pages": 3, "chunks": result["chunks"], "embedded": 0}
    assert seen[-1] == {"embedded": result["chunks"]}
    assert db.list_documents(conn) == []  # the curated list never shows an upload
    assert db.embedding_counts(conn) == {}  # an upload's vectors are not cached


def test_ingest_pdf_refuses_too_many_chunks(conn, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_MAX_CHUNKS", 1)
    embedder = FakeEmbedder()
    with pytest.raises(ingest.UploadRejected, match="too much text"):
        ingest.ingest_pdf(opener(conn), pdf(), ID, "s.pdf", embed_fn=embedder, sleep=lambda s: None)
    assert embedder.batches == []  # refused before any paid call
    assert db.count_chunks(conn) == 0


def _curated(conn):
    """One curated datasheet with a chunk about the same supply voltage."""
    document_id = db.upsert_document(conn, "lm35", "lm35.pdf", "LM35", "r")
    chunk = {"page": 1, "page_label": "1", "section": None, "kind": "text",
             "text": "Supply voltage VDD: 4 V to 30 V for the LM35."}
    db.replace_chunks(conn, document_id, config.UPLOAD_STRATEGY, [chunk], [[0.5] + [0.0] * 1023])


def test_an_upload_is_never_in_a_search_that_does_not_name_it(conn):
    _curated(conn)
    ingest.ingest_pdf(opener(conn), pdf(), ID, "s.pdf", embed_fn=FakeEmbedder(), sleep=lambda s: None)
    vector = [0.5] + [0.0] * 1023
    for hits in (search.vector_search(conn, vector, 30), search.keyword_search(conn, "supply voltage VDD", n=30),
                 search.hybrid_search(conn, "supply voltage VDD", vector, 30)):
        assert hits and {hit["document"] for hit in hits} == {"lm35"}
    named = search.hybrid_search(conn, "supply voltage VDD", vector, 30, document=uploads.document_name(ID))
    assert named and {hit["document"] for hit in named} == {uploads.document_name(ID)}


def test_keyword_scores_of_the_curated_datasheets_do_not_change_with_an_upload(conn):
    _curated(conn)
    before = search.keyword_search(conn, "supply voltage VDD", n=30)
    ingest.ingest_pdf(opener(conn), pdf(), ID, "s.pdf", embed_fn=FakeEmbedder(), sleep=lambda s: None)
    assert search.keyword_search(conn, "supply voltage VDD", n=30) == before


def test_an_expired_upload_is_not_searched_and_is_deleted_by_the_next(conn):
    long_ago = datetime.now(UTC) - timedelta(hours=config.UPLOAD_KEEP_HOURS + 1)
    ingest.ingest_pdf(opener(conn), pdf(), ID, "s.pdf", embed_fn=FakeEmbedder(), sleep=lambda s: None, now=long_ago)
    name = uploads.document_name(ID)
    assert db.count_chunks(conn, config.UPLOAD_STRATEGY, name) > 0
    assert search.keyword_search(conn, "supply voltage", n=30, document=name) == []
    other = "fedcba9876543210"
    result = ingest.ingest_pdf(opener(conn), pdf(), other, "t.pdf", embed_fn=FakeEmbedder(), sleep=lambda s: None)
    assert result["expired_deleted"] == 1
    assert db.count_chunks(conn, config.UPLOAD_STRATEGY, name) == 0


def test_an_existing_database_gets_the_upload_columns(conn):
    conn.execute("ALTER TABLE documents DROP COLUMN source, DROP COLUMN title, DROP COLUMN expires_at")
    db.create_schema(conn)
    db.upsert_document(conn, "lm35", "lm35.pdf", "LM35", "r")
    assert [doc["name"] for doc in db.list_documents(conn)] == ["lm35"]  # a row from before counts as curated


# ---------------------------------------------------------------- the Knowledge Base hand-off

class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = (Body, ContentType)


def test_stage_for_kb_writes_the_pdf_and_its_sidecar(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_BUCKET", "bucket")
    s3 = FakeS3()
    key = ingest.stage_for_kb(b"%PDF-data", ID, "demo sensor.pdf", s3=s3)
    assert key == f"kb/{ID}.pdf"
    assert s3.objects[key] == (b"%PDF-data", "application/pdf")
    sidecar = __import__("json").loads(s3.objects[key + ".metadata.json"][0])
    attributes = sidecar["metadataAttributes"]
    assert attributes["document"]["value"] == {"type": "STRING", "stringValue": uploads.document_name(ID)}
    assert attributes["part"]["value"]["stringValue"] == "demo sensor"


class FakeAgent:
    def __init__(self, conflicts=0):
        self.conflicts, self.calls = conflicts, []

    def start_ingestion_job(self, **kwargs):
        self.calls.append(kwargs)
        if self.conflicts:
            self.conflicts -= 1
            raise ClientError({"Error": {"Code": "ConflictException", "Message": "a job is running"}},
                              "StartIngestionJob")
        return {"ingestionJob": {"ingestionJobId": "JOB1234567", "status": "STARTING"}}


def test_start_kb_sync_starts_the_uploads_data_source(monkeypatch):
    monkeypatch.setattr(config, "KB_ID", "KB12345678")
    monkeypatch.setattr(config, "KB_UPLOAD_DATA_SOURCE_ID", "DSUPLOAD01")
    agent = FakeAgent()
    assert ingest.start_kb_sync(ID, client=agent, sleep=lambda s: None) == "JOB1234567"
    assert agent.calls[0]["knowledgeBaseId"] == "KB12345678" and agent.calls[0]["dataSourceId"] == "DSUPLOAD01"


def test_start_kb_sync_waits_for_a_running_job(monkeypatch):
    slept = []
    agent = FakeAgent(conflicts=2)
    assert ingest.start_kb_sync(ID, client=agent, sleep=slept.append) == "JOB1234567"
    assert slept == [20, 20] and len(agent.calls) == 3


def test_start_kb_sync_gives_up_after_its_attempts():
    with pytest.raises(ClientError):
        ingest.start_kb_sync(ID, client=FakeAgent(conflicts=9), sleep=lambda s: None, attempts=2)


# ---------------------------------------------------------------- run_upload (no database: a fake ingest)

class Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def waiting_store():
    store = uploads.MemoryStore()
    store.create(uploads.new_record(ID, "s.pdf", kb=True))
    assert store.take_lock(ID, 60)
    return store


def fake_ingest(connect, data, upload_id, filename, progress=None):
    progress(pages=2, chunks=5, embedded=0)
    progress(embedded=5)
    return {"document": uploads.document_name(upload_id), "pages": 2, "chunks": 5}


def test_run_upload_ready_on_both_pipelines(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_BUCKET", "bucket")
    store, s3 = waiting_store(), FakeS3()
    final = ingest.run_upload(store, ID, b"%PDF-x", Conn, to_kb=True, ingest_fn=fake_ingest, s3=s3,
                              kb_client=FakeAgent())
    assert (final["status"], final["ours"], final["kb"]) == ("ready", "ready", "syncing")
    assert final["kb_job"] == "JOB1234567" and final["pages"] == 2 and final["embedded"] == 5
    assert f"kb/{ID}.pdf" in s3.objects
    assert store.take_lock("someone-else", 60)  # released


def test_run_upload_without_the_kb_leaves_it_off():
    store = uploads.MemoryStore()
    store.create(uploads.new_record(ID, "s.pdf", kb=False))
    final = ingest.run_upload(store, ID, b"%PDF-x", Conn, to_kb=False, ingest_fn=fake_ingest)
    assert (final["status"], final["ours"], final["kb"]) == ("ready", "ready", "off")


def test_run_upload_refused_file_says_why_and_releases_the_lock():
    def refuse(*args, **kwargs):
        raise ingest.UploadRejected("The PDF is scanned (its pages are images with no text to read).")

    store = waiting_store()
    final = ingest.run_upload(store, ID, b"%PDF-x", Conn, to_kb=True, ingest_fn=refuse)
    assert (final["status"], final["ours"], final["kb"]) == ("failed", "failed", "off")
    assert "scanned" in final["error"]
    assert store.take_lock("someone-else", 60)


def test_run_upload_unexpected_error_gives_a_plain_message(capsys):
    def crash(*args, **kwargs):
        raise ValueError("internal detail")

    final = ingest.run_upload(waiting_store(), ID, b"%PDF-x", Conn, to_kb=True, ingest_fn=crash)
    assert final["status"] == "failed" and final["error"] == ingest.FAILED
    assert "internal detail" not in final["error"]


def test_run_upload_kb_failure_keeps_our_pipeline_ready(monkeypatch, capsys):
    monkeypatch.setattr(config, "UPLOAD_BUCKET", "bucket")

    class BrokenS3:
        def put_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject")

    store = waiting_store()
    final = ingest.run_upload(store, ID, b"%PDF-x", Conn, to_kb=True, ingest_fn=fake_ingest, s3=BrokenS3())
    assert (final["status"], final["ours"], final["kb"]) == ("ready", "ready", "failed")
    assert store.take_lock("someone-else", 60)


def test_run_upload_of_an_unknown_upload_does_nothing():
    final = ingest.run_upload(uploads.MemoryStore(), ID, b"%PDF-x", Conn, to_kb=False, ingest_fn=fake_ingest)
    assert final["status"] == "failed"


def test_run_upload_stops_when_another_upload_holds_the_lock():
    store = uploads.MemoryStore()
    store.create(uploads.new_record(ID, "s.pdf", kb=False))
    assert store.take_lock("another-upload", 600)  # this upload's own lock expired and was taken
    final = ingest.run_upload(store, ID, b"%PDF-x", Conn, to_kb=False, ingest_fn=lambda *a, **k: pytest.fail("ran"))
    assert final["status"] == "failed" and final["error"] == uploads.LATE
    assert not store.take_lock("third", 60)  # the other upload's lock is left alone


def test_ingest_pdf_connects_only_after_embedding(conn):
    events = []

    def embed(texts, label=""):
        events.append("embed")
        return FakeEmbedder()(texts, label)

    @contextlib.contextmanager
    def connect():
        events.append("connect")
        yield conn

    ingest.ingest_pdf(connect, pdf(), ID, "s.pdf", embed_fn=embed, sleep=lambda s: None)
    assert events[-1] == "connect" and events.count("connect") == 1 and "embed" in events
