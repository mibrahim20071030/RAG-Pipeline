"""Cycle 2 step 9b tests for reviewers' notes: matching on both pipelines, the prompt, publishing and retracting, and
the reviewer's routes. No paid call: Titan and the database functions are fakes; the tests marked with `conn` use the
local Postgres and skip without it."""
import contextlib

import pytest

from rag import config, corrections, db, generate, kb, pipeline, records, review, search

ANSWER = "ab" * 16
DIMS = config.EMBED_DIMENSIONS
NEAR = [1.0] + [0.0] * (DIMS - 1)          # the fake note vector
FAR = [0.0, 1.0] + [0.0] * (DIMS - 2)      # a question about something else: similarity 0


# ---------------------------------------------------------------- fakes

class FakeDb:
    """The corrections table in memory: what db.store_correction / set_correction_status / match_corrections /
    correction_vectors do. Every note's vector is NEAR."""

    def __init__(self, similarity=0.9):
        self.rows, self.similarity = {}, similarity

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "store_correction", self.store)
        monkeypatch.setattr(db, "set_correction_status", self.status)
        monkeypatch.setattr(db, "match_corrections", self.match)
        monkeypatch.setattr(db, "correction_vectors", self.vectors)
        monkeypatch.setattr(db, "correction_similarities", self.similarities)
        monkeypatch.setattr(search, "question_vector", lambda conn, text, embed_fn=None: "[0.1]")

    def store(self, conn, correction_id, document, page, text, vector, status):
        self.rows[correction_id] = {"document": document, "page": page, "text": text, "status": status}

    def status(self, conn, correction_id, status):
        self.rows[correction_id]["status"] = status
        return True

    def match(self, conn, vector, document, min_similarity, n, statuses=("published",)):
        return [{"correction_id": cid, "document": row["document"], "page": row["page"], "text": row["text"],
                 "similarity": self.similarity}
                for cid, row in self.rows.items()
                if row["status"] in statuses and (document is None or row["document"] == document)
                and self.similarity >= min_similarity][:n]

    def vectors(self, conn, ids):
        return {cid: NEAR for cid in ids if cid in self.rows}

    def similarities(self, conn, vector, statuses=("published",)):
        return {cid: self.similarity for cid, row in self.rows.items() if row["status"] in statuses}


class Embedder:
    """A fake Titan for the KB side's question embed, counting calls."""

    def __init__(self, vector=NEAR):
        self.vector, self.calls = vector, 0

    def __call__(self, text):
        self.calls += 1
        return self.vector


def opener():
    return contextlib.nullcontext("conn")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(config, "KB_ID", "KB12345678")
    fake_db = FakeDb()
    fake_db.install(monkeypatch)
    return {"db": fake_db, "store": records.MemoryStore()}


def draft(store, **fields):
    record = {"correction_id": "cd" * 16, "answer_id": ANSWER, "diagnosis": "misread", "document": "lm35",
              "page": 4, "text": "The LM35 is accurate to ±0.5 °C at 25 °C; ±0.2 °C is the LM35A.",
              "question": "How accurate is the LM35 at 25 °C?", "status": "draft", "ours": "off", "kb": "off",
              "created_at": 1, "expires_at": None, "error": None, **fields}
    store.put_new(corrections.key(record["correction_id"]), record)
    return record["correction_id"]


# ---------------------------------------------------------------- publish and retract

def test_publish_goes_live_on_both_sides_at_once(setup):
    s, cid = setup, draft(setup["store"])
    record = corrections.publish(s["store"], cid, "rev", opener)
    assert record["status"] == "published" and record["ours"] == "ready" and record["kb"] == "ready"
    assert s["db"].rows[cid]["status"] == "published"
    assert s["db"].rows[cid]["text"].startswith("The LM35 is accurate")  # the model reads the reviewer's text only
    assert s["store"].get(corrections.key(cid))["status"] == "published"
    [note] = corrections.published_notes(s["store"])
    assert note["correction_id"] == cid and note["document"] == "lm35" and note["page"] == 4
    assert note["text"] == s["db"].rows[cid]["text"] and corrections.unpack(note["vector"]) == NEAR
    assert corrections.published_ids(s["store"]) == [cid]
    assert corrections.refresh(s["store"], cid)["status"] == "published"


