"""Cycle 2 step 4 and 7b tests: the Lambda handler and the settings Lambda sets from its environment.

No database and no AWS call: connect, ask, the answer and the daily counter are fakes. Step 7b: a request that will
not be answered (off switch, bad route or body, bad question, over the cap) must never open a connection.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

import handler
from rag import api, config, costlog, uploads

ROOT = Path(__file__).resolve().parent.parent


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.broken = False

    def close(self):
        self.closed = True


class Fakes:
    """Stand-ins for db.connect and api.ask that record their calls. The fake ask opens the connection, as the real
    one does once a question passed its checks."""

    def __init__(self):
        self.conns, self.asked = [], []
        self.connect_error = None
        self.ask_result = (200, {"answer": "35 V [1]", "timings_ms": {"search": 1.0}, "spent_usd": 0.0003})
        self.ask_error = None

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.conns.append(FakeConnection())
        return self.conns[-1]

    def ask(self, payload, open_conn, upload_store=None):
        with open_conn() as conn:
            self.asked.append((conn, payload))
            if self.ask_error:
                raise self.ask_error
            return self.ask_result

    def call(self, event):
        return handler.lambda_handler(event, None, connect=self.connect, ask=self.ask)


class FakeCounter:
    """rag.usage with a limit kept in memory."""

    def __init__(self, used=0):
        self.used, self.released = used, 0

    def take(self, kind, limit, day=None):
        if self.used >= limit:
            return None
        self.used += 1
        return self.used

    def release(self, kind, day=None):
        self.used, self.released = max(0, self.used - 1), self.released + 1


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED", True)
    return Fakes()


def v2(method, path, body=None, content_type="application/json", base64_body=False):
    """An HTTP API (payload 2.0) event."""
    event = {"version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method, "path": path}},
             "headers": {"content-type": content_type} if content_type else {}, "isBase64Encoded": False}
    if body is not None:
        event["body"] = base64.b64encode(body.encode()).decode() if base64_body else body
        event["isBase64Encoded"] = base64_body
    return event


def v1(method, path, body=None):
    """A REST API (payload 1.0) event; header names keep the case the client sent."""
    return {"httpMethod": method, "path": path, "headers": {"Content-Type": "application/json"}, "body": body,
            "isBase64Encoded": False}


def parsed(response):
    assert response["headers"]["content-type"].startswith("application/json")
    return response["statusCode"], json.loads(response["body"])


ASK = json.dumps({"question": "What is the maximum supply voltage of the LM35?"})


def test_ask_with_an_http_api_event(fakes):
    status, body = parsed(fakes.call(v2("POST", "/api/ask", ASK)))
    assert status == 200 and body["answer"] == "35 V [1]"
    assert fakes.asked[0][1] == {"question": "What is the maximum supply voltage of the LM35?"}


def test_ask_with_a_rest_api_event(fakes):
    status, _ = parsed(fakes.call(v1("POST", "/api/ask", ASK)))
    assert status == 200 and len(fakes.asked) == 1


def test_base64_body_is_decoded(fakes):
    status, _ = parsed(fakes.call(v2("POST", "/api/ask", ASK, base64_body=True)))
    assert status == 200
    assert fakes.asked[0][1]["question"].startswith("What is")


def test_documents_route_is_gone(fakes):
    # Step 7b: the page has the list written in, so this route only gave anyone a free way to wake Aurora.
    status, body = parsed(fakes.call(v2("GET", "/api/documents")))
    assert status == 404 and "error" in body
    assert fakes.conns == []


def test_status_from_ask_is_passed_on(fakes):
    fakes.ask_result = (400, {"error": "Type a question first."})
    assert parsed(fakes.call(v2("POST", "/api/ask", "{}"))) == (400, {"error": "Type a question first."})


@pytest.mark.parametrize("event, status", [
    (v2("GET", "/"), 404),
    (v2("GET", "/pdfs/lm35.pdf"), 404),
    (v2("GET", "/api/ask"), 405),
    (v2("POST", "/api/documents", "{}"), 404),
    (v2("POST", "/api/ask", ASK, content_type="text/plain"), 415),
    (v2("POST", "/api/ask", ASK, content_type=None), 415),
    (v2("POST", "/api/ask"), 400),
    (v2("POST", "/api/ask", "{not json"), 400),
    (v2("POST", "/api/ask", json.dumps({"question": "x" * 20_000})), 413),
    ({}, 404),
    ({**v2("POST", "/api/ask", ASK), "body": "not base64!", "isBase64Encoded": True}, 400),
])
def test_bad_requests_never_reach_ask(fakes, event, status):
    got, body = parsed(fakes.call(event))
    assert got == status and "error" in body
    assert fakes.asked == []


def test_a_bad_request_does_not_open_a_connection(fakes):
    fakes.call(v2("POST", "/api/ask", "{not json"))
    assert fakes.conns == []


def test_each_request_opens_and_closes_its_connection(fakes):
    # Aurora does not pause while a connection is open, so none may outlive its request.
    fakes.call(v2("POST", "/api/ask", ASK))
    fakes.call(v2("POST", "/api/ask", ASK))
    assert len(fakes.conns) == 2 and all(conn.closed for conn in fakes.conns)
    assert fakes.asked[0][0] is fakes.conns[0]


def test_off_switch_answers_before_anything_else(fakes, monkeypatch, capsys):
    monkeypatch.setattr(config, "DEMO_ENABLED", False)
    for event in (v2("POST", "/api/ask", ASK), v2("GET", "/api/documents"), {}):
        status, body = parsed(fakes.call(event))
        assert status == 503 and body == {"error": api.DEMO_OFF, "off": True}
    assert fakes.conns == [] and fakes.asked == []
    assert all(json.loads(line)["status"] == 503 for line in capsys.readouterr().out.splitlines())


def real_ask(counter, answer=None):
    """The real api.ask with a fake counter and a fake answer, as the handler's `ask`."""
    def answer_fn(conn, question, document=None):
        if answer:
            raise answer
        return {"answer": "35 V [1]", "passages": [], "sources": [], "not_contained": False, "invalid": [],
                "cut_off": False, "timings_ms": {"search": 1.0}}

    return lambda payload, open_conn, upload_store=None: api.ask(payload, open_conn, answer_fn, counter,
                                                               upload_store=upload_store)


