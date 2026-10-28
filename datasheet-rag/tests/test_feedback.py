"""Cycle 2 step 9b tests for the visitor's side of the feedback loop: an answer gets an id and is saved, and
POST /api/feedback rates it once. No paid call, no database, no DynamoDB (stores and counters are fakes)."""
import contextlib

import pytest
from botocore.exceptions import ClientError

from rag import api, config, db, records

ID = "ab" * 16


def result(**fields):
    base = {"question": "q", "answer": "35 V [1].", "not_contained": False, "invalid": [], "cut_off": False,
            "sources": [{"label": 1, "id": 7, "document": "lm35", "page": 4, "page_label": "3"}],
            "passages": [{"id": 7, "document": "lm35", "page": 4, "page_label": "3", "kind": "text",
                          "text": "Supply voltage: MAX: 35 V"}],
            "timings_ms": {"search": 1.0, "rerank": 1.0, "generate": 1.0}}
    return {**base, **fields}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(db, "list_documents", lambda conn: [{"name": "lm35", "file": "lm35.pdf"}])
    monkeypatch.setattr(db, "delete_expired_uploads", lambda conn: 0)
    monkeypatch.setattr(config, "API_DAILY_ASK_LIMIT", 0)
    monkeypatch.setattr(config, "FEEDBACK_DAILY_LIMIT", 0)


class Counter:
    def __init__(self, limit_reached=False):
        self.taken, self.released, self.limit_reached = [], [], limit_reached

    def take(self, kind, limit, day=None):
        self.taken.append(kind)
        return None if self.limit_reached else 1

    def release(self, kind, day=None):
        self.released.append(kind)


class BrokenStore(records.MemoryStore):
    def put_new(self, key, record):
        raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no"}}, "PutItem")


def ask(payload, store, answer=None):
    return api.ask(payload, lambda: contextlib.nullcontext(None), lambda conn, question, document=None:
                   answer or result(), answer_store=store)


# ---------------------------------------------------------------- answers are saved

def test_an_answer_gets_an_id_and_is_saved():
    store = records.MemoryStore()
    status, body = ask({"question": "Max supply?", "document": "lm35"}, store)
    assert status == 200 and records.valid_id(body["answer_id"]) and "_result" not in body
    saved = store.get(records.ANSWER + body["answer_id"])
    assert saved["question"] == "Max supply?" and saved["document"] == "lm35" and saved["pipeline"] == "ours"
    assert saved["answer"] == "35 V [1]." and saved["passages"][0]["text"] == "Supply voltage: MAX: 35 V"
    assert saved["sources"] == body["sources"] and saved["setup"]["pipeline"] == "ours"


def test_a_kb_answer_is_saved_as_kb():
    store = records.MemoryStore()
    status, body = api.ask({"question": "q", "pipeline": "kb"}, lambda: pytest.fail("no database"),
                           kb_answer_fn=lambda question, document=None: result(timings_ms={"retrieve": 1.0}),
                           answer_store=store)
    assert status == 200 and store.get(records.ANSWER + body["answer_id"])["pipeline"] == "kb"


def test_without_a_store_there_is_no_id():
    status, body = ask({"question": "q"}, None)
    assert status == 200 and body["answer_id"] is None and "_result" not in body


def test_a_failed_save_still_answers():
    status, body = ask({"question": "q"}, BrokenStore())
    assert status == 200 and body["answer_id"] is None and body["answer"] == "35 V [1]."


def test_an_upload_answer_is_not_saved(monkeypatch):
    store = records.MemoryStore()
    monkeypatch.setattr(api, "check_upload", lambda payload, name, store=None, kb_status=None:
                        ({"upload_id": "0" * 16, "filename": "mine.pdf"}, None))
    status, body = ask({"question": "q", "upload_id": "0" * 16}, store)
    assert status == 200 and body["answer_id"] is None and store.scan(records.ANSWER) == []


def test_a_refused_question_is_not_saved():
    store = records.MemoryStore()
    status, body = ask({"question": ""}, store)
    assert status == 400 and "answer_id" not in body and store.scan(records.ANSWER) == []


def test_a_note_source_is_marked():
    note = {"label": 1, "id": "note-x", "document": "lm35", "page": 4, "page_label": None, "note": True}
    status, body = ask({"question": "q"}, records.MemoryStore(), result(sources=[note]))
    assert status == 200
    assert body["sources"][0]["note"] is True and body["sources"][0]["title"].startswith("Verified note: ")


# ---------------------------------------------------------------- rating

@pytest.fixture
def answered():
    store = records.MemoryStore()
    store.put_new(records.ANSWER + ID, {"answer_id": ID, "question": "q", "pipeline": "ours", "expires_at": None})
    return store


@pytest.mark.parametrize(("payload", "status"), [
    ("text", 400),
    ({"answer_id": "nope", "rating": "down"}, 404),
    ({"answer_id": ID, "rating": "meh"}, 400),
    ({"answer_id": ID, "rating": "down", "reason": "boring"}, 400),
    ({"answer_id": ID, "rating": "up", "reason": "wrong"}, 400),
    ({"answer_id": ID, "rating": "down", "comment": 5}, 400),
    ({"answer_id": ID, "rating": "down", "comment": "x" * (config.FEEDBACK_MAX_COMMENT_CHARS + 1)}, 400),
])
def test_bad_ratings_are_refused(answered, payload, status):
    assert api.feedback(payload, answered)[0] == status
    assert answered.scan(records.FEEDBACK) == []


def test_a_thumbs_down_waits_for_review_with_a_copy_of_the_answer(answered):
    status, body = api.feedback({"answer_id": ID, "rating": "down", "reason": "wrong",
                                 "comment": "  It is 30 V\x07 on page 4.\n"}, answered)
    assert (status, body) == (200, {"ok": True})
    item = answered.get(records.FEEDBACK + ID)
    assert item["status"] == "pending" and item["reason"] == "wrong" and item["comment"] == "It is 30 V on page 4."
    kept = item["expires_at"] - item["created_at"]
    assert item["answer"]["question"] == "q" and kept == config.FEEDBACK_KEEP_DAYS * 86400


def test_a_thumbs_up_is_noted_not_queued(answered):
    assert api.feedback({"answer_id": ID, "rating": "up"}, answered)[0] == 200
    assert answered.get(records.FEEDBACK + ID)["status"] == "noted"


def test_one_rating_per_answer(answered):
    assert api.feedback({"answer_id": ID, "rating": "up"}, answered)[0] == 200
    assert api.feedback({"answer_id": ID, "rating": "down"}, answered)[0] == 409


def test_an_unknown_answer_gives_its_place_back(monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DAILY_LIMIT", 5)
    counter = Counter()
    assert api.feedback({"answer_id": ID, "rating": "up"}, records.MemoryStore(), counter)[0] == 404
    assert counter.taken == ["feedback"] and counter.released == ["feedback"]


def test_the_daily_cap_stops_ratings(answered, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DAILY_LIMIT", 5)
    status, body = api.feedback({"answer_id": ID, "rating": "up"}, answered, Counter(limit_reached=True))
    assert status == 429 and body["limit"] == "daily" and answered.scan(records.FEEDBACK) == []


def test_a_store_that_fails_is_busy(monkeypatch):
    class Failing(records.MemoryStore):
        def get(self, key):
            raise RuntimeError("USAGE_TABLE is not set")

    assert api.feedback({"answer_id": ID, "rating": "up"}, Failing())[0] == 503
