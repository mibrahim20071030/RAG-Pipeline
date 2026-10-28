"""Offline tests of the deployed upload check (step 9c): no network, no AWS, no database."""
import json

import pytest

from rag import config
from rag.eval import upload_eval as ue

UPLOAD = "ab" * 16
DOC = f"upload-{UPLOAD}"


def test_the_case_file_is_valid():
    cases = ue.load_cases()
    assert [c["id"] for c in cases] == ["u1", "u2", "u3", "u4"]
    assert {c["kind"] for c in cases} == {"answer", "refuse"}
    assert any(not c["upload"] for c in cases)  # the isolation case asks without upload_id


def test_a_bad_case_is_refused(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"id": "x", "kind": "answer", "upload": True, "question": "q", "must_contain": [],
                                "pages": []}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ue.load_cases(path)


ANSWER = {"id": "u1", "kind": "answer", "upload": True, "question": "q", "must_contain": ["Hewitt", "1973"],
          "pages": [8]}
REFUSE = {"id": "u3", "kind": "refuse", "upload": True, "question": "q", "must_contain": [], "pages": []}


def test_an_answer_passes_with_the_values_and_a_page_of_the_upload():
    body = {"answer": "Carl Hewitt, 1973 [1].", "not_contained": False, "sources": [{"document": DOC, "page": 8}]}
    assert ue.score(ANSWER, body, UPLOAD, "")["passed"]
    wrong_page = {**body, "sources": [{"document": DOC, "page": 9}]}
    assert not ue.score(ANSWER, wrong_page, UPLOAD, "")["passed"]
    other_doc = {**body, "sources": [{"document": "lm35", "page": 8}]}
    assert not ue.score(ANSWER, other_doc, UPLOAD, "")["passed"]


def test_a_refusal_passes_only_without_a_note():
    body = {"answer": config.NOT_CONTAINED, "not_contained": True, "sources": []}
    assert ue.score(REFUSE, body, UPLOAD, "")["passed"]
    assert not ue.score(REFUSE, body, None, "n1")["passed"]  # a curated answer that was given a note
    cited_note = {**body, "sources": [{"document": "lm35", "page": 5, "note": True}]}
    assert not ue.score(REFUSE, cited_note, UPLOAD, "")["passed"]
    assert not ue.score(REFUSE, {**body, "not_contained": False}, UPLOAD, "")["passed"]


def test_the_plan_counts_one_upload_and_two_asks_a_case():
    p = ue.plan(ue.load_cases())
    assert p["uploads"] == 1 and p["asks_ours"] == p["asks_kb"] == 4 and p["cost"] > 0


class FakeSite:
    """/api/uploads, the S3 POST and the upload's record, in memory."""

    def __init__(self, states=("ingesting", "ready")):
        self.calls, self.states = [], list(states)

    def __call__(self, url, method, body=None, headers=None):
        self.calls.append((method, url))
        if url.endswith("/api/uploads"):
            return 200, json.dumps({"upload_id": UPLOAD, "post": {"url": "https://s3/bucket", "fields": {"key": "k"}}})
        if url == "https://s3/bucket":
            assert b'name="file"' in body and headers["content-type"].startswith("multipart/form-data")
            return 204, ""
        state = self.states.pop(0)
        return 200, json.dumps({"status": state, "ours": "ready" if state == "ready" else "waiting",
                                "kb": "ready" if state == "ready" else "syncing"})


