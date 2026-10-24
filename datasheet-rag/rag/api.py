"""The JSON API of the web page, without HTTP: a request dict in, (status, response dict) out.

`rag.server` puts this behind a local HTTP server and `handler.py` behind Lambda; keeping HTTP out of here means both
call `ask` unchanged. `documents` is for the local server only (the deployed page has the list written in).

POST /api/ask   {"question": "...", "document": null | "<name>", "upload_id": null | "<id>",
                 "pipeline": "ours" (default) | "kb"}
  200 -> {question, answer, answer_parts: [{text, labels?}], not_contained,
          sources: [{label, document, part, page, page_label, title, pdf_url, upload}], warnings, timings_ms,
          spent_usd, setup}
  4xx / 5xx -> {"error": "<what happened and what to do>"}
GET /api/documents -> 200 [{name, part, revision, pdf_url}]   (local server only)
POST /api/uploads {"filename": "x.pdf", "size": <bytes>}       (step 9a)
  200 -> {upload_id, filename, limits, post: {url, fields}}   (deployed: the browser posts the file to S3)
      or {upload_id, filename, limits, put_url}               (local: the browser PUTs the file to the server)
GET /api/uploads/<id> -> 200 the upload's record (rag.uploads.PUBLIC_FIELDS)

pdf_url is /pdfs/<file> when the PDF is on this machine (rag.server serves it), else the vendor's https address, else
null.
"""
import contextlib
import traceback
from urllib.parse import quote

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, costlog, db, generate, kb, pipeline, uploads, usage

NO_DATABASE = "Cannot reach Postgres. Start Docker Desktop, then run: docker start rag-pg"
# The same failures as seen by a visitor of the deployed page (config.DEPLOYED), with no local-machine hints.
DATABASE_WAKING = ("The database did not answer. It pauses when idle and takes about 20 seconds to wake up: "
                   "ask again in 20 seconds.")
AWS_FAILED = "The answer service could not reach the AI models ({detail}). Try again later."
DAILY_LIMIT = "The demo has reached today's limit of questions. Try again tomorrow (the day starts at 00:00 UTC)."
BUSY = "The service is busy. Try again in a minute."
DEMO_OFF = "The demo is switched off right now. Try again later."
UPLOAD_UNKNOWN = "That upload is not known or has expired (uploads are kept for 24 hours). Upload the PDF again."
UPLOAD_BUSY = "Another PDF is being processed right now. Try again in a few minutes."
UPLOAD_DAILY_LIMIT = "The demo has reached today's limit of uploads. Try again tomorrow (the day starts at 00:00 UTC)."


def no_database() -> str:
    return DATABASE_WAKING if config.DEPLOYED else NO_DATABASE


def _error(status: int, message: str) -> tuple[int, dict]:
    return status, {"error": message}


def _pdf_url(name: str, file: str | None) -> str | None:
    """Where a datasheet can be read: the local server's copy when running locally with the PDF on this machine,
    otherwise the vendor's own copy (config.DOCUMENTS "url", checked to be our revision on 2026-10-18, but it always
    serves the newest), otherwise None. Deployed, it is never a local copy: the vendors do not allow republishing."""
    if file and not config.DEPLOYED and (config.PDF_DIR / file).is_file():
        return "/pdfs/" + quote(file)
    return config.DOCUMENTS.get(name, {}).get("url")


def documents(conn) -> list[dict]:
    """The stored datasheets, for the page's filter: name, part, revision and the PDF's address."""
    return [{"name": row["name"], "part": row["part"], "revision": row["revision"],
             "pdf_url": _pdf_url(row["name"], row["file"])}
            for row in db.list_documents(conn)]


def _bedrock_error(error: Exception) -> tuple[int, dict]:
    """A refused or failed AWS call as a status and a message a person can act on."""
    code = error.response.get("Error", {}).get("Code", "") if isinstance(error, ClientError) else ""
    if code in ("ThrottlingException", "TooManyRequestsException"):
        return _error(429, "AWS is limiting calls right now (the reranker allows 3 a minute). "
                           "Wait a minute and ask again.")
    if config.DEPLOYED:
        return _error(502, AWS_FAILED.format(detail=code or type(error).__name__))
    if code == "AccessDeniedException":
        return _error(502, "AWS refused the call: the local AWS user needs bedrock:InvokeModel and bedrock:Rerank "
                           "on the project models.")
    if code:
        return _error(502, f"AWS refused the call ({code}). The server console has the details.")
    return _error(502, f"Could not call AWS ({type(error).__name__}). Check that ~\\.aws\\credentials holds a key "
                       "and that this machine is online.")


