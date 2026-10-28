"""Reviewers' notes (Cycle 2 step 9b): matching them to a question, publishing them to both pipelines, retracting.

A note is written by a reviewer after reading a visitor's thumbs-down: one fact, checked against one page of one
curated datasheet. Both pipelines decide whether a note fits a question the same way: the cosine similarity of the
question's Titan vector to the note's, at least CORRECTION_MIN_SIMILARITY. Notes are searched on their own, never
mixed into the datasheet search, and each side keeps the notes where it can read them:

    ours   the `corrections` table in Postgres, matched there by pgvector, with the question's vector the search
           has already paid for
    KB     the published list (item `notes#list` in the records table, DynamoDB deployed), which holds each
           published note with its vector, so a KB answer still never wakes Aurora; the question is embedded once

Not the KB's own Retrieve: its score only ranks the notes for one question (a reranker score, or with the reranker
off a score set by the rank), so no threshold separates a note that fits from one that does not
(runs/feedback_eval.md). The KB still finds the datasheet passages.

A matched note goes before the passages as "Verified note: <part> (<document>), page <page>", so a citation of it is
a citation of the datasheet page it was checked against. What the model reads is the reviewer's text only: the
question a note answers (which may be the visitor's own words) helps matching and is never in a passage.

A note's record (rag.records, correction#<id>) moves:
    draft -> publishing -> published      (or failed; publish again from failed)
    published -> retracted                (at once on both sides)
It is "published" only after our match found it for its own question; the KB side then finds it too, because it
uses the same vectors and threshold. Only a published note reaches an answer: our rows carry the status, and the
KB side reads only the published list.
"""
import base64
import math
import struct
import time
import traceback

import psycopg

from rag import config, db, embed, records, search

PUBLISHABLE = ("draft", "failed", "retracted")
RETRACTABLE = ("published", "ingesting", "failed")  # "ingesting": only a note left from the old KB sync
# {"notes": [...], "version": n}: changed by every publish and retract, never rebuilt from a Scan. (Until 2026-10-28
# the list was "notes#published", ids only; that item is no longer read.)
PUBLISHED_KEY = "notes#list"


def key(correction_id: str) -> str:
    return records.CORRECTION + correction_id


def match_text(record: dict) -> str:
    """What is embedded: the question the note answers, then the note. The question line helps the note match the
    same question in other words; it is never shown to the model."""
    return f"Questions like: {record['question']}\n{record['text']}"


def note_hit(correction_id: str, document: str, page: int, text: str, score) -> dict:
    """A note in the hit shape of the search, so generate numbers and cites it like any passage."""
    return {"id": f"note-{correction_id}", "document": document, "page": page, "page_label": None, "section": None,
            "kind": "note", "text": text, "score": score, "correction_id": correction_id}


def _curated(document: str | None) -> bool:
    return document is None or document in config.DOCUMENTS


# ---------------------------------------------------------------- the published list, for the KB side

def pack(vector) -> str:
    """A vector as base64 of little-endian float32: what pgvector stores, so both sides compute the same numbers.
    About 5.5 KB for 1024 dimensions."""
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


def unpack(text: str) -> list[float]:
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def published_notes(store) -> list[dict]:
    return (store.get(PUBLISHED_KEY) or {}).get("notes") or []


def published_ids(store) -> list[str]:
    return [note["correction_id"] for note in published_notes(store)]


def list_entry(record: dict, vector) -> dict:
    return {"correction_id": record["correction_id"], "document": record["document"], "page": record["page"],
            "text": record["text"], "vector": pack(vector)}


def _seed(store, conn) -> list[dict]:
    """The list's first content: every note already published (from before the list held vectors), with our stored
    vectors. A Scan, once: those notes were published long before, so its eventual consistency does not matter."""
    published = sorted((r for r in store.scan(records.CORRECTION) if r.get("status") == "published"),
                       key=lambda r: r["correction_id"])
    vectors = db.correction_vectors(conn, [r["correction_id"] for r in published]) if published else {}
    return [list_entry(r, vectors[r["correction_id"]]) for r in published if r["correction_id"] in vectors]


