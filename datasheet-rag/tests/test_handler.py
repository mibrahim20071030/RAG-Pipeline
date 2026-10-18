"""Cycle 2 step 4 tests: the Lambda handler and the settings Lambda sets from its environment.

No database and no AWS call: connect, ask and documents are fakes.
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
from rag import api, config, costlog

ROOT = Path(__file__).resolve().parent.parent


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.broken = False

    def close(self):
        self.closed = True


class Fakes:
    """Stand-ins for db.connect, api.ask and api.documents that record their calls."""

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

    def ask(self, conn, payload):
        self.asked.append((conn, payload))
        if self.ask_error:
            raise self.ask_error
        return self.ask_result

    def documents(self, conn):
        return [{"name": "lm35", "part": "LM35", "revision": "SNIS159H", "pdf_url": None}]

    def call(self, event):
        return handler.lambda_handler(event, None, connect=self.connect, ask=self.ask, documents=self.documents)


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


def test_documents(fakes):
    status, body = parsed(fakes.call(v2("GET", "/api/documents")))
    assert status == 200 and body[0]["name"] == "lm35"


def test_status_from_ask_is_passed_on(fakes):
    fakes.ask_result = (400, {"error": "Type a question first."})
    assert parsed(fakes.call(v2("POST", "/api/ask", "{}"))) == (400, {"error": "Type a question first."})


@pytest.mark.parametrize("event, status", [
    (v2("GET", "/"), 404),
    (v2("GET", "/pdfs/lm35.pdf"), 404),
    (v2("GET", "/api/ask"), 405),
    (v2("POST", "/api/documents", "{}"), 405),
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
    fakes.call(v2("GET", "/api/documents"))
    assert len(fakes.conns) == 2 and all(conn.closed for conn in fakes.conns)
    assert fakes.asked[0][0] is fakes.conns[0]


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
    status, body = parsed(fakes.call(v2("GET", "/api/documents")))
    assert status == 500 and "error" in body
    log = json.loads(next(line for line in capsys.readouterr().out.splitlines() if line.startswith("{")))
    assert log["status"] == 500 and log["path"] == "/api/documents"


def test_database_that_does_not_answer_gives_503(fakes):
    fakes.connect_error = psycopg.OperationalError("timeout expired")
    status, body = parsed(fakes.call(v2("GET", "/api/documents")))
    assert status == 503 and body["error"] == api.DATABASE_WAKING
    fakes.connect_error = None
    assert parsed(fakes.call(v2("GET", "/api/documents")))[0] == 200


def test_database_lost_during_ask_gives_503(fakes):
    fakes.ask_error = psycopg.OperationalError("server closed the connection")
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
             "AWS_LAMBDA_FUNCTION_NAME")
    if any(name in os.environ for name in names):
        pytest.skip("a Lambda setting is set in this shell")
    assert config.RUNS_DIR == config.ROOT / "runs"
    assert config.BEDROCK_MAX_ATTEMPTS == 10
    assert config.RERANK_MIN_INTERVAL_S == 20
    assert config.BEDROCK_READ_TIMEOUT_S == 60 and config.BEDROCK_CONNECT_TIMEOUT_S == 60
    assert config.DEPLOYED is False


def test_lambda_settings_come_from_the_environment(tmp_path):
    env = {**os.environ, "RAG_RUNS_DIR": str(tmp_path), "BEDROCK_MAX_ATTEMPTS": "2", "RERANK_MIN_INTERVAL_S": "0",
           "AURORA_CONNECT_TIMEOUT_S": "22", "AURORA_CONNECT_ATTEMPTS": "1", "AURORA_RETRY_PAUSE_S": "3",
           "BEDROCK_CONNECT_TIMEOUT_S": "5", "BEDROCK_READ_TIMEOUT_S": "20", "AWS_LAMBDA_FUNCTION_NAME": "datasheet-rag"}
    code = ("from rag import config as c; print(c.COST_LOG, c.BEDROCK_MAX_ATTEMPTS, c.RERANK_MIN_INTERVAL_S, "
            "c.AURORA_CONNECT_TIMEOUT_S, c.AURORA_CONNECT_ATTEMPTS, c.AURORA_RETRY_PAUSE_S, c.BEDROCK_CONNECT_TIMEOUT_S, "
            "c.BEDROCK_READ_TIMEOUT_S, c.DEPLOYED, sep='|')")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out.split("|") == [str(tmp_path / "cost_log.csv"), "2", "0.0", "22", "1", "3.0", "5.0", "20.0", "True"]