@pytest.fixture
def capped(fakes, monkeypatch):
    """The fakes with the real api.ask, a daily cap of 2 and a fake counter; db.list_documents answers too."""
    monkeypatch.setattr(config, "API_DAILY_ASK_LIMIT", 2)
    monkeypatch.setattr(api.db, "list_documents", lambda conn: [{"name": "lm35", "file": "lm35.pdf"}])
    monkeypatch.setattr(api.db, "delete_expired_uploads", lambda conn: 0)
    fakes.counter = FakeCounter()
    fakes.ask = real_ask(fakes.counter)
    return fakes


@pytest.mark.parametrize("payload", [
    [], {"question": ""}, {"question": "   "}, {"question": 5}, {"question": "x" * (config.API_MAX_QUESTION_CHARS + 1)},
    {"question": "q", "document": "lm358"}, {"question": "q", "document": 3},
])
def test_a_refused_question_never_connects_or_counts(capped, payload):
    status, body = parsed(capped.call(v2("POST", "/api/ask", json.dumps(payload))))
    assert status == 400 and "error" in body
    assert capped.conns == [] and capped.counter.used == 0


def test_over_the_daily_cap_never_connects(capped, capsys):
    capped.counter.used = 2
    status, body = parsed(capped.call(v2("POST", "/api/ask", ASK)))
    assert status == 429 and body["limit"] == "daily"
    assert capped.conns == []
    assert "connect_ms" not in json.loads(capsys.readouterr().out.strip())


