"""Offline tests of the deployed evaluation: no network, no AWS, no database."""
import csv
import http.client
import io
import json
import urllib.error

import pytest

from rag import config
from rag.eval import deployed_eval as de

QUESTION = {"id": "q01", "type": "single_value", "question": "What is the absolute maximum supply voltage?",
            "sources": [("lm35", 4)]}
OK_BODY = {"answer": "35 V [1]", "not_contained": False, "sources": [{"document": "lm35", "page": 4}],
           "warnings": [], "timings_ms": {"search": 50.0, "rerank": 400.0, "generate": 500.0}, "spent_usd": 0.0023}


def response(status, body=None, client_ms=1000.0, headers=None):
    return {"status": status, "headers": headers or {}, "body": body, "text": json.dumps(body), "client_ms": client_ms}


class Sender:
    """Answers with the given responses in order and records the questions sent."""

    def __init__(self, *responses):
        self.responses, self.sent = list(responses), []

    def __call__(self, url, question):
        self.sent.append(question)
        return self.responses.pop(0)


def clock_now():
    times = iter(f"2026-10-18T12:00:{second:02d}.000+00:00" for second in range(60))
    return lambda: next(times)


# --- send ---------------------------------------------------------------------------------------------------


class FakeHTTPResponse(io.BytesIO):
    status = 200

    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"X-Cache": "Miss from cloudfront"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_send_reads_status_headers_and_json():
    seen = {}

    def opener(request, timeout):
        seen["body"], seen["type"], seen["timeout"] = request.data, request.get_header("Content-type"), timeout
        return FakeHTTPResponse(json.dumps(OK_BODY).encode())

    result = de.send("https://site", "Q?", opener=opener)
    assert result["status"] == 200 and result["body"]["answer"] == "35 V [1]"
    assert result["headers"]["x-cache"] == "Miss from cloudfront"
    assert json.loads(seen["body"]) == {"question": "Q?", "document": None, "pipeline": "ours"}
    assert seen["type"] == "application/json" and seen["timeout"] == config.DEPLOYED_TIMEOUT_S


def test_send_turns_http_error_into_status():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {}, io.BytesIO(b'{"message":"Too Many"}'))

    result = de.send("https://site", "Q?", opener=opener)
    assert result["status"] == 429 and result["body"] == {"message": "Too Many"}


@pytest.mark.parametrize("error", [TimeoutError("timed out"), http.client.IncompleteRead(b"par")])
def test_send_gives_status_0_when_nothing_usable_comes_back(error):
    def opener(request, timeout):
        raise error

    result = de.send("https://site", "Q?", opener=opener)
    assert result["status"] == 0 and result["body"] is None and type(error).__name__ in result["text"]


def test_ask_with_retries_sends_once_when_attempts_is_0(monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED_MAX_ATTEMPTS", 0)
    result = de.ask_with_retries("u", "Q?", Sender(response(503, None)), lambda s: None, clock_now())
    assert result["attempts"] == 1


# --- retries ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status,body,expected", [
    (200, OK_BODY, None),
    (400, {"error": "Type a question first."}, None),
    (502, {"error": "A step stopped"}, None),
    (429, {"error": "AWS is limiting calls"}, "retry"),
    (429, {"message": "Too Many Requests"}, "retry"),
    (503, {"message": "Service Unavailable"}, "retry"),
    (504, None, "retry"),
    (0, None, "retry"),
    (429, {"error": "limit", "limit": "daily"}, "stop"),
    (503, {"error": "The demo is switched off right now.", "off": True}, "stop"),
])
def test_retry_decision(status, body, expected):
    assert de.retry_decision(status, body) == expected


def test_ask_with_retries_waits_and_keeps_every_status():
    sender = Sender(response(429, {"message": "Too Many Requests"}, 120.0), response(504, None, 30000.0),
                    response(200, OK_BODY, 900.0))
    slept = []
    result = de.ask_with_retries("u", "Q?", sender, slept.append, clock_now())
    assert result["status"] == 200 and result["attempts"] == 3
    assert result["attempt_statuses"] == [429, 504, 200]
    assert result["first_client_ms"] == 120.0 and result["client_ms"] == 900.0
    assert slept == [config.DEPLOYED_RETRY_WAIT_S] * 2
    assert result["sent_at"].endswith("12:00:02.000+00:00")  # the send that was answered