def _edit_list(store, change, conn=None) -> bool:
    """Apply `change(notes) -> notes` to the published list. Each write needs the version it read, so a publish and
    a retract at the same time never overwrite each other: the loser reads again and reapplies its change. A list
    not made yet is seeded first (that needs `conn`; without one there is nothing on the KB side to change, and
    False is returned)."""
    for _ in range(10):
        item = store.get(PUBLISHED_KEY)
        if item is None:
            if conn is None:
                return False
            notes = change(_seed(store, conn))
            if store.put_new(PUBLISHED_KEY, {"notes": notes, "version": 1, "expires_at": None}):
                return True
            continue
        notes = change(item.get("notes") or [])
        if store.update(PUBLISHED_KEY, expect={"version": item["version"]}, notes=notes,
                        version=item["version"] + 1):
            return True
    raise RuntimeError("The published notes changed too often to update. Try again.")


def _add(store, conn, entry: dict) -> None:
    _edit_list(store, lambda notes: sorted([n for n in notes if n["correction_id"] != entry["correction_id"]]
                                           + [entry], key=lambda n: n["correction_id"]), conn)


def _drop(store, correction_id: str, conn=None) -> None:
    _edit_list(store, lambda notes: [n for n in notes if n["correction_id"] != correction_id], conn)


# ---------------------------------------------------------------- matching, at answer time

def match(conn, question: str, document: str | None, embed_fn=None) -> list[dict]:
    """Our side: the published notes at least CORRECTION_MIN_SIMILARITY close to the question, best first, at most
    CORRECTION_MAX. None for a question about an upload: notes cite curated datasheets only."""
    if not _curated(document):
        return []
    vector = search.question_vector(conn, question, embed_fn)  # cached by the search a moment ago
    try:
        rows = db.match_corrections(conn, vector, document, config.CORRECTION_MIN_SIMILARITY, config.CORRECTION_MAX)
    except psycopg.errors.UndefinedTable:
        # A database made before step 9b (create_schema adds the table); answers go on without notes.
        traceback.print_exc()
        return []
    return [note_hit(row["correction_id"], row["document"], row["page"], row["text"], row["similarity"])
            for row in rows]


def kb_match(question: str, document: str | None, store=None, embed_fn=None) -> list[dict]:
    """The KB side: the same rule as `match`, on the published list. One read of the list, and one Titan embed of
    the question only when a published note could fit (one of the named datasheet's, or any with none named)."""
    if not _curated(document):
        return []
    notes = [note for note in published_notes(store or records.default_store())
             if document is None or note["document"] == document]
    if not notes:
        return []
    question_vector = unpack(pack((embed_fn or embed.embed_text)(question)))  # float32, as pgvector reads it
    scored = sorted(((cosine(question_vector, unpack(note["vector"])), note["correction_id"], note)
                     for note in notes), key=lambda item: (-item[0], item[1]))
    return [note_hit(note["correction_id"], note["document"], note["page"], note["text"], similarity)
            for similarity, _, note in scored
            if similarity >= config.CORRECTION_MIN_SIMILARITY][:config.CORRECTION_MAX]


# ---------------------------------------------------------------- publishing

