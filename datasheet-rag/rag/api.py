"""The JSON API of the web page, without HTTP: a request dict in, (status, response dict) out.

`rag.server` puts this behind a local HTTP server and `handler.py` behind Lambda; keeping HTTP out of here means both
call `ask` unchanged. `documents` is for the local server only (the deployed page has the list written in).

POST /api/ask   {"question": "...", "document": null | "<name>"}
  200 -> {question, answer, answer_parts: [{text, labels?}], not_contained,
          sources: [{label, document, part, page, page_label, title, pdf_url}], warnings, timings_ms, spent_usd, setup}
  4xx / 5xx -> {"error": "<what happened and what to do>"}
GET /api/documents -> 200 [{name, part, revision, pdf_url}]   (local server only)

pdf_url is /pdfs/<file> when the PDF is on this machine (rag.server serves it), else the vendor's https address, else
null.
"""
import contextlib
import traceback
from urllib.parse import quote

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, costlog, db, generate, pipeline, usage

NO_DATABASE = "Cannot reach Postgres. Start Docker Desktop, then run: docker start rag-pg"
# The same failures as seen by a visitor of the deployed page (config.DEPLOYED), with no local-machine hints.
DATABASE_WAKING = ("The database did not answer. It pauses when idle and takes about 20 seconds to wake up: "
                   "ask again in 20 seconds.")
AWS_FAILED = "The answer service could not reach the AI models ({detail}). Try again later."
DAILY_LIMIT = "The demo has reached today's limit of questions. Try again tomorrow (the day starts at 00:00 UTC)."
BUSY = "The service is busy. Try again in a minute."
DEMO_OFF = "The demo is switched off right now. Try again later."


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


def _source(source: dict, files: dict[str, str | None]) -> dict:
    url = _pdf_url(source["document"], files.get(source["document"]))
    return {
        "label": source["label"],
        "document": source["document"],
        "part": config.DOCUMENTS.get(source["document"], {}).get("part"),
        "page": source["page"],
        "page_label": source.get("page_label"),
        "title": generate.passage_label(source),
        # #page=N opens most browser PDF viewers at that page; `page` is the PDF position, not the printed number.
        "pdf_url": f"{url}#page={source['page']}" if url else None,
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


def ask(payload, open_conn, answer_fn=None, counter=None) -> tuple[int, dict]:
    """Answer one question from the page. Never raises for a bad request, an AWS refusal or a lost database.

    `open_conn()` gives a context manager that yields a database connection. It is entered only after the request
    passed every check and took a place under the daily cap, so nothing else wakes the database. `counter` is the
    daily counter (default rag.usage); tests pass a fake."""
    question, document, error = check_ask(payload)
    if error:
        return error
    capped = config.API_DAILY_ASK_LIMIT > 0
    day = usage.today()  # fixed once, so a place given back after midnight goes back to the day it was taken from
    if capped:
        counter = counter or usage
        try:
            counted = counter.take("asks", config.API_DAILY_ASK_LIMIT, day=day)
        except (ClientError, BotoCoreError, RuntimeError):
            # Without a counted place there is no answer: an unreachable or unset counter must not lift the cap.
            traceback.print_exc()
            return _error(503, BUSY)
        if counted is None:
            # "limit" tells the page this lasts until tomorrow, unlike the throttling 429 ("wait a minute").
            return 429, {"error": DAILY_LIMIT, "limit": "daily"}

    connected = False
    try:
        with open_conn() as conn:
            connected = True
            status, body = _answer(conn, question, document, answer_fn)
    except psycopg.OperationalError:
        status, body = _error(503, no_database())
    # The place goes back only when nothing was paid for: the database never answered, the datasheet is not
    # stored, or AWS throttled a model. A database lost partway through an answer keeps it (Bedrock may have run).
    if capped and (not connected or status in (400, 429)):
        with contextlib.suppress(ClientError, BotoCoreError):
            counter.release("asks", day=day)
    return status, body


def _answer(conn, question: str, document: str | None, answer_fn) -> tuple[int, dict]:
    files = {row["name"]: row["file"] for row in db.list_documents(conn)}
    if document is not None and document not in files:
        return _error(400, "Pick a datasheet from the list.")  # configured but not loaded in this database
    logged_before = costlog.row_count()
    try:
        result = (answer_fn or pipeline.answer_question)(conn, question, document=document)
    except (ClientError, BotoCoreError) as error:
        return _bedrock_error(error)
    except RuntimeError as error:
        # The reranker scored fewer documents than sent, or the answer model stopped for another reason.
        return _error(502, f"A step stopped without a usable answer: {error}")

    return 200, {
        "question": question,
        "answer": result["answer"],
        "answer_parts": answer_parts(result["answer"], result["passages"]),
        "not_contained": result["not_contained"],
        "sources": [_source(source, files) for source in result["sources"]],
        "warnings": pipeline.warnings(result),
        "timings_ms": result["timings_ms"],
        "spent_usd": costlog.spent_since(logged_before),
        "setup": {"strategy": config.DEFAULT_STRATEGY, "search": config.SEARCH_MODE, "candidates": config.N_CANDIDATES,
                  "passages": config.TOP_K, "model": config.GENERATE_MODEL_ID},
    }