def test_ask_with_retries_gives_up_after_max_attempts():
    sender = Sender(*[response(503, None)] * config.DEPLOYED_MAX_ATTEMPTS)
    result = de.ask_with_retries("u", "Q?", sender, lambda s: None, clock_now())
    assert result["status"] == 503 and result["attempts"] == config.DEPLOYED_MAX_ATTEMPTS
    assert sender.responses == []


def test_daily_limit_stops():
    sender = Sender(response(429, {"error": "today's limit", "limit": "daily"}))
    with pytest.raises(de.DailyLimit):
        de.ask_with_retries("u", "Q?", sender, lambda s: None, clock_now())


# --- rows and resume ----------------------------------------------------------------------------------------


def answered(**extra):
    return {**response(200, {**OK_BODY, **extra}, 950.0, {"x-cache": "Miss from cloudfront", "x-amz-cf-pop": "YTO"}),
            "sent_at": "2026-10-18T12:00:00.000+00:00", "attempts": 1, "attempt_statuses": [200],
            "first_client_ms": 950.0}


def test_result_row_of_an_answer():
    row = de.result_row(QUESTION, "first", answered())
    assert row["cited"] == "lm35:4" and row["not_contained"] is False and row["invalid"] is False
    assert (row["search_ms"], row["rerank_ms"], row["generate_ms"]) == (50.0, 400.0, 500.0)
    assert row["spent_usd"] == 0.0023 and row["cf_pop"] == "YTO" and row["error"] is None


def test_result_row_flags_invalid_labels_from_warnings():
    row = de.result_row(QUESTION, "first", answered(warnings=["The answer cites passages that do not exist: [12]"]))
    assert row["invalid"] is True


def test_result_row_of_a_gateway_error():
    failed = {**response(504, {"message": "Gateway Timeout"}, 30000.0), "sent_at": "2026-10-18T12:00:00.000+00:00",
              "attempts": 4, "attempt_statuses": [504] * 4, "first_client_ms": 30000.0}
    row = de.result_row(QUESTION, "cold", failed)
    assert row["answer"] is None and row["cited"] is None and row["error"] == "Gateway Timeout"
    assert row["attempt_statuses"] == "504; 504; 504; 504"


def test_run_pass_paces_resumes_and_writes_rows(tmp_path):
    path = tmp_path / "first.csv"
    second = {**QUESTION, "id": "q02", "question": "Second?"}
    third = {**QUESTION, "id": "q03", "question": "Third?"}
    de.append_row(path, de.result_row(QUESTION, "first", answered()))
    sender = Sender(response(200, OK_BODY), response(200, OK_BODY))
    slept, ticks = [], iter([0.0, 5.0, 5.0])
    asked = de.run_pass("u", [QUESTION, second, third], "first", path, sender, slept.append, lambda: next(ticks),
                        clock_now())
    assert asked == 2 and sender.sent == ["Second?", "Third?"]
    assert slept == [config.DEPLOYED_PACE_S - 5.0]
    assert [row["id"] for row in de.latest_rows(path)] == ["q01", "q02", "q03"]
    assert de.done_ids(path) == {"q01", "q02", "q03"}


def test_pace_counts_from_the_last_retry(tmp_path):
    second = {**QUESTION, "id": "q02", "question": "Second?"}
    sender = Sender(response(429, {"message": "Too Many Requests"}), response(200, OK_BODY), response(200, OK_BODY))
    slept, ticks = [], iter([60.0, 61.0, 70.0])  # q01 answered at 60 s, after its retry
    de.run_pass("u", [QUESTION, second], "first", tmp_path / "first.csv", sender, slept.append,
                lambda: next(ticks), clock_now())
    assert slept == [config.DEPLOYED_RETRY_WAIT_S, config.DEPLOYED_PACE_S - 1.0]


def test_a_failed_question_is_asked_again_and_its_last_row_counts(tmp_path):
    path = tmp_path / "first.csv"
    failed = {**response(502, {"error": "A step stopped"}), "sent_at": "2026-10-18T12:00:00.000+00:00",
              "attempts": 1, "attempt_statuses": [502], "first_client_ms": 1.0}
    de.append_row(path, de.result_row(QUESTION, "first", failed))
    assert de.done_ids(path) == set()
    de.run_pass("u", [QUESTION], "first", path, Sender(response(200, OK_BODY)), lambda s: None, lambda: 0.0,
                clock_now())
    assert [row["status"] for row in de.latest_rows(path)] == ["200"]


