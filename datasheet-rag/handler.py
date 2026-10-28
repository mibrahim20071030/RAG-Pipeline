"""The Lambda entry point (Cycle 2): the JSON API of `rag.api` behind API Gateway.

Lambda handler settings: handler.lambda_handler (the API function), handler.ingest_handler (the ingest function,
step 9a, started by S3 when an uploaded PDF lands under incoming/; it is the only one with pdfplumber in its zip)

POST /api/ask           {"question": "...", "document": null | "<name>", "upload_id": ...} -> rag.api.ask
POST /api/uploads       {"filename": "...", "size": ...}  -> rag.api.start_upload (a presigned POST to S3)
GET  /api/uploads/<id>                                     -> rag.api.upload_status
POST /api/feedback      {"answer_id": "...", "rating": ...} -> rag.api.feedback (step 9b)
     /api/review/...                                       -> rag.review.route (step 9b; a reviewer's Cognito login)

The reviewer routes are behind API Gateway's JWT authorizer, which refuses a request without a valid token before
this function runs. Here the token's groups must also include REVIEWER_GROUP (REVIEW_AUTH, on when deployed), so a
login alone is not enough. /api/review/config is the exception: the page reads it before logging in.

Both API Gateway payload formats are read: HTTP API (version 2.0) and REST API (version 1.0); a stage name at the
start of the path is ignored. CORS headers are API Gateway's job.

A request that will not be answered never reaches the database (Cycle 2 step 7b): the off switch
(DEMO_ENABLED=false), the route, the body and the question are checked first, and the daily cap is counted in
DynamoDB; only then does `rag.api.ask` open the connection. The log line has `connect_ms` only when it did.
Each request that is answered opens its own database connection and closes it before returning: Aurora does not pause while a connection is open, and a frozen container would
keep one open for as long as Lambda keeps the container. Each request prints one JSON line (route, status,
timings, spend), so CloudWatch keeps what the cost log in /tmp loses when the container goes.
"""
import base64
import binascii
import contextlib
import json
import time
import traceback
from urllib.parse import unquote_plus

import psycopg

from rag import api, config, db, kb, records, review, uploads

# GET /api/documents was removed in step 7b: the page has the list written in. "/api/uploads/{id}" stands for
# /api/uploads/<any id>.
ROUTES = {"/api/ask": "POST", "/api/uploads": "POST", "/api/uploads/{id}": "GET", "/api/feedback": "POST"}
UPLOAD_PATH = "/api/uploads/"
REVIEW_CONFIG = review.PREFIX + "config"


def _query(event: dict) -> dict:
    return {str(name): str(value) for name, value in (event.get("queryStringParameters") or {}).items()}


def _claims(event: dict) -> dict:
    """The token's claims as API Gateway's authorizer passed them: HTTP API (jwt.claims) or REST API (claims)."""
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    return (authorizer.get("jwt") or {}).get("claims") or authorizer.get("claims") or {}


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


@contextlib.contextmanager
def _open_conn(connect, log: dict):
    """A connection for one request, timed into the log line (`connect_ms`), closed when the request ends. A database
    that does not answer is printed to the log and raised for `rag.api.ask` to turn into a 503."""
    start = time.perf_counter()
    try:
        conn = connect()
    except psycopg.OperationalError:
        traceback.print_exc()
        raise
    finally:
        log["connect_ms"] = round((time.perf_counter() - start) * 1000, 1)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):  # a connection that is already broken may fail to close
            conn.close()


def _route(path: str) -> str:
    if path.startswith(UPLOAD_PATH) and path.count("/") == 3 and len(path) > len(UPLOAD_PATH):
        return "/api/uploads/{id}"
    return path