class Refused(Exception):
    """A step that cannot go on, with the status and message for the reviewer."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _current(store, record: dict) -> dict:
    return store.get(key(record["correction_id"])) or record


def _fail(store, record: dict, message: str, conn=None, expect=None, **fields) -> dict:
    """Mark the note failed, only while it is still in the state `expect` (a retract that came in meanwhile wins),
    and only then our row too."""
    if not store.update(key(record["correction_id"]), expect=expect, status="failed", error=message, **fields):
        return _current(store, record)
    if conn is not None:
        db.set_correction_status(conn, record["correction_id"], "failed")
    return {**record, **fields, "status": "failed", "error": message}


def _ours_finds(conn, record: dict, embed_fn=None) -> float | None:
    """The note's similarity to its own question, or None when it is under the threshold. This note only: other
    notes (a pending one left by a publish that stopped partway, say) cannot push it out."""
    vector = search.question_vector(conn, record["question"], embed_fn)
    similarity = db.correction_similarities(conn, vector, statuses=("pending",)).get(record["correction_id"])
    return similarity if similarity is not None and similarity >= config.CORRECTION_MIN_SIMILARITY else None


def publish(store, correction_id: str, reviewer: str, open_conn, embed_fn=None, now=None) -> dict:
    """Publish a draft (or a failed or retracted note): embed it, store it on our side as 'pending', check our match
    finds it for its question, then put it live on both sides at once.

    Raises Refused for a note that is not in a state to publish, or when CORRECTION_MAX_PUBLISHED notes are live."""
    record = store.get(key(correction_id))
    if record is None:
        raise Refused(404, "No such note.")
    if record.get("status") in PUBLISHABLE and len(published_ids(store)) >= config.CORRECTION_MAX_PUBLISHED:
        raise Refused(409, f"{config.CORRECTION_MAX_PUBLISHED} notes are published, the most the list holds. "
                           "Retract one first.")
    if not store.update(key(correction_id), expect={"status": PUBLISHABLE}, status="publishing", error=None,
                        published_by=reviewer):
        raise Refused(409, f"The note is {record.get('status')}; only a draft, failed or retracted note can be "
                           "published.")
    record = {**record, "status": "publishing", "error": None}
    try:
        return _publish(store, record, open_conn, embed_fn, now)
    except BaseException:
        # A database that did not answer, or an AWS refusal of the embed: the note can be published again.
        store.update(key(correction_id), expect={"status": "publishing"}, status="failed",
                     error="Publishing stopped partway (the database or AWS did not answer). Publish again.")
        raise


def _publish(store, record: dict, open_conn, embed_fn, now) -> dict:
    correction_id = record["correction_id"]
    publishing = {"status": "publishing"}
    with open_conn() as conn:
        vector = search.question_vector(conn, match_text(record), embed_fn)  # paid once, kept in embedding_cache
        # The row holds the reviewer's text (what the model reads); the vector is of the question and the text.
        db.store_correction(conn, correction_id, record["document"], record["page"], record["text"], vector,
                            "pending")
        similarity = _ours_finds(conn, record, embed_fn)
        if similarity is None:
            return _fail(store, record, f"Our search does not find the note for its own question (threshold "
                                        f"{config.CORRECTION_MIN_SIMILARITY}). Reword the note.", conn, publishing)
        published_at = int(time.time() if now is None else now)
        fields = {"ours": "ready", "ours_similarity": round(similarity, 4), "kb": "ready" if config.KB_ID else "off",
                  "published_at": published_at, "error": None}
        # The record first: from here a retract may come in, and it undoes the two steps below.
        if not store.update(key(correction_id), expect=publishing, status="published", **fields):
            return _current(store, record)
        try:
            db.set_correction_status(conn, correction_id, "published")
            _add(store, conn, list_entry(record, db.correction_vectors(conn, [correction_id])[correction_id]))
        except BaseException:
            _undo(store, conn, correction_id)
            store.update(key(correction_id), expect={"status": "published"}, status="failed",
                         error="Publishing stopped partway (the database or AWS did not answer). Publish again.")
            raise
        if (store.get(key(correction_id)) or {}).get("status") != "published":
            _undo(store, conn, correction_id)  # retracted while the row and the list were being set
            return _current(store, record)
    return {**record, **fields, "status": "published"}


def _undo(store, conn, correction_id: str) -> None:
    """Out of both sides again, as far as AWS and the database still answer."""
    for step in (lambda: _drop(store, correction_id, conn),
                 lambda: db.set_correction_status(conn, correction_id, "retracted")):
        try:
            step()
        except Exception:  # noqa: BLE001 (best effort: the caller reports the note as failed or retracted)
            traceback.print_exc()


def refresh(store, correction_id: str) -> dict | None:
    """The note's record; None for an unknown note. Publishing and retracting finish in one call."""
    return store.get(key(correction_id))