def test_run_cold_waits_idle_before_each_trial(tmp_path):
    slept = []
    rows = de.run_cold("u", QUESTION, 2, 600, tmp_path / "cold.csv",
                       Sender(response(200, OK_BODY), response(200, OK_BODY)), slept.append, clock_now())
    assert len(rows) == 2 and slept == [600, 600]


# --- dry run ------------------------------------------------------------------------------------------------


def test_plan_never_pays(monkeypatch):
    calls = {}
    monkeypatch.setattr(de.search, "uncached_questions", lambda conn, texts: ["New?"])

    def fake_search(conn, text, mode, embed_fn=None):
        with pytest.raises(RuntimeError):
            embed_fn(text)  # the plan's embed function refuses to pay
        calls.setdefault("searched", []).append(text)
        return [{"id": 1}]

    monkeypatch.setattr(de.search, "search", fake_search)
    monkeypatch.setattr(de.rerank, "calls_needed", lambda conn, text, hits: 1 if text == "Old uncached?" else 0)
    monkeypatch.setattr(de.usage, "count", lambda kind: 7)
    monkeypatch.setattr(de, "_local_tokens", lambda reference=None: (5000.0, 50.0))
    qs = [{**QUESTION, "question": text} for text in ("New?", "Old uncached?", "Old cached?")]
    plan = de.plan_pass(object(), qs)
    assert plan["embed_calls"] == 1 and plan["rerank_calls"] == 2 and plan["generate_calls"] == 3
    assert calls["searched"] == ["Old uncached?", "Old cached?"]
    assert plan["cost"]["rerank"] == pytest.approx(2 * config.RERANK_USD_PER_QUERY)
    assert plan["cost"]["generate"] == pytest.approx(3 * (5 * config.GENERATE_USD_PER_1K_INPUT
                                                          + 0.05 * config.GENERATE_USD_PER_1K_OUTPUT))
    assert plan["daily_asks"] == 7 and plan["minutes"] == pytest.approx(3 * config.DEPLOYED_PACE_S / 60)


# --- CloudWatch ---------------------------------------------------------------------------------------------

REPORT_COLD = ("REPORT RequestId: aaaa-1111\tDuration: 1046.53 ms\tBilled Duration: 2005 ms\tMemory Size: 512 MB\t"
               "Max Memory Used: 122 MB\tInit Duration: 958.12 ms\t\n")
REPORT_WARM = ("REPORT RequestId: bbbb-2222\tDuration: 410.20 ms\tBilled Duration: 411 ms\tMemory Size: 512 MB\t"
               "Max Memory Used: 123 MB\t\n")


def test_parse_report_cold_and_warm():
    cold = de.parse_report(REPORT_COLD)
    assert cold == {"duration_ms": 1046.53, "billed_ms": 2005.0, "max_memory_mb": 122.0, "init_ms": 958.12}
    assert de.parse_report(REPORT_WARM)["init_ms"] is None
    assert de.parse_report(REPORT_WARM)["duration_ms"] == 410.20


def handler_line(timings, connect=20.0):
    return json.dumps({"method": "POST", "path": "/api/ask", "connect_ms": connect, "timings_ms": timings,
                       "spent_usd": 0.002, "status": 200, "total_ms": 900.0})


def cost_line(usd):
    return json.dumps({"cost": {"operation": "rerank", "model_id": "m", "unit": "queries", "quantity": 1,
                                "est_cost_usd": usd}})