def _source(source: dict, files: dict[str, str | None], upload_title: str | None = None) -> dict:
    # An upload has no address: the page opens the visitor's own copy of the file.
    upload = bool(source["document"]) and source["document"].startswith(config.UPLOAD_PREFIX)
    url = None if upload else _pdf_url(source["document"], files.get(source["document"]))
    return {
        "label": source["label"],
        "document": source["document"],
        "part": upload_title if upload else config.DOCUMENTS.get(source["document"], {}).get("part"),
        "upload": upload,
        "page": source["page"],
        "page_label": source.get("page_label"),
        "title": generate.passage_label(source),
        # #page=N opens most browser PDF viewers at that page; `page` is the PDF position, not the printed number.
        # A Knowledge Base passage may have no page: then the link opens the datasheet, not a made-up page.
        "pdf_url": (f"{url}#page={source['page']}" if source["page"] is not None else url) if url else None,
    }


def answer_parts(answer: str, passages: list[dict]) -> list[dict]:
    """The answer cut into text and citations, by the same rules that picked the sources: {"text"} for plain text,
    {"text", "labels"} for a bracket that cites passages. The page links only these, so it never shows a copied
    footnote marker (GPIO16[2]) as a citation."""
    parts, last = [], 0
    for start, end, labels in generate.citation_spans(answer, passages):
        if start > last:
            parts.append({"text": answer[last:start]})
        parts.append({"text": answer[start:end], "labels": labels})
        last = end
    if last < len(answer):
        parts.append({"text": answer[last:]})
    return parts


def check_ask(payload) -> tuple[str | None, str | None, tuple[int, dict] | None]:
    """The question and datasheet of a request, or the error to answer. Needs no database, so a request that will
    be refused never wakes Aurora: the datasheets are the ones in config.DOCUMENTS, the same as the stored ones."""
    if not isinstance(payload, dict):
        return None, None, _error(400, "Send a JSON object with a question.")
    question, document = payload.get("question"), payload.get("document")
    if document == "":
        document = None
    if document is not None and (not isinstance(document, str) or document not in config.DOCUMENTS):
        return None, None, _error(400, "Pick a datasheet from the list.")
    if not isinstance(question, str) or not question.strip():
        return None, None, _error(400, "Type a question first.")
    question = question.strip()
    if len(question) > config.API_MAX_QUESTION_CHARS:
        return None, None, _error(400, f"Keep the question under {config.API_MAX_QUESTION_CHARS} characters "
                                       f"(it has {len(question)}).")
    return question, document, None


def refresh_kb(record: dict, store, kb_status) -> dict:
    """While the KB syncs an upload, ask `kb_status(job_id)` whether the job has finished, and move the record on.
    Called by the status poll and by a question, so the KB side becomes ready even when the page stopped polling."""
    if record.get("kb") != "syncing" or not record.get("kb_job") or not kb_status:
        return record
    try:
        job, failed = kb_status(record["kb_job"])
    except (ClientError, BotoCoreError):
        traceback.print_exc()  # asked again next time
        return record
    state = ("failed" if failed else "ready") if job in kb.JOB_DONE else "failed" if job in kb.JOB_FAILED else None
    if state:
        record = {**record, "kb": state}
        store.update(record["upload_id"], kb=state)
    return record


def check_upload(payload: dict, name: str, store=None, kb_status=None) -> tuple[dict | None, tuple[int, dict] | None]:
    """The record of the upload a question is about, or (None, None) when it names none, or the error to answer.
    Read from the upload store (DynamoDB deployed), never the database, and only when the request names one."""
    upload_id = payload.get("upload_id")
    if upload_id in (None, ""):
        return None, None
    if not uploads.valid_id(upload_id):
        return None, _error(400, UPLOAD_UNKNOWN)
    if payload.get("document") not in (None, ""):
        return None, _error(400, "Ask about one datasheet or your upload, not both.")
    try:
        record = (store or uploads.default_store()).get(upload_id)
    except (ClientError, BotoCoreError, RuntimeError):
        traceback.print_exc()
        return None, _error(503, BUSY)
    if record is None:
        return None, _error(404, UPLOAD_UNKNOWN)
    if name == "kb":
        try:
            record = refresh_kb(record, store or uploads.default_store(), kb_status)
        except (ClientError, BotoCoreError, RuntimeError):
            traceback.print_exc()
    state = record.get(name)
    if state == "ready":
        return record, None
    if record.get("status") == "failed":
        return None, _error(409, record.get("error") or "Your PDF could not be processed.")
    if state in ("off", "failed"):
        other = "Custom pipeline" if name == "kb" else "AWS managed"
        return None, _error(409, f"Your PDF is not available on this pipeline. Ask with {other} instead.")
    return None, _error(409, "Your PDF is not ready on this pipeline yet. Wait until the upload says it is ready.")