def test_our_search_not_finding_the_note_fails_it(setup):
    s, cid = setup, draft(setup["store"])
    s["db"].similarity = config.CORRECTION_MIN_SIMILARITY - 0.1
    record = corrections.publish(s["store"], cid, "rev", opener)
    assert record["status"] == "failed" and "does not find" in record["error"]
    assert s["db"].rows[cid]["status"] == "failed" and corrections.published_notes(s["store"]) == []


def test_without_the_kb_a_note_is_live_on_ours_only(setup, monkeypatch):
    monkeypatch.setattr(config, "KB_ID", "")
    s, cid = setup, draft(setup["store"])
    record = corrections.publish(s["store"], cid, "rev", opener)
    assert record["status"] == "published" and record["kb"] == "off"


def test_only_a_draft_failed_or_retracted_note_is_published(setup):
    s = setup
    cid = draft(s["store"], status="published")
    with pytest.raises(corrections.Refused) as refused:
        corrections.publish(s["store"], cid, "rev", opener)
    assert refused.value.status == 409
    with pytest.raises(corrections.Refused):
        corrections.publish(s["store"], "ef" * 16, "rev", opener)


def test_the_published_list_has_room_for_a_fixed_number(setup, monkeypatch):
    monkeypatch.setattr(config, "CORRECTION_MAX_PUBLISHED", 1)
    s = setup
    corrections.publish(s["store"], draft(s["store"]), "rev", opener)
    second = draft(s["store"], correction_id="ef" * 16)
    with pytest.raises(corrections.Refused) as refused:
        corrections.publish(s["store"], second, "rev", opener)
    assert refused.value.status == 409 and "Retract one first" in str(refused.value)
    assert s["store"].get(corrections.key(second))["status"] == "draft"


def test_a_full_list_of_notes_fits_one_dynamodb_item():
    """CORRECTION_MAX_PUBLISHED notes at the longest text, as DynamoStore writes the list, stay under 400 KB."""
    note = {"correction_id": "cd" * 16, "document": "esp32_datasheet_en", "page": 100,
            "text": "x" * config.CORRECTION_MAX_CHARS, "vector": corrections.pack([0.123456789] * DIMS)}
    attr = records._to_attr([note] * config.CORRECTION_MAX_PUBLISHED)
    assert len(attr["M"]["json"]["S"].encode("utf-8")) < 390_000


def test_a_database_that_does_not_answer_leaves_the_note_publishable(setup):
    s, cid = setup, draft(setup["store"])

    def down():
        raise RuntimeError("no database")

    with pytest.raises(RuntimeError):
        corrections.publish(s["store"], cid, "rev", down)
    assert s["store"].get(corrections.key(cid))["status"] == "failed"


def test_retract_takes_the_note_out_of_both_at_once(setup):
    s, cid = setup, draft(setup["store"])
    corrections.publish(s["store"], cid, "rev", opener)
    assert corrections.kb_match("q", None, store=s["store"], embed_fn=Embedder()) != []
    record = corrections.retract(s["store"], cid, "rev", opener)
    assert record["status"] == "retracted" and s["db"].rows[cid]["status"] == "retracted"
    assert corrections.published_notes(s["store"]) == [] and corrections.published_ids(s["store"]) == []
    assert corrections.kb_match("q", None, store=s["store"], embed_fn=Embedder()) == []
    assert corrections.publish(s["store"], cid, "rev", opener)["status"] == "published"  # and back again


def test_a_retract_with_the_database_down_still_leaves_the_kb_side(setup):
    s, cid = setup, draft(setup["store"])
    corrections.publish(s["store"], cid, "rev", opener)

    def down():
        raise RuntimeError("no database")

    with pytest.raises(corrections.Refused) as refused:
        corrections.retract(s["store"], cid, "rev", down)
    assert refused.value.status == 503 and "Finish retract" in str(refused.value)
    record = s["store"].get(corrections.key(cid))
    assert record["status"] == "retracted" and record["ours"] == "retracting" and record["kb"] == "off"
    assert corrections.published_notes(s["store"]) == [] and corrections.unfinished_retract(record)
    assert s["db"].rows[cid]["status"] == "published"  # our side still has it until the retract is finished
    finished = corrections.retract(s["store"], cid, "rev", opener)  # Finish retract
    assert finished["ours"] == "off" and s["db"].rows[cid]["status"] == "retracted"
    assert not corrections.unfinished_retract(s["store"].get(corrections.key(cid)))
    with pytest.raises(corrections.Refused) as again:
        corrections.retract(s["store"], cid, "rev", opener)
    assert again.value.status == 409


