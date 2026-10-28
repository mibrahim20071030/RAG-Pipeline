"""The reviewer's JSON API (Cycle 2 step 9b), without HTTP: (method, path, query, payload) in, (status, body) out.

Deployed, every route here except /api/review/config sits behind API Gateway's JWT authorizer (a Cognito login), and
`handler.py` also checks that the token's groups include REVIEWER_GROUP before calling `route`. The local server
calls it without a login: it serves 127.0.0.1 only.

GET  /api/review/config                      {auth, domain, client_id}: what the page needs to log in (public)
GET  /api/review/feedback[?all=1]            thumbs-downs waiting for review (all=1: every rating)
POST /api/review/feedback/<answer_id>        {diagnosis, note}: what went wrong; correct / not_in_datasheets close it
GET  /api/review/page?document=&page=        the stored text of a datasheet page and its PDF link, to check a note
GET  /api/review/corrections                 every note
POST /api/review/corrections                 {answer_id, document, page, text, checked: true[, question]}: a draft
GET  /api/review/corrections/<id>            the note
POST /api/review/corrections/<id>/publish    embed, store, check our match finds it, then live on both sides
POST /api/review/corrections/<id>/retract    out of both pipelines at once
POST /api/review/corrections/<id>/discard    a draft, failed or retracted note thrown away; its rating goes back
                                             to the queue for a new note
GET  /api/review/report                      counts: ratings, reasons, diagnoses, notes
"""
import time
import traceback
from collections import Counter
from dataclasses import dataclass

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import api, config, corrections, db, records

PREFIX = "/api/review/"
CORRECTING = tuple(d for d in config.DIAGNOSES if d not in config.DIAGNOSES_CLOSING)


@dataclass
class Deps:
    """What the routes use; tests pass fakes. `open_conn()` gives a context manager yielding a connection."""
    store: object
    open_conn: object
    embed_fn: object = None


def config_body() -> dict:
    return {"auth": config.REVIEW_AUTH, "domain": config.COGNITO_DOMAIN or None,
            "client_id": config.COGNITO_CLIENT_ID or None}


def groups(claims: dict) -> list[str]:
    """The token's groups. HTTP API passes a list claim as text such as "[reviewers admins]"; a real list is read
    as it is."""
    value = (claims or {}).get("cognito:groups")
    if isinstance(value, list):
        return [str(group) for group in value]
    if isinstance(value, str):
        return value.strip().strip("[]").replace(",", " ").split()
    return []


def _error(status: int, message: str) -> tuple[int, dict]:
    return status, {"error": message}


def route(method: str, path: str, query: dict, payload, reviewer: str, deps: Deps) -> tuple[int, dict]:
    """One reviewer request. Never raises for a refused step, an AWS failure or a lost database."""
    parts = path.removeprefix(PREFIX).strip("/").split("/")
    try:
        if parts == ["config"] and method == "GET":
            return 200, config_body()
        if parts == ["feedback"] and method == "GET":
            return 200, queue(deps.store, everything=query.get("all") in ("1", "true"))
        if len(parts) == 2 and parts[0] == "feedback" and method == "POST":
            return diagnose(deps.store, parts[1], payload, reviewer)
        if parts == ["page"] and method == "GET":
            return page(deps, query.get("document"), query.get("page"))
        if parts == ["corrections"] and method == "GET":
            notes = [r for r in deps.store.scan(records.CORRECTION) if r.get("status") != "discarded"]
            return 200, {"corrections": sorted(notes, key=lambda r: -(r.get("created_at") or 0))}
        if parts == ["corrections"] and method == "POST":
            return create_correction(deps.store, payload, reviewer)
        if len(parts) == 2 and parts[0] == "corrections" and method == "GET":
            record = corrections.refresh(deps.store, parts[1]) if records.valid_id(parts[1]) else None
            return (200, record) if record else _error(404, "No such note.")
        if (len(parts) == 3 and parts[0] == "corrections" and parts[2] in ("publish", "retract", "discard")
                and method == "POST"):
            if not records.valid_id(parts[1]):
                return _error(404, "No such note.")
            if parts[2] == "publish":
                return 200, corrections.publish(deps.store, parts[1], reviewer, deps.open_conn, deps.embed_fn)
            if parts[2] == "discard":
                return 200, corrections.discard(deps.store, parts[1], reviewer, deps.open_conn)
            return 200, corrections.retract(deps.store, parts[1], reviewer, deps.open_conn)
        if parts == ["report"] and method == "GET":
            return 200, report(deps.store)
    except corrections.Refused as refused:
        return _error(refused.status, str(refused))
    except psycopg.OperationalError:
        return _error(503, api.no_database())
    except (ClientError, BotoCoreError) as error:
        traceback.print_exc()
        code = error.response.get("Error", {}).get("Code", "") if isinstance(error, ClientError) else ""
        return _error(502, f"An AWS call failed ({code or type(error).__name__}). Try again in a minute.")
    except RuntimeError as error:
        traceback.print_exc()
        return _error(503, str(error))
    return _error(404, "Not found.")