def events_two_streams():
    """Two containers whose lines interleave in time, as FilterLogEvents returns them."""
    timings = {"search": 50.0, "rerank": 400.0, "generate": 500.0}
    return [
        {"logStreamName": "a", "timestamp": 1000, "message": "START RequestId: aaaa-1111 Version: $LATEST\n"},
        {"logStreamName": "b", "timestamp": 1001, "message": "START RequestId: bbbb-2222 Version: $LATEST\n"},
        {"logStreamName": "a", "timestamp": 1500, "message": cost_line(0.002)},
        {"logStreamName": "b", "timestamp": 1502, "message": "some print that is not JSON\n"},
        {"logStreamName": "a", "timestamp": 1600, "message": cost_line(0.0003)},
        {"logStreamName": "a", "timestamp": 1700, "message": handler_line(timings, 17900.0)},
        {"logStreamName": "b", "timestamp": 1703, "message": handler_line({"search": 1.0, "rerank": 2.0,
                                                                            "generate": 3.0})},
        {"logStreamName": "a", "timestamp": 1800, "message": "END RequestId: aaaa-1111\n"},
        {"logStreamName": "a", "timestamp": 1801, "message": REPORT_COLD},
        {"logStreamName": "b", "timestamp": 1802, "message": REPORT_WARM},
    ]


def test_group_invocations_keeps_streams_apart():
    invocations = {inv["request_id"]: inv for inv in de.group_invocations(events_two_streams())}
    cold, warm = invocations["aaaa-1111"], invocations["bbbb-2222"]
    assert cold["cost_usd"] == pytest.approx(0.0023) and cold["costs"] == 2
    assert cold["handler"]["connect_ms"] == 17900.0 and cold["init_ms"] == 958.12
    assert warm["costs"] == 0 and warm["init_ms"] is None and warm["start_ms"] == 1001


def row_sent_at(epoch_ms, **extra):
    from datetime import UTC, datetime
    sent = datetime.fromtimestamp(epoch_ms / 1000, UTC).isoformat(timespec="milliseconds")
    return {"id": "q01", "status": "200", "sent_at": sent, "search_ms": "50.0", "rerank_ms": "400.0",
            "generate_ms": "500.0", **extra}


def test_match_rows_by_time_and_timings():
    invocations = de.group_invocations(events_two_streams())
    # Both requests started within the window of this row; the first unused in START order (aaaa) is taken.
    first = de.match_rows([row_sent_at(990)], invocations)[0]
    assert first["request_id"] == "aaaa-1111" and first["log_match"] == "ok"
    assert first["connect_ms"] == 17900.0 and first["log_cost_usd"] == pytest.approx(0.0023)
    both = de.match_rows([row_sent_at(990), row_sent_at(995)], invocations)
    assert both[1]["request_id"] == "bbbb-2222" and both[1]["log_match"] == "timings differ"


def test_match_rows_without_a_request_in_the_window():
    later = 1000 + (de.MATCH_BEFORE_S + 1) * 1000  # both STARTs are too long before this send
    far = de.match_rows([row_sent_at(later)], de.group_invocations(events_two_streams()))[0]
    assert far["log_match"] == "none" and far["request_id"] is None


def test_fetch_events_follows_pages():
    class Logs:
        def __init__(self):
            self.calls = []

        def filter_log_events(self, **kwargs):
            self.calls.append(kwargs)
            if "nextToken" not in kwargs:
                return {"events": [{"message": "1"}], "nextToken": "t"}
            return {"events": [{"message": "2"}]}

    logs = Logs()
    assert [e["message"] for e in de.fetch_events(logs, 0, 10)] == ["1", "2"]
    assert logs.calls[0]["logGroupName"] == "/aws/lambda/datasheet-rag" and logs.calls[1]["nextToken"] == "t"


# --- summary ------------------------------------------------------------------------------------------------


def test_percentile():
    assert de.percentile([1, 2, 3, 4], 50) == 2.5
    assert de.percentile(["10", "", None, "30"], 100) == 30.0
    assert de.percentile([], 50) is None
    assert de.percentile([5], 90) == 5.0


@pytest.mark.parametrize("value,invalid", [(True, True), ("True", True), ("False", False), ("", False),
                                           (None, False), ("[]", False), ("11", True), ("11; 12", True)])
def test_has_invalid_reads_both_csv_forms(value, invalid):
    assert de._has_invalid(value) is invalid


def test_lambda_usd():
    assert de.lambda_usd(1000) == pytest.approx(0.5 * config.LAMBDA_USD_PER_GB_S + config.LAMBDA_USD_PER_REQUEST)