def lambda_handler(event, context, connect=None, ask=None, store=None, presign=None, kb_status=None,
                   answers=None, review_deps=None) -> dict:
    """One API Gateway request. `connect`, `ask`, the upload store, `presign`, `kb_status`, the answer and feedback
    store (`answers`) and the reviewer routes' `review_deps` replace the real ones in tests."""
    connect, ask = connect or db.connect, ask or api.ask
    start = time.perf_counter()
    log = {}
    try:
        response = _handle(event if isinstance(event, dict) else {}, connect, ask, log,
                           (store, presign or uploads.presign_post, kb_status or kb.ingestion_status),
                           (answers, review_deps))
    except Exception:  # noqa: BLE001 (any failure still gets an answer the page can show, and a log line)
        traceback.print_exc()
        response = _error(500, "The server hit an unexpected error. Its log has the details.")
    log["status"] = response["statusCode"]
    log["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
    print(json.dumps(log), flush=True)
    return response


def _handle(event, connect, ask, log, upload_parts, feedback_parts) -> dict:
    if not config.DEMO_ENABLED:
        return _response(503, {"error": api.DEMO_OFF, "off": True})
    try:
        method, path, headers, body = _request(event)
    except ValueError:
        return _error(400, "The request body is not valid base64.")
    answers, review_deps = feedback_parts
    if path.startswith(review.PREFIX):
        return _review(event, method, path, headers, body, connect, log, answers, review_deps)
    route = _route(path)
    log.update({"method": method, "path": route})  # an upload id is not logged
    if route not in ROUTES:
        return _error(404, "Not found.")
    if method != ROUTES[route]:
        return _error(405, f"Use {ROUTES[route]} for {route}.")

    store, presign, kb_status = upload_parts
    store = store or uploads.default_store()
    if route == "/api/uploads/{id}":
        status, data = api.upload_status(path.removeprefix(UPLOAD_PATH), store, kb_status)
        return _response(status, data)

    payload, error = _payload(headers, body)
    if error:
        return error
    if route == "/api/uploads":
        status, data = api.start_upload(payload, store, presign, to_kb=uploads.kb_configured())
        log["upload"] = status == 200
        return _response(status, data)
    answers = answers or records.default_store()
    if route == "/api/feedback":
        # Never connects to the database. The comment is not logged.
        status, data = api.feedback(payload, answers)
        if isinstance(payload, dict) and payload.get("rating") in ("up", "down"):
            log["rating"] = payload["rating"]
        return _response(status, data)

    if isinstance(payload, dict):
        # Logged before the API checks it, so an unknown value is logged as such, never copied in.
        name = "ours" if payload.get("pipeline") is None else payload["pipeline"]  # as api.check_pipeline reads it
        log["pipeline"] = name if name in config.PIPELINES else "unknown"
    status, data = ask(payload, lambda: _open_conn(connect, log), upload_store=store, answer_store=answers)
    log.update({key: data[key] for key in ("timings_ms", "spent_usd") if key in data})
    if data.get("answer_id"):
        log["answer_saved"] = True
    return _response(status, data)


def _review(event, method, path, headers, body, connect, log, answers, deps) -> dict:
    """A reviewer route: only for a token whose groups include REVIEWER_GROUP (see the module notes)."""
    log.update({"method": method, "path": "/api/review/..."})  # ids are not logged
    if path == REVIEW_CONFIG and method == "GET":
        return _response(200, review.config_body())
    reviewer = "local"
    if config.REVIEW_AUTH:
        claims = _claims(event)
        if config.REVIEWER_GROUP not in review.groups(claims):
            return _error(403, "This page is for reviewers. Log in with a reviewer's account.")
        reviewer = str(claims.get("sub") or claims.get("username") or "unknown")
    log["reviewer"] = reviewer
    payload = None
    if method == "POST":
        payload, error = _payload(headers, body)
        if error:
            return error
    elif method != "GET":
        return _error(405, "Use GET or POST.")
    deps = deps or review.Deps(store=answers or records.default_store(), open_conn=lambda: _open_conn(connect, log))
    status, data = review.route(method, path, _query(event), payload, reviewer, deps)
    return _response(status, data)


# ---------------------------------------------------------------- the ingest function (step 9a)

def _incoming(record: dict) -> tuple[str, str, int] | None:
    """(bucket, key, size) of an S3 "object created" record under UPLOAD_INCOMING, or None for anything else."""
    s3 = record.get("s3") or {}
    bucket = (s3.get("bucket") or {}).get("name")
    obj = s3.get("object") or {}
    key = unquote_plus(obj.get("key") or "")  # S3 event keys are URL-encoded
    if not bucket or not key.startswith(config.UPLOAD_INCOMING):
        return None
    return bucket, key, int(obj.get("size") or 0)


def ingest_handler(event, context, store=None, s3=None, connect=None, run=None) -> dict:
    """S3 started this for each new file under incoming/: ingest it as the upload its name says, then delete it.

    A file with no waiting record (unknown, expired, or a retry after the first try took it) is deleted unread.
    S3's own size condition already held the file to UPLOAD_MAX_BYTES; it is checked again here."""
    from rag import ingest  # pdfplumber is in this function's zip only

    store, connect = store or uploads.default_store(), connect or db.connect
    s3 = s3 or uploads._s3()
    done = []
    for record in (event or {}).get("Records", []):
        found = _incoming(record)
        if found is None:
            continue
        bucket, key, size = found
        upload_id = key.removeprefix(config.UPLOAD_INCOMING).removesuffix(".pdf")
        start = time.perf_counter()
        log = {"ingest": True, "size": size}
        try:
            upload = store.get(upload_id) if uploads.valid_id(upload_id) else None
            if upload is None or upload.get("status") != "waiting":
                log["skipped"] = True
            elif size > config.UPLOAD_MAX_BYTES:
                store.update(upload_id, status="failed", ours="failed", kb="off",
                             error=uploads.TOO_LARGE)
                store.release_lock(upload_id)
            else:
                data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                final = (run or ingest.run_upload)(store, upload_id, data, connect, to_kb=uploads.kb_configured())
                log.update({key_: final.get(key_) for key_ in ("status", "ours", "kb", "pages", "chunks")})
        except Exception:  # noqa: BLE001 (one bad file must not stop the others, and must still be deleted)
            traceback.print_exc()
            log["status"] = "error"
            # Reading the file failed before run_upload took over: the record must not stay "waiting".
            if uploads.valid_id(upload_id):
                with contextlib.suppress(Exception):
                    if (store.get(upload_id) or {}).get("status") == "waiting":
                        store.update(upload_id, status="failed", ours="failed", kb="off", error=ingest.FAILED)
                        store.release_lock(upload_id)
        finally:
            with contextlib.suppress(Exception):
                s3.delete_object(Bucket=bucket, Key=key)
        log["total_ms"] = round((time.perf_counter() - start) * 1000, 1)
        print(json.dumps(log), flush=True)
        done.append(log)
    return {"processed": len(done)}