def test_a_note_retracted_before_the_fix_can_be_finished(setup):
    """Retracted by the old code with its database step lost: `ours` still "ready", our row still published."""
    s, cid = setup, draft(setup["store"], status="retracted", ours="ready")
    s["db"].rows[cid] = {"document": "lm35", "page": 4, "text": "t", "status": "published"}
    assert corrections.unfinished_retract(s["store"].get(corrections.key(cid)))
    assert corrections.retract(s["store"], cid, "rev", opener)["ours"] == "off"
    assert s["db"].rows[cid]["status"] == "retracted"


def test_discarding_an_unfinished_retract_finishes_it_first(setup):
    s, cid = setup, draft(setup["store"], status="retracted", ours="retracting")
    s["db"].rows[cid] = {"document": "lm35", "page": 4, "text": "t", "status": "published"}
    assert corrections.discard(s["store"], cid, "rev", opener)["status"] == "discarded"
    assert s["db"].rows[cid]["status"] == "retracted"


def test_a_draft_cannot_be_retracted(setup):
    with pytest.raises(corrections.Refused):
        corrections.retract(setup["store"], draft(setup["store"]), "rev", opener)


# ---------------------------------------------------------------- matching at answer time

def test_kb_match_uses_the_same_threshold_as_ours(setup):
    s, cid = setup, draft(setup["store"])
    corrections.publish(s["store"], cid, "rev", opener)
    notes = corrections.kb_match("q", "lm35", store=s["store"], embed_fn=Embedder())
    assert [note["correction_id"] for note in notes] == [cid]
    assert notes[0]["text"] == s["store"].get(corrections.key(cid))["text"]  # the reviewer's text
    assert notes[0]["kind"] == "note" and notes[0]["page"] == 4 and notes[0]["document"] == "lm35"
    assert notes[0]["score"] == pytest.approx(1.0)
    assert corrections.kb_match("q", None, store=s["store"], embed_fn=Embedder(FAR)) == []  # similarity 0


def test_kb_match_embeds_only_when_a_note_could_fit(setup):
    s, cid = setup, draft(setup["store"])
    embedder = Embedder()
    assert corrections.kb_match("q", None, store=s["store"], embed_fn=embedder) == []  # nothing published
    corrections.publish(s["store"], cid, "rev", opener)
    assert corrections.kb_match("q", "esp32_datasheet_en", store=s["store"], embed_fn=embedder) == []
    assert corrections.kb_match("q", "upload-0123456789abcdef", store=s["store"], embed_fn=embedder) == []
    assert embedder.calls == 0
    corrections.kb_match("q", None, store=s["store"], embed_fn=embedder)
    assert embedder.calls == 1


def test_kb_match_keeps_the_best_notes_first(setup):
    s = setup
    for number, cid in enumerate(("aa" * 16, "bb" * 16, "cc" * 16)):
        draft(s["store"], correction_id=cid, page=number + 1)
        corrections.publish(s["store"], cid, "rev", opener)
    tilted = [0.9, (1 - 0.81) ** 0.5] + [0.0] * (DIMS - 2)  # similarity 0.9 to the question
    item = s["store"].get(corrections.PUBLISHED_KEY)
    item["notes"][0]["vector"] = corrections.pack(tilted)
    s["store"].update(corrections.PUBLISHED_KEY, notes=item["notes"])
    notes = corrections.kb_match("q", None, store=s["store"], embed_fn=Embedder())
    assert [n["correction_id"] for n in notes] == ["bb" * 16, "cc" * 16][:config.CORRECTION_MAX]


def test_no_notes_for_an_upload_on_ours(setup):
    assert corrections.match("conn", "q", "upload-0123456789abcdef") == []


def test_pack_keeps_float32():
    vector = [0.1, -0.25, 3.0]
    assert corrections.unpack(corrections.pack(vector)) == pytest.approx(vector, abs=1e-7)
    assert corrections.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0 and corrections.cosine([0.0], [0.0]) == 0.0