def test_an_answered_question_counts_and_connects(capped, capsys):
    status, body = parsed(capped.call(v2("POST", "/api/ask", ASK)))
    assert status == 200 and body["answer"] == "35 V [1]"
    assert capped.counter.used == 1 and len(capped.conns) == 1 and capped.conns[0].closed
    assert "connect_ms" in json.loads(capsys.readouterr().out.strip())


def test_database_that_does_not_answer_gives_the_place_back(capped):
    capped.connect_error = psycopg.OperationalError("timeout expired")
    status, body = parsed(capped.call(v2("POST", "/api/ask", ASK)))
    assert status == 503 and body["error"] == api.DATABASE_WAKING
    assert capped.counter.used == 0 and capped.counter.released == 1


def test_connection_is_closed_when_ask_fails(fakes):
    fakes.ask_error = KeyError("boom")
    fakes.call(v2("POST", "/api/ask", ASK))
    assert fakes.conns[0].closed


@pytest.mark.parametrize("stage", ["prod", "$default", None])
def test_stage_name_in_the_path_is_ignored(fakes, stage):
    event = v2("POST", f"/{stage}/api/ask" if stage and stage != "$default" else "/api/ask", ASK)
    if stage:
        event["requestContext"]["stage"] = stage
    assert parsed(fakes.call(event))[0] == 200


def test_content_type_is_read_without_case(fakes):
    assert parsed(fakes.call(v2("POST", "/api/ask", ASK, content_type="Application/JSON; charset=utf-8")))[0] == 200


def test_other_connect_failure_gives_json_500_and_a_log_line(fakes, capsys):
    fakes.connect_error = ValueError("DB_TARGET is 'aurora' but AURORA_HOST is empty")
    status, body = parsed(fakes.call(v2("POST", "/api/ask", ASK)))
    assert status == 500 and "error" in body
    log = json.loads(next(line for line in capsys.readouterr().out.splitlines() if line.startswith("{")))
    assert log["status"] == 500 and log["path"] == "/api/ask"


def test_database_that_does_not_answer_gives_503(fakes):
    fakes.ask = real_ask(None)
    fakes.connect_error = psycopg.OperationalError("timeout expired")
    status, body = parsed(fakes.call(v2("POST", "/api/ask", ASK)))
    assert status == 503 and body["error"] == api.DATABASE_WAKING


def test_database_lost_during_ask_gives_503(fakes, monkeypatch):
    def lost(conn):
        raise psycopg.OperationalError("server closed the connection")

    monkeypatch.setattr(api.db, "list_documents", lost)
    monkeypatch.setattr(api.db, "delete_expired_uploads", lambda conn: 0)
    fakes.ask = real_ask(None)
    status, body = parsed(fakes.call(v2("POST", "/api/ask", ASK)))
    assert status == 503 and body["error"] == api.DATABASE_WAKING
    assert fakes.conns[0].closed


def test_unexpected_error_gives_json_500(fakes):
    fakes.ask_error = KeyError("boom")
    status, body = parsed(fakes.call(v2("POST", "/api/ask", ASK)))
    assert status == 500 and "error" in body


def test_one_log_line_per_request(fakes, capsys):
    fakes.call(v2("POST", "/api/ask", ASK))
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert len(lines) == 1
    log = json.loads(lines[0])
    assert log["path"] == "/api/ask" and log["status"] == 200
    assert log["spent_usd"] == 0.0003 and log["timings_ms"] == {"search": 1.0}
    assert "question" not in log and "total_ms" in log and "connect_ms" in log


def test_deployed_messages_have_no_local_hints(monkeypatch):
    from botocore.exceptions import ClientError, NoCredentialsError

    monkeypatch.setattr(config, "DEPLOYED", True)
    assert api.no_database() == api.DATABASE_WAKING
    denied = ClientError({"Error": {"Code": "AccessDeniedException"}}, "Converse")
    for error in (denied, NoCredentialsError()):
        status, body = api._bedrock_error(error)
        assert status == 502 and "credentials" not in body["error"] and "local" not in body["error"]
    monkeypatch.setattr(config, "DEPLOYED", False)
    assert api.no_database() == api.NO_DATABASE