@pytest.mark.parametrize("ours,local,same", [
    ({"cited": "lm35:4; lm35:7", "not_contained": "False"}, {"cited": "lm35:7; lm35:4", "not_contained": "False"},
     True),
    ({"cited": "lm35:4", "not_contained": "False"}, {"cited": "lm35:7", "not_contained": "False"}, False),
    ({"cited": "", "not_contained": "True"}, {"cited": "lm35:4", "not_contained": "True"}, True),
    ({"cited": "lm35:4", "not_contained": "False"}, {"cited": "", "not_contained": "True"}, False),
    ({"cited": "esp32_datasheet_en:51", "not_contained": "False"},
     {"cited": "esp32_datasheet_en:51", "not_contained": "False"}, True),
])
def test_same_answer(ours, local, same):
    assert de.same_answer(ours, local) is same


def test_summary_row_scores_like_the_local_metrics():
    rows = [
        {"id": "q01", "status": "200", "attempts": "1", "cited": "lm35:4", "not_contained": "False", "invalid": "False",
         "client_ms": "1000", "spent_usd": "0.002", "billed_ms": "1000", "duration_ms": "999", "connect_ms": "50",
         "search_ms": "10", "rerank_ms": "400", "generate_ms": "500", "init_ms": "", "request_id": "a",
         "log_cost_usd": "0.002"},
        {"id": "q02", "status": "200", "attempts": "2", "cited": "", "not_contained": "True", "invalid": "False",
         "client_ms": "3000", "spent_usd": "0.0004", "billed_ms": "3000", "duration_ms": "2999",
         "connect_ms": "18000", "search_ms": "20", "rerank_ms": "10", "generate_ms": "600", "init_ms": "900",
         "request_id": "b", "log_cost_usd": "0.0004"},
        {"id": "q49", "status": "200", "attempts": "1", "cited": "", "not_contained": "True", "invalid": "True",
         "client_ms": "2000", "spent_usd": "0.0003", "billed_ms": "", "request_id": ""},
    ]
    truths = {"q01": [("lm35", 4)], "q02": [("lm35", 7)], "q49": []}
    local = {"q01": {"cited": "lm35:4", "not_contained": "False"}, "q02": {"cited": "lm35:7", "not_contained": "False"}}
    row = de.summary_row("first", rows, truths, local, 0.001)
    assert row["citation_hit_rate"] == 0.5 and row["answerable_refused"] == 0.5 and row["unanswerable_refused"] == 1.0
    assert row["same_as_local"] == "1/2" and row["retried"] == 1 and row["invalid_labels"] == 1
    assert row["client_ms_p50"] == 2000.0 and row["client_ms_max"] == 3000.0 and row["connect_ms_max"] == 18000.0
    assert row["cold_inits"] == 1 and row["model_usd_total"] == pytest.approx(0.0027)
    assert row["log_cost_usd_total"] == pytest.approx(0.0024)
    assert row["lambda_usd_per_query"] == pytest.approx((de.lambda_usd(1000) + de.lambda_usd(3000)) / 2)
    assert row["aurora_usd_per_query"] == 0.001


