"""The Lambda entry point (Cycle 2): the JSON API of `rag.api` behind API Gateway.

Lambda handler setting: handler.lambda_handler

POST /api/ask        {"question": "...", "document": null | "<name>"} -> rag.api.ask
GET  /api/documents  -> rag.api.documents

Both API Gateway payload formats are read: HTTP API (version 2.0) and REST API (version 1.0); a stage name at the
start of the path is ignored. CORS headers are API Gateway's job. Each request opens its own database connection
and closes it before returning: Aurora does not pause while a connection is open, and a frozen container would
keep one open for as long as Lambda keeps the container. Each request prints one JSON line (route, status,
timings, spend), so CloudWatch keeps what the cost log in /tmp loses when the container goes.
"""
import base64
import binascii
import contextlib
import json
import time
import traceback

import psycopg

from rag import api, config, db

ROUTES = {"/api/ask": "POST", "/api/documents": "GET"}


def _request(event: dict) -> tuple[str, str, dict, str | bytes | None]:
    """Method, path (without a stage name), headers (lower-case names) and body of an HTTP API (2.0) or REST API
    (1.0) event. Raises ValueError for a body marked base64 that is not."""
    context = event.get("requestContext") or {}
    http = context.get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "").upper()
    path = event.get("rawPath") or event.get("path") or ""
    stage = context.get("stage")
    if stage and stage != "$default" and path.startswith(f"/{stage}/"):
        # An HTTP API stage other than $default puts its name in rawPath: /prod/api/ask.
        path = path[len(stage) + 1:]
    headers = {str(name).lower(): str(value) for name, value in (event.get("headers") or {}).items()}
    body = event.get("body")
    if body is not None and event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body, validate=True)
        except binascii.Error as error:
            raise ValueError("body is not valid base64") from error
    return method, path, headers, body


def _response(status: int, data) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json; charset=utf-8"},
            "body": json.dumps(data, ensure_ascii=False)}


def _error(status: int, message: str) -> dict:
    return _response(status, {"error": message})


def _payload(headers: dict, body) -> tuple[object, dict | None]:
    """The parsed JSON body, or None and the error response."""
    # A JSON content type cannot be sent across sites without a CORS preflight, which only the page's origin passes.
    if not headers.get("content-type", "").lower().startswith("application/json"):
        return None, _error(415, "Send the question as JSON.")
    if not body:
        return None, _error(400, "The request body is missing.")
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    if len(raw) > config.API_MAX_BODY_BYTES:
        return None, _error(413, "The request body is too large.")
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _error(400, "The request body is not valid JSON.")


def lambda_handler(event, context, connect=None, ask=None, documents=None) -> dict:
    """One API Gateway request. `connect`, `ask` and `documents` replace the real ones in tests."""
    connect, ask, documents = connect or db.connect, ask or api.ask, documents or api.documents
    start = time.perf_counter()
    log = {}
    try:
        response = _handle(event if isinstance(event, dict) else {}, connect, ask, documents, log)
    except Exception:  # noqa: BLE001 (any failure still gets an answer the page can show, and a log line)
        traceback.print_exc()
        response = _error(500, "The server hit an unexpected error. Its log has the details.")
    log["status"] = response["statusCode"]
    log["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
    print(json.dumps(log), flush=True)
    return response


def _handle(event, connect, ask, documents, log) -> dict:
    try:
        method, path, headers, body = _request(event)
    except ValueError:
        return _error(400, "The request body is not valid base64.")
    log.update({"method": method, "path": path})
    if path not in ROUTES:
        return _error(404, "Not found.")
    if method != ROUTES[path]:
        return _error(405, f"Use {ROUTES[path]} for {path}.")

    payload = None
    if path == "/api/ask":
        payload, error = _payload(headers, body)
        if error:
            return error

    connect_start = time.perf_counter()
    try:
        conn = connect()
    except psycopg.OperationalError:
        traceback.print_exc()
        return _error(503, api.no_database())
    finally:
        log["connect_ms"] = round((time.perf_counter() - connect_start) * 1000, 1)

    try:
        if path == "/api/documents":
            return _response(200, documents(conn))
        status, data = ask(conn, payload)
    except psycopg.OperationalError:
        traceback.print_exc()
        return _error(503, api.no_database())
    finally:
        with contextlib.suppress(Exception):  # a connection that is already broken may fail to close
            conn.close()

    log.update({key: data[key] for key in ("timings_ms", "spent_usd") if key in data})
    return _response(status, data)