def test_deployed_cost_rows_are_printed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "DEPLOYED", True)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    costlog.log_cost("rerank", "cohere.rerank-v3-5:0", "queries", 1, 0.002)
    line = json.loads(capsys.readouterr().out.strip())
    assert line == {"cost": {"operation": "rerank", "model_id": "cohere.rerank-v3-5:0", "unit": "queries",
                             "quantity": 1, "est_cost_usd": 0.002}}
    assert costlog.row_count() == 1


def test_defaults_are_unchanged():
    # Local runs keep the Cycle 1 behaviour when none of the Lambda variables is set.
    names = ("RAG_RUNS_DIR", "BEDROCK_MAX_ATTEMPTS", "RERANK_MIN_INTERVAL_S", "BEDROCK_READ_TIMEOUT_S",
             "AWS_LAMBDA_FUNCTION_NAME", "DEMO_ENABLED", "API_DAILY_ASK_LIMIT")
    if any(name in os.environ for name in names):
        pytest.skip("a Lambda setting is set in this shell")
    assert config.RUNS_DIR == config.ROOT / "runs"
    assert config.BEDROCK_MAX_ATTEMPTS == 10
    assert config.RERANK_MIN_INTERVAL_S == 20
    assert config.BEDROCK_READ_TIMEOUT_S == 60 and config.BEDROCK_CONNECT_TIMEOUT_S == 60
    assert config.DEPLOYED is False
    assert config.DEMO_ENABLED is True and config.API_DAILY_ASK_LIMIT == 0


def test_lambda_settings_come_from_the_environment(tmp_path):
    env = {**os.environ, "RAG_RUNS_DIR": str(tmp_path), "BEDROCK_MAX_ATTEMPTS": "2", "RERANK_MIN_INTERVAL_S": "0",
           "AURORA_CONNECT_TIMEOUT_S": "22", "AURORA_CONNECT_ATTEMPTS": "1", "AURORA_RETRY_PAUSE_S": "3",
           "BEDROCK_CONNECT_TIMEOUT_S": "5", "BEDROCK_READ_TIMEOUT_S": "20", "AWS_LAMBDA_FUNCTION_NAME": "datasheet-rag",
           "DEMO_ENABLED": "False", "USAGE_TABLE": "datasheet-rag-usage", "API_DAILY_ASK_LIMIT": "50"}
    code = ("from rag import config as c; print(c.COST_LOG, c.BEDROCK_MAX_ATTEMPTS, c.RERANK_MIN_INTERVAL_S, "
            "c.AURORA_CONNECT_TIMEOUT_S, c.AURORA_CONNECT_ATTEMPTS, c.AURORA_RETRY_PAUSE_S, c.BEDROCK_CONNECT_TIMEOUT_S, "
            "c.BEDROCK_READ_TIMEOUT_S, c.DEPLOYED, c.DEMO_ENABLED, c.USAGE_TABLE, c.API_DAILY_ASK_LIMIT, sep='|')")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out.split("|") == [str(tmp_path / "cost_log.csv"), "2", "0.0", "22", "1", "3.0", "5.0", "20.0", "True",
                              "False", "datasheet-rag-usage", "50"]


def test_kb_ask_never_connects_and_logs_its_pipeline(capped, capsys, monkeypatch):
    # Cycle 2 step 8: the Knowledge Base pipeline answers without the database.
    monkeypatch.setattr(config, "KB_DAILY_ASK_LIMIT", 2)

    def kb_answer(question, document=None):
        return {"answer": "35 V [1]", "passages": [], "sources": [], "not_contained": False, "invalid": [],
                "cut_off": False, "timings_ms": {"retrieve": 1.0, "generate": 2.0}}

    capped.ask = lambda payload, open_conn, upload_store=None: api.ask(payload, open_conn, None, capped.counter,
                                                                       kb_answer)
    status, body = parsed(capped.call(v2("POST", "/api/ask", json.dumps({"question": "q", "pipeline": "kb"}))))
    assert status == 200 and body["setup"]["pipeline"] == "kb"
    assert capped.conns == []
    line = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert line["pipeline"] == "kb" and "connect_ms" not in line