def test_summarise_spreads_aurora_over_every_question(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED_DIR", tmp_path / "deployed")
    monkeypatch.setattr(config, "EVAL_DIR", tmp_path / "eval")
    (tmp_path / "eval").mkdir()
    with (tmp_path / "eval" / f"{config.DEPLOYED_LOCAL_REFERENCE}.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "cited", "not_contained", "search_ms", "generate_ms"])
        writer.writeheader()
        writer.writerow({"id": "q01", "cited": "lm35:4", "not_contained": "False", "search_ms": "60",
                         "generate_ms": "510"})
    de.append_row(de.pass_path("first"), de.result_row(QUESTION, "first", answered()))
    for _ in range(3):  # three cold trials of the same question all count
        de.append_row(de.pass_path("cold"), de.result_row(QUESTION, "cold", answered()))
    rows = de.summarise([QUESTION], aurora_acu_hours=0.5)
    assert [row["pass"] for row in rows] == [f"local:{config.DEPLOYED_LOCAL_REFERENCE}", "first", "cold"]
    assert rows[0]["citation_hit_rate"] == 1.0 and rows[0]["client_ms_p50"] is None
    assert rows[1]["same_as_local"] == "1/1" and rows[2]["questions"] == 3
    assert rows[1]["aurora_usd_per_query"] == pytest.approx(0.5 * config.AURORA_USD_PER_ACU_HOUR / 4)


# --- the Knowledge Base pass (Cycle 2 step 8) -------------------------------------------------------------------

KB_BODY = {**OK_BODY, "timings_ms": {"retrieve": 300.0, "generate": 500.0}, "spent_usd": 0.0012}


def test_send_names_the_kb_pipeline():
    seen = {}

    def opener(request, timeout):
        seen["body"] = request.data
        return FakeHTTPResponse(json.dumps(KB_BODY).encode())

    de.send("https://site", "Q?", opener=opener, pipeline="kb")
    assert json.loads(seen["body"])["pipeline"] == "kb"


class PipelineSender(Sender):
    def __call__(self, url, question, pipeline="ours"):
        self.sent.append((question, pipeline))
        return self.responses.pop(0)


def test_kb_pass_sends_the_kb_pipeline_and_keeps_retrieve_as_search(tmp_path):
    sender = PipelineSender(response(200, KB_BODY))
    de.run_pass("u", [QUESTION], "kb", tmp_path / "kb.csv", sender, lambda s: None, lambda: 0.0, clock_now())
    assert sender.sent == [(QUESTION["question"], "kb")]
    [row] = de.latest_rows(tmp_path / "kb.csv")
    assert row["search_ms"] == "300.0" and row["rerank_ms"] == "" and row["pass"] == "kb"


def test_ours_pass_keeps_the_default_pipeline(tmp_path):
    sender = PipelineSender(response(200, OK_BODY))
    de.run_pass("u", [QUESTION], "first", tmp_path / "first.csv", sender, lambda s: None, lambda: 0.0, clock_now())
    assert sender.sent == [(QUESTION["question"], "ours")]


def test_kb_log_timings_match_a_kb_row():
    row = de.result_row(QUESTION, "kb", answered(**KB_BODY))
    handler = {"timings_ms": {"retrieve": 300.0, "generate": 500.0}}
    assert de._same_timings({k: str(v) if v is not None else "" for k, v in row.items()}, handler)


def test_kb_plan_reads_only_the_kb_counter(monkeypatch):
    counted = []
    monkeypatch.setattr(de.usage, "count", lambda kind: counted.append(kind) or 7)
    monkeypatch.setattr(de.db, "connect", lambda **kw: (_ for _ in ()).throw(AssertionError("must not wake Aurora")))
    monkeypatch.setattr(de, "notes_embed_on", lambda: False)
    plan = de.plan_kb_pass([QUESTION] * 3)
    assert counted == ["kb_asks"] and plan["retrieve_calls"] == 3 and plan["embed_calls"] == 0
    assert plan["cost"]["retrieve"] == 3 * config.KB_USD_PER_RETRIEVE and plan["cost"]["embed"] == 0
    monkeypatch.setattr(de, "notes_embed_on", lambda: True)  # a published note: one Titan embed per question,
    plan = de.plan_kb_pass([QUESTION] * 3)                    # never a second Retrieve
    assert plan["retrieve_calls"] == 3 and plan["embed_calls"] == 3 and plan["cost"]["embed"] > 0
    assert plan["daily_left"] == config.DEPLOYED_KB_DAILY_LIMIT - 7


# --- step 9c: the passes with notes ---------------------------------------------------------------------------


def test_a_notes_pass_records_the_answer_id_and_its_notes(tmp_path):
    from rag import records

    store = records.MemoryStore()
    store.put_new(records.ANSWER + "ab" * 16, {"notes": ["n1"], "expires_at": None})
    body = {**OK_BODY, "answer_id": "ab" * 16}
    path = tmp_path / "notes.csv"
    de.run_pass("u", [QUESTION], "notes", path, Sender(response(200, body)), lambda s: None, lambda: 0.0,
                clock_now(), notes_fn=lambda answer_id: de.notes_used(answer_id, store))
    [row] = de._read_csv(path)
    assert row["answer_id"] == "ab" * 16 and row["notes_used"] == "n1"


def test_notes_used_is_empty_for_none_and_unknown_when_unreadable():
    from rag import records

    store = records.MemoryStore()
    store.put_new(records.ANSWER + "cd" * 16, {"notes": [], "expires_at": None})
    assert de.notes_used("cd" * 16, store) == ""
    assert de.notes_used("ef" * 16, store) is None and de.notes_used(None, store) is None

    class Down:
        def get(self, key):
            raise RuntimeError("no table")

    assert de.notes_used("cd" * 16, Down()) is None  # printed, not raised


def test_the_kb_notes_pass_asks_the_kb_pipeline(tmp_path):
    seen = {}

    def sender(url, question, pipeline="ours"):
        seen["pipeline"] = pipeline
        return response(200, OK_BODY)

    de.run_pass("u", [QUESTION], "kb_notes", tmp_path / "kb_notes.csv", sender, lambda s: None, lambda: 0.0,
                clock_now(), notes_fn=lambda answer_id: None)
    assert seen["pipeline"] == "kb"


def test_a_row_added_to_an_older_file_keeps_its_header(tmp_path):
    path = tmp_path / "cached.csv"
    old = [field for field in de.ROW_FIELDS if field not in ("answer_id", "notes_used")]
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=old).writeheader()
    de.append_row(path, {"id": "q01", "status": 200, "answer_id": "x", "notes_used": "n1"})
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == old and len(rows[1]) == len(old)


def test_compare_lists_what_the_notes_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED_DIR", tmp_path)
    base = {"status": 200, "not_contained": False}
    for pass_, cited, refused, notes in (("cached", "", True, ""), ("notes", "lm35:5", False, "n1")):
        de.append_row(de.pass_path(pass_), {**base, "id": "q08", "cited": cited, "not_contained": refused,
                                             "notes_used": notes})
        de.append_row(de.pass_path(pass_), {**base, "id": "q01", "cited": "lm35:4"})
    changes = {c["id"]: c for c in de.compare("notes", "cached", {"q08": [("lm35", 5)], "q01": [("lm35", 4)]})}
    assert changes["q08"]["changed"] and not changes["q08"]["hit_before"] and changes["q08"]["hit_after"]
    assert changes["q08"]["notes_used"] == "n1" and changes["q08"]["refused_before"]
    assert not changes["q01"]["changed"] and changes["q01"]["hit_after"]


def test_only_the_step7_passes_share_the_aurora_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED_DIR", tmp_path / "deployed")
    monkeypatch.setattr(config, "EVAL_DIR", tmp_path / "eval")  # no local reference runs
    for pass_ in ("first", "notes"):
        de.append_row(de.pass_path(pass_), {"id": "q01", "status": 200, "cited": "lm35:4", "not_contained": False})
    rows = {row["pass"]: row for row in de.summarise([QUESTION], aurora_acu_hours=1.0)}
    assert rows["first"]["aurora_usd_per_query"] == pytest.approx(config.AURORA_USD_PER_ACU_HOUR)
    assert rows["notes"]["aurora_usd_per_query"] is None


def test_compare_counts_a_refusal_lost_on_a_question_with_no_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEPLOYED_DIR", tmp_path)
    de.append_row(de.pass_path("cached"), {"id": "q44", "status": 200, "cited": "", "not_contained": True})
    de.append_row(de.pass_path("notes"), {"id": "q44", "status": 200, "cited": "lm35:5", "not_contained": False})
    [change] = de.compare("notes", "cached", {"q44": []})
    assert change["changed"] and change["hit_before"] and not change["hit_after"]


def test_an_empty_file_gets_a_header(tmp_path):
    path = tmp_path / "notes.csv"
    path.write_text("", encoding="utf-8")
    de.append_row(path, {"id": "q01", "status": 200})
    assert de._read_csv(path)[0]["id"] == "q01"


def test_a_log_line_matched_with_other_timings_is_left_out_of_the_log_numbers():
    rows = [{"id": "q01", "status": "200", "cited": "lm35:4", "not_contained": "False", "log_match": "ok",
             "connect_ms": "", "duration_ms": "900"},
            {"id": "q02", "status": "200", "cited": "lm35:4", "not_contained": "False",
             "log_match": "timings differ", "connect_ms": "115", "duration_ms": "100"}]
    row = de.summary_row("kb_notes", rows, {"q01": [("lm35", 4)], "q02": [("lm35", 4)]}, {}, None)
    assert row["connect_ms_max"] is None and row["lambda_ms_p50"] == 900