def check_pipeline(payload: dict) -> tuple[str | None, tuple[int, dict] | None]:
    """Which pipeline answers: "ours" (the default) or "kb" (the Managed Knowledge Base), or the error to answer."""
    name = payload.get("pipeline")
    if name is None:
        name = "ours"
    if not isinstance(name, str) or name not in config.PIPELINES:
        return None, _error(400, "Pick a pipeline: ours or kb.")
    return name, None


def ask(payload, open_conn, answer_fn=None, counter=None, kb_answer_fn=None,
        upload_store=None, kb_status=None) -> tuple[int, dict]:
    """Answer one question from the page. Never raises for a bad request, an AWS refusal or a lost database.

    `open_conn()` gives a context manager that yields a database connection. It is entered only after the request
    passed every check and took a place under the daily cap, so nothing else wakes the database, and never for the
    Knowledge Base pipeline. `counter` is the daily counter (default rag.usage); tests pass fakes for it and for
    the two answer functions. A question about an upload (`upload_id`) is checked against its record in
    `upload_store` (default rag.uploads.default_store()) before anything is counted, paid or opened."""
    question, document, error = check_ask(payload)
    if error:
        return error
    name, error = check_pipeline(payload)
    if error:
        return error
    if kb_status is None and config.KB_UPLOAD_DATA_SOURCE_ID:
        kb_status = kb.ingestion_status
    record, error = check_upload(payload, name, upload_store, kb_status)
    if error:
        return error
    upload_title = None
    if record is not None:
        document, upload_title = uploads.document_name(record["upload_id"]), uploads.title_of(record["filename"])
    # Each pipeline has its own count and cap.
    kind, limit = ("asks", config.API_DAILY_ASK_LIMIT) if name == "ours" else ("kb_asks", config.KB_DAILY_ASK_LIMIT)
    capped = limit > 0
    day = usage.today()  # fixed once, so a place given back after midnight goes back to the day it was taken from
    if capped:
        counter = counter or usage
        try:
            counted = counter.take(kind, limit, day=day)
        except (ClientError, BotoCoreError, RuntimeError):
            # Without a counted place there is no answer: an unreachable or unset counter must not lift the cap.
            traceback.print_exc()
            return _error(503, BUSY)
        if counted is None:
            # "limit" tells the page this lasts until tomorrow, unlike the throttling 429 ("wait a minute").
            return 429, {"error": DAILY_LIMIT, "limit": "daily"}

    if name == "kb":
        logged_before = costlog.row_count()
        status, body = _answer_kb(question, document, kb_answer_fn, upload_title)
        # The place goes back only when the cost log shows nothing was paid for (KB_ID unset, Retrieve refused or
        # throttled). An unreadable log keeps the place, so a failure never lifts the cap.
        if capped and status != 200 and costlog.spent_since(logged_before) == 0:
            with contextlib.suppress(ClientError, BotoCoreError):
                counter.release(kind, day=day)
        return status, body

    connected = False
    try:
        with open_conn() as conn:
            connected = True
            status, body = _answer(conn, question, document, answer_fn, upload_title)
    except psycopg.OperationalError:
        status, body = _error(503, no_database())
    # The place goes back only when nothing was paid for: the database never answered, the datasheet is not
    # stored, or AWS throttled a model. A database lost partway through an answer keeps it (Bedrock may have run).
    if capped and (not connected or status in (400, 429)):
        with contextlib.suppress(ClientError, BotoCoreError):
            counter.release(kind, day=day)
    return status, body


def _answer(conn, question: str, document: str | None, answer_fn, upload_title=None) -> tuple[int, dict]:
    # Uploads whose 24 hours are up are deleted on the next question as well as the next upload; no search reads them.
    try:
        db.delete_expired_uploads(conn)
    except psycopg.Error:
        traceback.print_exc()
    files = {row["name"]: row["file"] for row in db.list_documents(conn)}
    if document is not None and document not in files and upload_title is None:
        return _error(400, "Pick a datasheet from the list.")  # configured but not loaded in this database
    logged_before = costlog.row_count()
    try:
        result = (answer_fn or pipeline.answer_question)(conn, question, document=document)
    except (ClientError, BotoCoreError) as error:
        return _bedrock_error(error)
    except RuntimeError as error:
        # The reranker scored fewer documents than sent, or the answer model stopped for another reason.
        return _error(502, f"A step stopped without a usable answer: {error}")

    setup = {"pipeline": "ours", "strategy": config.DEFAULT_STRATEGY, "search": config.SEARCH_MODE,
             "candidates": config.N_CANDIDATES, "passages": config.TOP_K, "model": config.GENERATE_MODEL_ID}
    return 200, _body(question, result, files, pipeline.warnings(result), setup, logged_before, upload_title)