def test_pipeline_puts_published_notes_first(setup, monkeypatch):
    setup["db"].rows["n1"] = {"document": "lm35", "page": 4, "text": "note text", "status": "published"}
    monkeypatch.setattr(search, "search", lambda conn, q, mode, document=None, embed_fn=None: [{"id": 1}])
    hit = {"id": 1, "document": "lm35", "page": 2, "page_label": None, "kind": "text", "text": "datasheet text"}
    monkeypatch.setattr(pipeline.rerank, "rerank", lambda conn, q, hits, client=None: [hit])
    seen = {}

    def fake_generate(question, chunks, client=None):
        seen["chunks"] = chunks
        return {"answer": "[1]", "sources": [], "invalid": [], "not_contained": False, "cut_off": False}

    monkeypatch.setattr(pipeline.generate, "generate", fake_generate)
    result = pipeline.answer_question("conn", "q", document="lm35")
    assert [c["kind"] for c in seen["chunks"]] == ["note", "text"] and "notes" in result["timings_ms"]
    monkeypatch.setattr(config, "CORRECTIONS_ENABLED", False)
    pipeline.answer_question("conn", "q")
    assert [c["kind"] for c in seen["chunks"]] == ["text"]


def test_kb_pipeline_puts_notes_first(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(config, "KB_ID", "KB12345678")
    monkeypatch.setattr(kb, "retrieve", lambda question, document=None, client=None: [
        {"id": "c", "document": "lm35", "page": 2, "page_label": None, "kind": "text", "text": "t", "score": 1}])
    seen = {}
    monkeypatch.setattr(kb.generate, "generate", lambda question, chunks, client=None: seen.update(chunks=chunks) or {
        "answer": "x", "sources": [], "invalid": [], "not_contained": False, "cut_off": False})
    note = corrections.note_hit("n1", "lm35", 4, "note", 0.9)
    result = kb.answer_question("q", notes_fn=lambda q, d: [note])
    assert seen["chunks"][0] is note and "notes" in result["timings_ms"]


# ---------------------------------------------------------------- the prompt

def test_a_note_is_labelled_and_adds_its_rule():
    note = corrections.note_hit("n1", "lm35", 4, "The LM35 is ±0.5 °C.", 0.9)
    plain = {"id": 1, "document": "lm35", "page": 4, "page_label": None, "kind": "text", "text": "x"}
    assert generate.passage_label(note) == "Verified note: LM35 (lm35), page 4"
    with_note, _ = generate.build_prompt("q", [note, plain])
    without, _ = generate.build_prompt("q", [plain])
    assert without[0]["text"] == generate.system_prompt() and "Verified note" not in without[0]["text"]
    assert "6. A passage labelled \"Verified note\"" in with_note[0]["text"]
    assert with_note[0]["text"].endswith("7. Only if no passage answers the question, reply with exactly this "
                                         "sentence, with no citation and nothing else:\n" + config.NOT_CONTAINED)
    cited = generate.resolve_citations("±0.5 °C [1] [2]", [note, plain])["sources"]
    assert cited[0]["note"] is True and "note" not in cited[1]


# ---------------------------------------------------------------- the reviewer's routes

@pytest.fixture
def reviewing(setup):
    store = setup["store"]
    answer = {"question": "How accurate is the LM35?", "document": None, "pipeline": "ours", "answer": "±0.2 °C [1]",
              "sources": [{"label": 1, "document": "lm35", "page": 4}], "passages": [], "notes": []}
    store.put_new(records.FEEDBACK + ANSWER, {"answer_id": ANSWER, "rating": "down", "reason": "wrong",
                                              "comment": "<b>wrong</b>", "status": "pending", "created_at": 5,
                                              "expires_at": None, "answer": answer})
    store.put_new(records.FEEDBACK + "cd" * 16, {"answer_id": "cd" * 16, "rating": "up", "status": "noted",
                                                 "created_at": 6, "expires_at": None, "answer": answer})
    store.put_new(records.ANSWER + ANSWER, {"pipeline": "ours", "notes": [], "expires_at": None})
    deps = review.Deps(store=store, open_conn=opener)
    return lambda method, path, payload=None, query=None: review.route(method, "/api/review/" + path, query or {},
                                                                       payload, "rev", deps)


def note_payload(**fields):
    return {"answer_id": ANSWER, "document": "lm35", "page": 4, "checked": True,
            "text": "The LM35 is accurate to ±0.5 °C at 25 °C; ±0.2 °C is the LM35A.", **fields}


def test_the_queue_has_pending_thumbs_downs_only(reviewing):
    status, body = reviewing("GET", "feedback")
    assert status == 200 and [item["answer_id"] for item in body["items"]] == [ANSWER]
    assert body["items"][0]["comment"] == "<b>wrong</b>"  # stored as written; the page shows it as text
    assert len(reviewing("GET", "feedback", query={"all": "1"})[1]["items"]) == 2


def test_a_closing_diagnosis_ends_the_item(reviewing):
    assert reviewing("POST", f"feedback/{ANSWER}", {"diagnosis": "correct"})[1]["status"] == "closed"
    assert reviewing("GET", "feedback")[1]["items"] == []
    assert reviewing("POST", f"feedback/{ANSWER}", {"diagnosis": "misread"})[0] == 409


def test_a_note_needs_a_correcting_diagnosis_a_page_and_the_check(reviewing):
    assert reviewing("POST", "corrections", note_payload())[0] == 409  # not diagnosed yet
    assert reviewing("POST", f"feedback/{ANSWER}", {"diagnosis": "nonsense"})[0] == 400
    assert reviewing("POST", f"feedback/{ANSWER}", {"diagnosis": "misread", "note": "LM35A row"})[0] == 200
    assert reviewing("POST", "corrections", note_payload(checked=False))[0] == 400
    assert reviewing("POST", "corrections", note_payload(document="upload-0123456789abcdef"))[0] == 400
    assert reviewing("POST", "corrections", note_payload(page=0))[0] == 400
    assert reviewing("POST", "corrections", note_payload(text="short"))[0] == 400
    status, record = reviewing("POST", "corrections", note_payload())
    assert status == 200 and record["status"] == "draft" and record["question"] == "How accurate is the LM35?"
    assert reviewing("GET", "feedback")[1]["items"] == []  # corrected
    assert reviewing("POST", "corrections", note_payload())[0] == 409  # one note per rating


def test_publish_and_retract_through_the_routes(reviewing):
    reviewing("POST", f"feedback/{ANSWER}", {"diagnosis": "misread"})
    cid = reviewing("POST", "corrections", note_payload())[1]["correction_id"]
    assert reviewing("POST", f"corrections/{cid}/publish", {})[1]["status"] == "published"
    assert reviewing("GET", f"corrections/{cid}")[1]["status"] == "published"
    assert reviewing("POST", f"corrections/{cid}/publish", {})[0] == 409
    assert reviewing("POST", f"corrections/{cid}/retract", {})[1]["status"] == "retracted"
    assert [n["status"] for n in reviewing("GET", "corrections")[1]["corrections"]] == ["retracted"]
    assert reviewing("GET", "corrections/nope")[0] == 404


def test_the_page_text_route(reviewing, monkeypatch):
    monkeypatch.setattr(db, "page_chunks", lambda conn, document, page, strategy: [f"{document} p{page}"])
    monkeypatch.setattr(db, "list_documents", lambda conn: [{"name": "lm35", "file": "lm35.pdf"}])
    status, body = reviewing("GET", "page", query={"document": "lm35", "page": "4"})
    assert status == 200 and body["texts"] == ["lm35 p4"] and body["part"] == "LM35"
    assert reviewing("GET", "page", query={"document": "nope", "page": "4"})[0] == 400
    assert reviewing("GET", "page", query={"document": "lm35", "page": "four"})[0] == 400


def test_the_report_counts_ratings(reviewing):
    body = reviewing("GET", "report")[1]
    assert body["pipelines"]["ours"] == {"answers": 1, "rated": 2, "down": 1, "up": 1, "down_rate": 0.5}
    assert body["reasons"] == {"wrong": 1}


def test_unknown_routes_and_groups():
    assert review.route("GET", "/api/review/nothing", {}, None, "rev", review.Deps(None, None))[0] == 404
    assert review.groups({"cognito:groups": "[reviewers admins]"}) == ["reviewers", "admins"]
    assert review.groups({"cognito:groups": ["reviewers"]}) == ["reviewers"]
    assert review.groups({}) == []


# ---------------------------------------------------------------- the corrections table (local Postgres)

def test_corrections_table_matches_published_notes_only(conn):
    vector = [1.0] + [0.0] * (config.EMBED_DIMENSIONS - 1)
    other = [0.0, 1.0] + [0.0] * (config.EMBED_DIMENSIONS - 2)
    db.store_correction(conn, "n1", "lm35", 4, "note one", vector, "published")
    db.store_correction(conn, "n2", "lm35", 4, "note two", vector, "pending")
    db.store_correction(conn, "n3", "esp32_datasheet_en", 9, "note three", other, "published")
    found = db.match_corrections(conn, vector, None, 0.5, 5)
    assert [row["correction_id"] for row in found] == ["n1"] and found[0]["similarity"] == pytest.approx(1.0)
    assert db.match_corrections(conn, vector, "esp32_datasheet_en", 0.0, 5)[0]["correction_id"] == "n3"
    assert [r["correction_id"] for r in db.match_corrections(conn, vector, None, 0.5, 5, ("pending",))] == ["n2"]
    assert db.set_correction_status(conn, "n1", "retracted") and db.match_corrections(conn, vector, None, 0.5, 5) == []
    db.store_correction(conn, "n1", "lm35", 5, "note one again", vector, "published")
    assert db.match_corrections(conn, vector, "lm35", 0.5, 5)[0]["page"] == 5
    assert set(db.correction_similarities(conn, vector)) == {"n1", "n3"}


def test_both_pipelines_pick_the_same_notes(conn):
    """The KB side (Python cosine on the published list) and ours (pgvector) give the same notes, in the same order,
    with the same similarities, on both sides of the threshold."""
    import random

    rng = random.Random(9)

    def vector_near(base, spread):
        return [x + rng.gauss(0, spread) for x in base]

    base = [rng.gauss(0, 1) for _ in range(DIMS)]
    store = records.MemoryStore()
    for number, spread in enumerate((0.2, 0.6, 1.0, 2.0)):
        cid = f"{number:02d}" * 16
        db.store_correction(conn, cid, "lm35", number + 1, f"note {number}", vector_near(base, spread), "published")
        store.put_new(corrections.key(cid), {"correction_id": cid, "document": "lm35", "page": number + 1,
                                             "text": f"note {number}", "status": "published", "expires_at": None})
        corrections._add(store, conn, corrections.list_entry(
            store.get(corrections.key(cid)), db.correction_vectors(conn, [cid])[cid]))
    found = []
    for question, spread in (("near", 0.1), ("middle", 0.9), ("far", 3.0)):
        vector = vector_near(base, spread)
        ours = corrections.match(conn, question, None, embed_fn=lambda text, v=vector: v)
        theirs = corrections.kb_match(question, None, store=store, embed_fn=lambda text, v=vector: v)
        assert [n["correction_id"] for n in theirs] == [n["correction_id"] for n in ours]
        assert [round(n["score"], 4) for n in theirs] == [round(n["score"], 4) for n in ours]
        found.append(len(ours))
    assert found[0] == config.CORRECTION_MAX and found[-1] == 0  # a note on both sides, then none on either


def test_a_one_datasheet_kb_search_reads_the_curated_source_only(monkeypatch):
    monkeypatch.setattr(config, "KB_CURATED_DATA_SOURCE_ID", "DSCURATED")
    filters = kb.search_config("lm35", 10)["managedSearchConfiguration"]["filter"]["andAll"]
    assert {"equals": {"key": "_data_source_id", "value": "DSCURATED"}} in filters
    upload = kb.search_config("upload-0123456789abcdef", 10)["managedSearchConfiguration"]["filter"]
    assert upload == {"equals": {"key": "document", "value": "upload-0123456789abcdef"}}


# ---------------------------------------------------------------- discarding a note

def test_discard_sends_the_rating_back_to_the_queue(reviewing):
    reviewing("POST", f"feedback/{ANSWER}", {"diagnosis": "false_refusal"})
    cid = reviewing("POST", "corrections", note_payload(document="esp32_datasheet_en", page=46))[1]["correction_id"]
    assert reviewing("GET", "feedback")[1]["items"] == []
    status, body = reviewing("POST", f"corrections/{cid}/discard", {})
    assert status == 200 and body["status"] == "discarded" and body["rating_back"] is True
    item = reviewing("GET", "feedback")[1]["items"][0]
    assert item["status"] == "diagnosed" and item["diagnosis"] == "false_refusal" and item["correction_id"] is None
    assert item["last_note"] == {"document": "esp32_datasheet_en", "page": 46, "question": "How accurate is the LM35?",
                                 "text": note_payload()["text"]}  # the form opens filled in
    assert reviewing("GET", "corrections")[1]["corrections"] == []
    assert reviewing("POST", "corrections", note_payload())[0] == 200  # a new note for the same rating


def test_a_published_note_must_be_retracted_before_it_is_discarded(reviewing):
    reviewing("POST", f"feedback/{ANSWER}", {"diagnosis": "misread"})
    cid = reviewing("POST", "corrections", note_payload())[1]["correction_id"]
    reviewing("POST", f"corrections/{cid}/publish", {})
    reviewing("GET", f"corrections/{cid}")
    assert reviewing("POST", f"corrections/{cid}/discard", {})[0] == 409
    reviewing("POST", f"corrections/{cid}/retract", {})
    assert reviewing("POST", f"corrections/{cid}/discard", {})[1]["status"] == "discarded"
    assert reviewing("POST", f"corrections/{cid}/discard", {})[0] == 409  # already gone


def test_discarding_a_failed_note_retracts_its_row_first(setup):
    s, cid = setup, draft(setup["store"])
    s["store"].put_new(records.FEEDBACK + ANSWER, {"answer_id": ANSWER, "status": "corrected", "correction_id": cid,
                                                   "expires_at": None})
    s["db"].similarity = 0.0
    assert corrections.publish(s["store"], cid, "rev", opener)["status"] == "failed"
    record = corrections.discard(s["store"], cid, "rev", opener)
    assert record["status"] == "discarded" and s["db"].rows[cid]["status"] == "retracted"
    assert s["store"].get(records.FEEDBACK + ANSWER)["status"] == "diagnosed"


def test_the_list_is_seeded_once_with_the_notes_published_before(setup):
    s = setup
    old = draft(s["store"], correction_id="aa" * 16, status="published")
    s["db"].rows[old] = {"document": "lm35", "page": 4, "text": "old", "status": "published"}
    corrections.publish(s["store"], draft(s["store"]), "rev", opener)
    assert corrections.published_ids(s["store"]) == ["aa" * 16, "cd" * 16]
    assert s["store"].get(corrections.PUBLISHED_KEY)["version"] == 1


def test_a_list_edit_that_loses_a_race_reads_again(setup):
    s = setup
    corrections.publish(s["store"], draft(s["store"]), "rev", opener)
    real_update, raced = s["store"].update, []

    def update(key, expect=None, **fields):
        if key == corrections.PUBLISHED_KEY and not raced:  # another reviewer's edit lands first
            raced.append(1)
            item = s["store"].get(key)
            real_update(key, notes=item["notes"] + [dict(item["notes"][0], correction_id="ee" * 16)],
                        version=item["version"] + 1)
        return real_update(key, expect, **fields)

    s["store"].update = update
    corrections.publish(s["store"], draft(s["store"], correction_id="ff" * 16), "rev", opener)
    assert corrections.published_ids(s["store"]) == ["cd" * 16, "ee" * 16, "ff" * 16]  # both edits kept


def test_a_retract_during_the_publish_undoes_it(setup, monkeypatch):
    s, cid = setup, draft(setup["store"])
    real_add = corrections._add

    def add_then_retract(store, conn, entry):
        real_add(store, conn, entry)
        corrections.retract(store, cid, "rev", opener)

    monkeypatch.setattr(corrections, "_add", add_then_retract)
    record = corrections.publish(s["store"], cid, "rev", opener)
    assert record["status"] == "retracted" and s["db"].rows[cid]["status"] == "retracted"
    assert corrections.published_ids(s["store"]) == []


def test_a_failure_after_the_record_is_published_takes_it_out_again(setup, monkeypatch):
    s, cid = setup, draft(setup["store"])

    def broken(*args):
        raise RuntimeError("throttled")

    monkeypatch.setattr(corrections, "_add", broken)
    with pytest.raises(RuntimeError):
        corrections.publish(s["store"], cid, "rev", opener)
    assert s["store"].get(corrections.key(cid))["status"] == "failed"
    assert s["db"].rows[cid]["status"] == "retracted" and corrections.published_ids(s["store"]) == []


def test_a_notes_failure_does_not_fail_the_kb_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(kb, "retrieve", lambda question, document=None, client=None: [
        {"id": "c", "document": "lm35", "page": 2, "page_label": None, "kind": "text", "text": "t", "score": 1}])
    seen = {}
    monkeypatch.setattr(kb.generate, "generate", lambda question, chunks, client=None: seen.update(chunks=chunks) or {
        "answer": "x", "sources": [], "invalid": [], "not_contained": False, "cut_off": False})

    def down(question, document):
        raise RuntimeError("USAGE_TABLE is not set")

    result = kb.answer_question("q", notes_fn=down)
    assert result["answer"] == "x" and [c["kind"] for c in seen["chunks"]] == ["text"]
