"""The JSON API of the web page, without HTTP: a request dict in, (status, response dict) out.

`rag.server` puts this behind a local HTTP server. Keeping HTTP out of here means another front (for example a
cloud function) can call `ask` and `documents` unchanged.

POST /api/ask   {"question": "...", "document": null | "<name>"}
  200 -> {question, answer, answer_parts: [{text, labels?}], not_contained,
          sources: [{label, document, part, page, page_label, title, pdf_url}], warnings, timings_ms, spent_usd, setup}
  4xx / 5xx -> {"error": "<what happened and what to do>"}
GET /api/documents -> 200 [{name, part, revision, pdf_url}]
"""
from urllib.parse import quote

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, costlog, db, generate, pipeline

NO_DATABASE = "Cannot reach Postgres. Start Docker Desktop, then run: docker start rag-pg"


def _error(status: int, message: str) -> tuple[int, dict]:
    return status, {"error": message}


def _pdf_url(file: str | None) -> str | None:
    """Where the server serves a datasheet, or None when its PDF is not on this machine."""
    if not file or not (config.PDF_DIR / file).is_file():
        return None
    return "/pdfs/" + quote(file)


def documents(conn) -> list[dict]:
    """The stored datasheets, for the page's filter: name, part, revision and the PDF's address."""
    return [{"name": row["name"], "part": row["part"], "revision": row["revision"], "pdf_url": _pdf_url(row["file"])}
            for row in db.list_documents(conn)]


def _bedrock_error(error: Exception) -> tuple[int, dict]:
    """A refused or failed AWS call as a status and a message a person can act on."""
    code = error.response.get("Error", {}).get("Code", "") if isinstance(error, ClientError) else ""
    if code in ("ThrottlingException", "TooManyRequestsException"):
        return _error(429, "AWS is limiting calls right now (the reranker allows 3 a minute). "
                           "Wait a minute and ask again.")
    if code == "AccessDeniedException":
        return _error(502, "AWS refused the call: the local AWS user needs bedrock:InvokeModel and bedrock:Rerank "
                           "on the project models.")
    if code:
        return _error(502, f"AWS refused the call ({code}). The server console has the details.")
    return _error(502, f"Could not call AWS ({type(error).__name__}). Check that ~\\.aws\\credentials holds a key "
                       "and that this machine is online.")


def _source(source: dict, files: dict[str, str | None]) -> dict:
    url = _pdf_url(files.get(source["document"]))
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


def ask(conn, payload, answer_fn=None) -> tuple[int, dict]:
    """Answer one question from the page. Never raises for a bad request, an AWS refusal or a lost database."""
    if not isinstance(payload, dict):
        return _error(400, "Send a JSON object with a question.")
    question, document = payload.get("question"), payload.get("document")
    if document == "":
        document = None
    if document is not None and not isinstance(document, str):
        return _error(400, "Pick a datasheet from the list.")
    if not isinstance(question, str) or not question.strip():
        return _error(400, "Type a question first.")
    question = question.strip()
    if len(question) > config.API_MAX_QUESTION_CHARS:
        return _error(400, f"Keep the question under {config.API_MAX_QUESTION_CHARS} characters "
                           f"(it has {len(question)}).")
    try:
        files = {row["name"]: row["file"] for row in db.list_documents(conn)}
    except psycopg.OperationalError:
        return _error(503, NO_DATABASE)
    if document is not None and document not in files:
        return _error(400, "Pick a datasheet from the list.")

    logged_before = costlog.row_count()
    try:
        result = (answer_fn or pipeline.answer_question)(conn, question, document=document)
    except (ClientError, BotoCoreError) as error:
        return _bedrock_error(error)
    except psycopg.OperationalError:
        return _error(503, NO_DATABASE)
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