def _answer_kb(question: str, document: str | None, kb_answer_fn, upload_title=None) -> tuple[int, dict]:
    """The Knowledge Base pipeline: no database. The PDFs are named after their datasheet, as kb_prepare uploads
    them, so the source links are built from config.DOCUMENTS alone."""
    logged_before = costlog.row_count()
    try:
        result = (kb_answer_fn or kb.answer_question)(question, document=document)
    except (ClientError, BotoCoreError) as error:
        return _bedrock_error(error)
    except RuntimeError as error:
        # KB_ID is not set, or the answer model stopped for another reason.
        return _error(502, f"A step stopped without a usable answer: {error}")
    files = {name: f"{name}.pdf" for name in config.DOCUMENTS}
    setup = {"pipeline": "kb", "search": "Managed Knowledge Base (hybrid)", "reranker": "managed",
             "passages": config.TOP_K, "model": config.GENERATE_MODEL_ID}
    return 200, _body(question, result, files, kb.warnings(result), setup, logged_before, upload_title)


def _body(question: str, result: dict, files: dict, warnings: list[str], setup: dict, logged_before,
          upload_title=None) -> dict:
    """The 200 answer of either pipeline."""
    return {
        "question": question,
        "answer": result["answer"],
        "answer_parts": answer_parts(result["answer"], result["passages"]),
        "not_contained": result["not_contained"],
        "sources": [_source(source, files, upload_title) for source in result["sources"]],
        "warnings": warnings,
        "timings_ms": result["timings_ms"],
        "spent_usd": costlog.spent_since(logged_before),
        "setup": setup,
    }


# ---------------------------------------------------------------- uploads (step 9a)

def check_upload_request(payload) -> tuple[str | None, tuple[int, dict] | None]:
    """The cleaned file name of an upload request {"filename", "size"}, or the error to answer."""
    if not isinstance(payload, dict):
        return None, _error(400, "Send a JSON object with the file's name and size.")
    filename = uploads.clean_filename(payload.get("filename"))
    if filename is None:
        return None, _error(400, "Choose a PDF file (its name must end in .pdf).")
    size = payload.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None, _error(400, "The file is empty.")
    if size > config.UPLOAD_MAX_BYTES:
        return None, _error(413, uploads.TOO_LARGE)
    return filename, None


def _limits() -> dict:
    return {"max_bytes": config.UPLOAD_MAX_BYTES, "max_pages": config.UPLOAD_MAX_PAGES,
            "keep_hours": config.UPLOAD_KEEP_HOURS}


def start_upload(payload, store, presign, counter=None, to_kb: bool = False) -> tuple[int, dict]:
    """Make a place for one upload: checks, the daily cap, the one-at-a-time lock, the record, then where to send the
    file (`presign(upload_id)` gives {"post": ...} deployed or {"put_url": ...} locally). Touches no database."""
    filename, error = check_upload_request(payload)
    if error:
        return error
    capped = config.UPLOAD_DAILY_LIMIT > 0
    day = usage.today()
    counter = counter or usage
    try:
        if capped and counter.take("uploads", config.UPLOAD_DAILY_LIMIT, day=day) is None:
            return 429, {"error": UPLOAD_DAILY_LIMIT, "limit": "daily"}
    except (ClientError, BotoCoreError, RuntimeError):
        traceback.print_exc()
        return _error(503, BUSY)

    upload_id = uploads.new_id()
    taken = False
    try:
        taken = store.take_lock(upload_id, config.UPLOAD_LOCK_WAIT_S)
        if not taken:
            status, body = 429, {"error": UPLOAD_BUSY}
        else:
            store.create(uploads.new_record(upload_id, filename, kb=to_kb))
            return 200, {"upload_id": upload_id, "filename": filename, "limits": _limits(), **presign(upload_id)}
    except (ClientError, BotoCoreError, RuntimeError):
        traceback.print_exc()
        status, body = _error(503, BUSY)
    # Nothing was uploaded: the place under the cap and the lock go back.
    if taken:
        with contextlib.suppress(ClientError, BotoCoreError, RuntimeError):
            store.release_lock(upload_id)
    if capped:
        with contextlib.suppress(ClientError, BotoCoreError, RuntimeError):
            counter.release("uploads", day=day)
    return status, body


def upload_status(upload_id, store, kb_status=None) -> tuple[int, dict]:
    """The upload's record for the page. While the KB is syncing, `kb_status(job_id)` (kb.ingestion_status deployed)
    is asked whether the job has finished, and the record is moved on. Touches no database."""
    if not uploads.valid_id(upload_id):
        return _error(404, UPLOAD_UNKNOWN)
    try:
        record = store.get(upload_id)
        if record is None:
            return _error(404, UPLOAD_UNKNOWN)
        record = refresh_kb(record, store, kb_status)
    except (ClientError, BotoCoreError, RuntimeError):
        traceback.print_exc()
        return _error(503, BUSY)
    return 200, uploads.public(record)