def test_run_uploads_waits_then_asks_every_case_on_both_pipelines(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    asked = []

    def sender(site, question, pipeline="ours", upload_id=None):
        asked.append((pipeline, upload_id))
        refused = upload_id is None
        body = {"answer": config.NOT_CONTAINED if refused else "Carl Hewitt, 1973 [1].", "not_contained": refused,
                "sources": [] if refused else [{"document": DOC, "page": 8}], "answer_id": "cd" * 16,
                "spent_usd": 0.001}
        return {"status": 200, "body": body, "client_ms": 900.0}

    out = tmp_path / "uploads.csv"
    rows = ue.run("https://site", pdf, [ANSWER, {**REFUSE, "id": "u4", "upload": False}], sender,
                  notes_fn=lambda answer_id: "", sleep=lambda s: None, request=FakeSite(), path=out)
    assert asked == [("ours", UPLOAD), ("ours", None), ("kb", UPLOAD), ("kb", None)]
    assert all(row["passed"] for row in rows) and {row["pipeline"] for row in rows} == set(config.PIPELINES)
    assert len(out.read_text(encoding="utf-8").splitlines()) == 5  # a header, then each row as it came


def test_a_failed_upload_stops_the_run(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    class Failing(FakeSite):
        def __call__(self, url, method, body=None, headers=None):
            if "/api/uploads/" in url:
                return 200, json.dumps({"status": "failed", "error": "scanned"})
            return super().__call__(url, method, body, headers)

    with pytest.raises(RuntimeError, match="scanned"):
        ue.run("https://site", pdf, [ANSWER], lambda *a, **k: None, sleep=lambda s: None, request=Failing(),
               path=tmp_path / "uploads.csv")


def test_notes_that_cannot_be_read_fail_a_curated_case():
    body = {"answer": config.NOT_CONTAINED, "not_contained": True, "sources": []}
    result = ue.score({**REFUSE, "upload": False}, body, None, None)
    assert not result["passed"] and result["noted"] is None


def test_a_request_with_no_answer_is_status_0(monkeypatch):
    def opener(request, timeout):
        raise OSError("connection reset")

    assert ue._request("https://site/x", "GET", opener=opener)[0] == 0


def test_the_wait_stops_on_a_refused_poll_or_a_failed_kb_side():
    with pytest.raises(RuntimeError, match="404"):
        ue.wait_ready("https://site", UPLOAD, request=lambda url, method: (404, "{}"), sleep=lambda s: None)
    failed_kb = json.dumps({"status": "ready", "ours": "ready", "kb": "failed"})
    with pytest.raises(RuntimeError, match="KB side is failed"):
        ue.wait_ready("https://site", UPLOAD, request=lambda url, method: (200, failed_kb), sleep=lambda s: None)
    ready = json.dumps({"status": "ready", "ours": "ready", "kb": "ready"})
    polls = iter([(0, "reset"), (200, ready)])  # no answer once, then ready
    assert ue.wait_ready("https://site", UPLOAD, request=lambda url, method: next(polls),
                         sleep=lambda s: None)["kb"] == "ready"


def test_an_ask_is_retried_like_the_deployed_eval(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    replies = iter([503, 200])

    def sender(site, question, pipeline="ours", upload_id=None):
        status = next(replies, 200)
        body = {"answer": "Carl Hewitt, 1973 [1].", "not_contained": False,
                "sources": [{"document": DOC, "page": 8}]} if status == 200 else {"message": "busy"}
        return {"status": status, "headers": {}, "body": body, "text": "", "client_ms": 10.0}

    rows = ue.run("https://site", pdf, [ANSWER], sender, sleep=lambda s: None, request=FakeSite(),
                  path=tmp_path / "uploads.csv")
    assert rows[0]["attempts"] == 2 and rows[0]["passed"]


def test_an_earlier_upload_can_be_reused(tmp_path):
    site = FakeSite()

    def sender(site_, question, pipeline="ours", upload_id=None):
        return {"status": 200, "headers": {}, "client_ms": 1.0,
                "body": {"answer": "Carl Hewitt, 1973 [1].", "not_contained": False,
                         "sources": [{"document": DOC, "page": 8}]}}

    rows = ue.run("https://site", None, [ANSWER], sender, sleep=lambda s: None, request=site,
                  path=tmp_path / "uploads.csv", upload_id=UPLOAD)
    assert not any(url.endswith("/api/uploads") for _, url in site.calls)  # nothing uploaded
    assert rows[0]["passed"] and rows[0]["upload_id"] == UPLOAD


def test_rescore_applies_the_current_refusal_rule(tmp_path):
    path = tmp_path / "uploads.csv"
    row = {"upload_id": UPLOAD, "id": "u4", "kind": "refuse", "pipeline": "kb", "upload": "False", "status": "200",
           "attempts": "1", "passed": "False", "refused": "False", "noted": "False", "cited": "", "notes_used": "",
           "client_ms": "1", "spent_usd": "0.001", "answer": "Sorry, I cannot find a relevant answer in the given context."}
    ue.append_row(row, path)
    ue.append_row({**row, "id": "u1", "kind": "answer", "passed": "True"}, path)
    assert ue.rescore(path) == ["kb u4"]
    rows = list(__import__("csv").DictReader(path.open(encoding="utf-8")))
    assert rows[0]["passed"] == "True" and rows[0]["refused"] == "True" and rows[1]["passed"] == "True"