@pytest.mark.parametrize("name, logged", [("kb", "kb"), (None, "ours"), ("x" * 500, "unknown"), ([1], "unknown")])
def test_only_a_known_pipeline_name_is_logged(fakes, capsys, name, logged):
    fakes.call(v2("POST", "/api/ask", json.dumps({"question": "q", "pipeline": name})))
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["pipeline"] == logged


# ---------------------------------------------------------------- uploads (Cycle 2 step 9a)

UPLOAD_ID = "0123456789abcdef"


def upload_call(fakes, event, store, presign=None, kb_status=None):
    return handler.lambda_handler(event, None, connect=fakes.connect, ask=fakes.ask, store=store,
                                  presign=presign or (lambda upload_id: {"post": {"url": "u", "fields": {}}}),
                                  kb_status=kb_status)


def test_start_upload_route_never_connects(fakes, monkeypatch, capsys):
    monkeypatch.setattr(config, "UPLOAD_DAILY_LIMIT", 0)
    store = uploads.MemoryStore()
    response = upload_call(fakes, v2("POST", "/api/uploads", json.dumps({"filename": "s.pdf", "size": 100})), store)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200 and uploads.valid_id(body["upload_id"]) and body["post"]["url"] == "u"
    assert fakes.conns == [] and fakes.asked == []
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["path"] == "/api/uploads" and line["upload"] is True and "connect_ms" not in line


def test_upload_status_route_does_not_log_the_id(fakes, capsys):
    store = uploads.MemoryStore()
    store.create(uploads.new_record(UPLOAD_ID, "s.pdf", kb=False))
    response = upload_call(fakes, v2("GET", f"/api/uploads/{UPLOAD_ID}"), store)
    assert response["statusCode"] == 200 and json.loads(response["body"])["status"] == "waiting"
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["path"] == "/api/uploads/{id}" and UPLOAD_ID not in json.dumps(line)
    assert fakes.conns == []


@pytest.mark.parametrize(("method", "path", "status"), [
    ("GET", "/api/uploads", 405),
    ("POST", f"/api/uploads/{UPLOAD_ID}", 405),
    ("GET", f"/api/uploads/{UPLOAD_ID}/file", 404),
    ("GET", "/api/uploads/", 404),
    ("GET", "/api/uploads/not-an-id", 404),
])
def test_upload_routes_refuse_the_wrong_shape(fakes, method, path, status):
    response = upload_call(fakes, v2(method, path, json.dumps({"filename": "s.pdf", "size": 1}) if method == "POST" else None),
                           uploads.MemoryStore())
    assert response["statusCode"] == status and fakes.conns == []


def test_upload_routes_are_off_with_the_demo(fakes, monkeypatch):
    monkeypatch.setattr(config, "DEMO_ENABLED", False)
    response = upload_call(fakes, v2("POST", "/api/uploads", json.dumps({"filename": "s.pdf", "size": 1})), uploads.MemoryStore())
    assert response["statusCode"] == 503


def test_ask_passes_the_upload_store_on(fakes):
    store = uploads.MemoryStore()
    seen = {}

    def ask(payload, open_conn, upload_store=None):
        seen["store"] = upload_store
        return 200, {}

    handler.lambda_handler(v2("POST", "/api/ask", json.dumps({"question": "q"})), None, connect=fakes.connect, ask=ask,
                           store=store)
    assert seen["store"] is store


# ---- the ingest function

def s3_event(key, size=1000, bucket="uploads-bucket"):
    return {"Records": [{"eventSource": "aws:s3", "s3": {"bucket": {"name": bucket},
                                                         "object": {"key": key, "size": size}}}]}


class FakeS3:
    def __init__(self, data=b"%PDF-1.4 data"):
        self.data, self.deleted, self.read = data, [], []

    def get_object(self, Bucket, Key):
        self.read.append(Key)
        return {"Body": __import__("io").BytesIO(self.data)}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def waiting(kb=False):
    store = uploads.MemoryStore()
    store.create(uploads.new_record(UPLOAD_ID, "s.pdf", kb=kb))
    store.take_lock(UPLOAD_ID, 60)
    return store