# ---------------------------------------------------------------- the queue

def _view(item: dict) -> dict:
    answer = item.get("answer") or {}
    return {key: item.get(key) for key in ("answer_id", "created_at", "rating", "reason", "comment", "status",
                                           "diagnosis", "review_note", "reviewed_by", "reviewed_at",
                                           "correction_id", "last_note")} | {
        "answer": {key: answer.get(key) for key in ("question", "document", "pipeline", "answer", "not_contained",
                                                    "sources", "passages", "notes", "model", "prompt_version")}}


def queue(store, everything: bool = False) -> dict:
    """The thumbs-downs still waiting (no diagnosis yet, or diagnosed and waiting for a note), oldest first; with
    `everything`, every rating, newest first."""
    items = store.scan(records.FEEDBACK)
    if everything:
        items.sort(key=lambda item: -(item.get("created_at") or 0))
    else:
        items = sorted((item for item in items if item.get("status") in ("pending", "diagnosed")),
                       key=lambda item: item.get("created_at") or 0)
    return {"items": [_view(item) for item in items], "diagnoses": list(config.DIAGNOSES),
            "closing": list(config.DIAGNOSES_CLOSING)}


def diagnose(store, answer_id: str, payload, reviewer: str, now=None) -> tuple[int, dict]:
    """Record what went wrong with a rated answer. A closing diagnosis (the answer was right, or the datasheets do
    not have it) ends the item; the others wait for a note."""
    if not records.valid_id(answer_id):
        return _error(404, "No such rating.")
    if not isinstance(payload, dict) or payload.get("diagnosis") not in config.DIAGNOSES:
        return _error(400, f"Pick a diagnosis: {', '.join(config.DIAGNOSES)}.")
    note = payload.get("note") or ""
    if not isinstance(note, str) or len(note) > config.REVIEW_NOTE_MAX_CHARS:
        return _error(400, f"Keep the review note under {config.REVIEW_NOTE_MAX_CHARS} characters.")
    diagnosis = payload["diagnosis"]
    status = "closed" if diagnosis in config.DIAGNOSES_CLOSING else "diagnosed"
    fields = {"status": status, "diagnosis": diagnosis, "review_note": note.strip() or None,
              "reviewed_by": reviewer, "reviewed_at": int(time.time() if now is None else now)}
    if not store.update(records.FEEDBACK + answer_id, expect={"status": ("pending", "diagnosed")}, **fields):
        return _error(409, "This rating is not waiting for a diagnosis (it is closed, corrected or unknown).")
    return 200, {"answer_id": answer_id, **fields}


def page(deps: Deps, document, number) -> tuple[int, dict]:
    """The stored text of one page of a curated datasheet and its PDF link, for checking a note against it."""
    if document not in config.DOCUMENTS:
        return _error(400, "Pick a datasheet from the list.")
    try:
        number = int(number)
    except (TypeError, ValueError):
        return _error(400, "Give the PDF page as a number.")
    with deps.open_conn() as conn:
        texts = db.page_chunks(conn, document, number, config.DEFAULT_STRATEGY)
        # The stored file name: locally the PDF the page numbers were taken from, not the vendor's newest revision.
        files = {row["name"]: row["file"] for row in db.list_documents(conn)}
    url = api._pdf_url(document, files.get(document))
    return 200, {"document": document, "part": config.DOCUMENTS[document]["part"], "page": number, "texts": texts,
                 "pdf_url": f"{url}#page={number}" if url else None}