DISCARDABLE = ("draft", "failed", "retracted")


def discard(store, correction_id: str, reviewer: str, open_conn, now=None) -> dict:
    """Throw a note away and send its rating back to the queue (diagnosed, waiting for a note, with the note's
    datasheet, page, text and question kept as `last_note` so the form opens filled in), so the reviewer can write it
    again: for a draft on the wrong page, or a note that failed and needs rewording. A failed note is retracted
    first, so its row is out of our side too. A published note must be retracted first."""
    record = store.get(key(correction_id))
    if record is None:
        raise Refused(404, "No such note.")
    if record.get("status") not in DISCARDABLE:
        raise Refused(409, f"The note is {record.get('status')}; retract it first, then discard it.")
    if record["status"] == "failed" or unfinished_retract(record):
        record = retract(store, correction_id, reviewer, open_conn, now)  # our row out first
    if not store.update(key(correction_id), expect={"status": ("draft", "retracted")}, status="discarded",
                        discarded_by=reviewer, discarded_at=int(time.time() if now is None else now)):
        raise Refused(409, "The note changed meanwhile. Reload the notes.")
    # Back to the queue only when the rating still points at this note.
    back = store.update(records.FEEDBACK + record["answer_id"],
                        expect={"status": "corrected", "correction_id": correction_id},
                        status="diagnosed", correction_id=None,
                        last_note={name: record.get(name) for name in ("document", "page", "text", "question")})
    return {**record, "status": "discarded", "rating_back": back}


def unfinished_retract(record: dict | None) -> bool:
    """A retracted note whose row on our side may still be live: the database did not answer the retract (`ours`
    "retracting"), or it was retracted before 2026-10-28, when `ours` was left as it was. Retract finishes it."""
    return bool(record) and record.get("status") == "retracted" and record.get("ours") not in ("off", None)


def retract(store, correction_id: str, reviewer: str, open_conn, now=None) -> dict:
    """Take a note out of both pipelines: its record and the published list at once, then our row. When the
    database does not answer (it may be waking up), the note is out of the KB side, its record says so (`ours`
    "retracting"), and Refused(503) asks the reviewer to retract again, which only finishes the last step."""
    record = store.get(key(correction_id))
    if record is None:
        raise Refused(404, "No such note.")
    retracted_at = int(time.time() if now is None else now)
    if store.update(key(correction_id), expect={"status": RETRACTABLE}, status="retracted", ours="retracting",
                    kb="off", retracted_by=reviewer, retracted_at=retracted_at):
        record = {**record, "status": "retracted", "ours": "retracting", "kb": "off", "retracted_at": retracted_at}
    elif unfinished_retract(record):
        store.update(key(correction_id), expect={"status": "retracted"}, ours="retracting", kb="off")
        record = {**record, "ours": "retracting", "kb": "off"}
    else:
        raise Refused(409, f"The note is {record.get('status')}; only a published, ingesting or failed note can be "
                           "retracted.")
    # Out of the KB side first, so a database that does not answer cannot keep it in. A list not made yet holds
    # nothing, so there is nothing to take out of it.
    _drop(store, correction_id)
    try:
        with open_conn() as conn:
            db.set_correction_status(conn, correction_id, "retracted")
    except (psycopg.OperationalError, RuntimeError) as error:
        traceback.print_exc()
        raise Refused(503, "Taken out of the AWS managed pipeline, but the database did not answer (it may be "
                           "waking up), so our pipeline still has it. Click Finish retract in a minute.") from error
    store.update(key(correction_id), expect={"status": "retracted"}, ours="off")
    return {**record, "ours": "off"}