def test_ingest_handler_runs_the_upload_and_deletes_the_incoming_file(capsys):
    store, s3, ran = waiting(), FakeS3(), []

    def run(store_, upload_id, data, connect, to_kb):
        ran.append((upload_id, data, to_kb))
        store_.update(upload_id, status="ready", ours="ready", chunks=4)
        return store_.get(upload_id)

    result = handler.ingest_handler(s3_event(f"incoming/{UPLOAD_ID}.pdf"), None, store=store, s3=s3,
                                    connect=lambda: None, run=run)
    assert result == {"processed": 1}
    assert ran == [(UPLOAD_ID, b"%PDF-1.4 data", False)]
    assert s3.deleted == [f"incoming/{UPLOAD_ID}.pdf"]
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["ingest"] is True and line["status"] == "ready" and line["chunks"] == 4


@pytest.mark.parametrize("key", ["incoming/ffffffffffffffff.pdf", "incoming/../x.pdf", f"incoming/{UPLOAD_ID}.pdf"])
def test_ingest_handler_skips_a_file_without_a_waiting_record(key, capsys):
    store = waiting()
    store.update(UPLOAD_ID, status="ready")  # a retry of an upload that was already done
    s3 = FakeS3()
    handler.ingest_handler(s3_event(key), None, store=store, s3=s3, connect=lambda: None,
                           run=lambda *a, **k: pytest.fail("ran"))
    assert s3.read == [] and s3.deleted == [key]


def test_ingest_handler_ignores_records_outside_incoming():
    s3 = FakeS3()
    result = handler.ingest_handler(s3_event(f"kb/{UPLOAD_ID}.pdf"), None, store=waiting(), s3=s3,
                                    connect=lambda: None, run=lambda *a, **k: pytest.fail("ran"))
    assert result == {"processed": 0} and s3.deleted == []


def test_ingest_handler_refuses_an_oversized_file_unread(monkeypatch, capsys):
    monkeypatch.setattr(config, "UPLOAD_MAX_BYTES", 100)
    store, s3 = waiting(), FakeS3()
    handler.ingest_handler(s3_event(f"incoming/{UPLOAD_ID}.pdf", size=101), None, store=store, s3=s3,
                           connect=lambda: None, run=lambda *a, **k: pytest.fail("ran"))
    assert s3.read == [] and store.get(UPLOAD_ID)["status"] == "failed"
    assert store.take_lock("next", 60)  # released


def test_ingest_handler_marks_a_failed_read(capsys):
    class BrokenS3(FakeS3):
        def get_object(self, Bucket, Key):
            raise OSError("network")

    store, s3 = waiting(), BrokenS3()
    handler.ingest_handler(s3_event(f"incoming/{UPLOAD_ID}.pdf"), None, store=store, s3=s3, connect=lambda: None,
                           run=lambda *a, **k: pytest.fail("ran"))
    record = store.get(UPLOAD_ID)
    assert record["status"] == "failed" and "network" not in record["error"]
    assert s3.deleted == [f"incoming/{UPLOAD_ID}.pdf"] and store.take_lock("next", 60)


def test_ingest_handler_reads_url_encoded_keys():
    store, s3, ran = waiting(), FakeS3(), []
    handler.ingest_handler(s3_event(f"incoming%2F{UPLOAD_ID}.pdf"), None, store=store, s3=s3, connect=lambda: None,
                           run=lambda store_, upload_id, *a, **k: ran.append(upload_id) or {})
    assert ran == [UPLOAD_ID]


def test_the_api_zip_never_imports_pdfplumber():
    # The query Lambda has no pdfplumber: importing handler (and through it api, uploads, kb) must not need it.
    code = "import sys, handler; assert 'pdfplumber' not in sys.modules and 'rag.ingest' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