def create_correction(store, payload, reviewer: str, now=None) -> tuple[int, dict]:
    """A draft note for a diagnosed thumbs-down. The reviewer names the datasheet page the note was checked
    against and confirms they checked it on that page."""
    if not isinstance(payload, dict):
        return _error(400, "Send the note as a JSON object.")
    answer_id, document, text = payload.get("answer_id"), payload.get("document"), payload.get("text")
    if not records.valid_id(answer_id):
        return _error(404, "No such rating.")
    if document not in config.DOCUMENTS:
        return _error(400, "A note must cite one of the curated datasheets.")
    number = payload.get("page")
    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 1000:
        return _error(400, "Give the PDF page the note was checked against.")
    if payload.get("checked") is not True:
        return _error(400, "Check the note against that page first, then tick the box.")
    if not isinstance(text, str) or not (config.CORRECTION_MIN_CHARS <= len(text.strip())
                                         <= config.CORRECTION_MAX_CHARS):
        return _error(400, f"Write the note in {config.CORRECTION_MIN_CHARS} to {config.CORRECTION_MAX_CHARS} "
                           "characters.")
    rating = store.get(records.FEEDBACK + answer_id)
    if rating is None:
        return _error(404, "No such rating.")
    if rating.get("status") != "diagnosed" or rating.get("diagnosis") not in CORRECTING:
        return _error(409, f"Diagnose the rating first as one of: {', '.join(CORRECTING)}.")
    question = payload.get("question") or (rating.get("answer") or {}).get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > config.API_MAX_QUESTION_CHARS:
        return _error(400, "The note needs the question it answers.")

    correction_id = records.new_id()
    now = time.time() if now is None else now
    record = {"correction_id": correction_id, "answer_id": answer_id, "diagnosis": rating["diagnosis"],
              "document": document, "page": number, "text": text.strip(), "question": question.strip(),
              "status": "draft", "ours": "off", "kb": "off", "created_by": reviewer, "created_at": int(now),
              "expires_at": None, "error": None}
    # The note first, then the rating: a failure in between leaves a draft nobody points at, never a rating that
    # points at no note. A rating that changed meanwhile discards the draft.
    store.put_new(corrections.key(correction_id), record)
    if not store.update(records.FEEDBACK + answer_id, expect={"status": "diagnosed"}, status="corrected",
                        correction_id=correction_id):
        store.update(corrections.key(correction_id), status="discarded")
        return _error(409, "The rating changed meanwhile. Reload the queue.")
    return 200, record


# ---------------------------------------------------------------- the report

def report(store) -> dict:
    """The loop's numbers: answers saved, ratings and the thumbs-down rate per pipeline, reasons, diagnoses, notes
    by status, and how answers that used a note were rated."""
    answers = store.scan(records.ANSWER)
    ratings = store.scan(records.FEEDBACK)
    notes = store.scan(records.CORRECTION)
    by_pipeline = {}
    for name in config.PIPELINES:
        saved = [a for a in answers if a.get("pipeline") == name]
        rated = [r for r in ratings if (r.get("answer") or {}).get("pipeline") == name]
        down = sum(1 for r in rated if r.get("rating") == "down")
        by_pipeline[name] = {"answers": len(saved), "rated": len(rated), "down": down,
                             "up": len(rated) - down,
                             "down_rate": round(down / len(rated), 3) if rated else None}
    with_notes = [r for r in ratings if (r.get("answer") or {}).get("notes")]
    return {
        "answers": len(answers), "answers_with_notes": sum(1 for a in answers if a.get("notes")),
        "pipelines": by_pipeline,
        "reasons": dict(Counter(r.get("reason") or "none" for r in ratings if r.get("rating") == "down")),
        "diagnoses": dict(Counter(r["diagnosis"] for r in ratings if r.get("diagnosis"))),
        "feedback_status": dict(Counter(r.get("status") for r in ratings)),
        "notes": dict(Counter(n.get("status") for n in notes if n.get("status") != "discarded")),
        "rated_with_notes": {"rated": len(with_notes),
                             "down": sum(1 for r in with_notes if r.get("rating") == "down")},
        "kept_days": {"answers": config.ANSWER_KEEP_DAYS, "ratings": config.FEEDBACK_KEEP_DAYS},
    }
