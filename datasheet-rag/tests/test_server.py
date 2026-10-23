"""Layer 8b tests for rag.server: the real HTTP server on a free port, with the database and the API faked."""
import http.client
import json
import os
import threading

import psycopg
import pytest

from rag import api, server


class FakeConn:
    closed = False
    broken = False


@pytest.fixture
def running(tmp_path):
    """A server on 127.0.0.1 with a page, one PDF and fake API functions; yields (port, calls, state)."""
    web, pdfs = tmp_path / "web", tmp_path / "pdfs"
    web.mkdir()
    pdfs.mkdir()
    (web / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (pdfs / "lm35.pdf").write_bytes(b"%PDF-1.4 test")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    calls = {"ask": [], "connect": 0, "documents": 0}

    def connect():
        calls["connect"] += 1
        return FakeConn()

    def ask(payload, open_conn):
        try:  # as rag.api.ask does: the connection is asked for, and a database that is down is a 503
            with open_conn():
                pass
        except psycopg.OperationalError:
            return 503, {"error": api.NO_DATABASE}
        calls["ask"].append(payload)
        if payload.get("question") == "crash":
            raise KeyError("output")
        return 200, {"answer": "35 V [1].", "spent_usd": 0.0003}

    def documents(conn):
        calls["documents"] += 1
        return [{"name": "lm35", "part": "LM35", "revision": "r", "pdf_url": "/pdfs/lm35.pdf"},
                {"name": "esp32", "part": "ESP32", "revision": "r", "pdf_url": "https://vendor.example/esp32.pdf"}]

    srv = server.make_server(port=0, connect=connect, ask=ask, documents=documents, web_dir=web, pdf_dir=pdfs)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1], calls, srv.state
    srv.shutdown()
    srv.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def post(port, payload, content_type="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return request(port, "POST", "/api/ask", body, {"Content-Type": content_type})


def test_page(running):
    port, _, _ = running
    response, data = request(port, "GET", "/")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy")
    assert data.startswith(b"<!doctype html>")


def test_documents(running):
    port, _, _ = running
    response, data = request(port, "GET", "/api/documents")
    assert response.status == 200 and json.loads(data)[0]["name"] == "lm35"


def test_ask_round_trip(running):
    port, calls, _ = running
    response, data = post(port, {"question": "What is the LM35 supply voltage?", "document": "lm35"})
    assert response.status == 200
    assert json.loads(data) == {"answer": "35 V [1].", "spent_usd": 0.0003}
    assert calls["ask"] == [{"question": "What is the LM35 supply voltage?", "document": "lm35"}]


def test_ask_needs_json_content_type(running):
    # A form post from another site cannot send application/json without a preflight, which is never answered.
    port, calls, _ = running
    response, _ = post(port, {"question": "q"}, content_type="text/plain")
    assert response.status == 415 and calls["ask"] == []


def test_bad_json(running):
    port, calls, _ = running
    response, data = post(port, b"{not json")
    assert response.status == 400 and "JSON" in json.loads(data)["error"] and calls["ask"] == []


def test_body_too_large(running):
    port, calls, _ = running
    response, _ = post(port, {"question": "x" * 20_000})
    assert response.status == 413 and calls["ask"] == []


def test_pdf_from_the_allow_list(running):
    port, _, _ = running
    response, data = request(port, "GET", "/pdfs/lm35.pdf")
    assert response.status == 200 and response.getheader("Content-Type") == "application/pdf"
    assert data == b"%PDF-1.4 test"


@pytest.mark.parametrize("path", ["/pdfs/../secret.txt", "/pdfs/..%2Fsecret.txt", "/pdfs/other.pdf",
                                  "/pdfs/%2e%2e/secret.txt", "/secret.txt", "/api/nothing"])
def test_nothing_else_is_served(running, path):
    port, _, _ = running
    response, data = request(port, "GET", path)
    assert response.status == 404 and b"secret" not in data


def test_other_host_is_refused(running):
    # DNS rebinding: a site whose name resolves to 127.0.0.1 still sends its own name as Host.
    port, calls, _ = running
    response, _ = request(port, "POST", "/api/ask", json.dumps({"question": "q"}).encode(),
                          {"Content-Type": "application/json", "Host": "evil.example"})
    assert response.status == 403 and calls["ask"] == []


def test_database_down_is_503_and_reconnects(running):
    port, _, state = running

    def down():
        raise psycopg.OperationalError("refused")

    real_connect = state.connect
    state._conn, state.connect = None, down
    response, data = post(port, {"question": "q"})
    assert response.status == 503 and "docker start rag-pg" in json.loads(data)["error"]
    state.connect = real_connect
    response, _ = post(port, {"question": "q"})
    assert response.status == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]", "[::1]:8000", "LOCALHOST:8000"])
def test_local_host_names_are_accepted(running, host):
    port, _, _ = running
    response, _ = request(port, "GET", "/api/documents", headers={"Host": host})
    assert response.status == 200


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "[::1", ""])
def test_other_host_names_are_refused(running, host):
    port, _, _ = running
    response, _ = request(port, "GET", "/api/documents", headers={"Host": host})
    assert response.status == 403


def test_unexpected_error_still_answers_json(running, capsys):
    port, _, _ = running
    response, data = post(port, {"question": "crash"})
    assert response.status == 500 and "unexpected" in json.loads(data)["error"]


def test_vendor_addresses_are_not_on_the_allow_list(running):
    _, _, state = running
    assert state.pdf_files() == {"lm35.pdf"}


def test_pdf_allow_list_is_read_once(running):
    port, calls, _ = running
    for _ in range(3):
        response, _ = request(port, "GET", "/pdfs/lm35.pdf")
        assert response.status == 200
    assert calls["documents"] == 1


@pytest.mark.skipif(os.name != "nt", reason="the port sharing it guards against happens on Windows only")
def test_second_server_cannot_take_the_same_port(running):
    port, _, _ = running
    with pytest.raises(OSError):
        server.make_server(port=port)


def test_connection_is_reused(running):
    port, calls, _ = running
    post(port, {"question": "a"})
    post(port, {"question": "b"})
    request(port, "GET", "/api/documents")
    assert calls["connect"] == 1
