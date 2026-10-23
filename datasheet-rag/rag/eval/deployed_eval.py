"""Deployed evaluation (Cycle 2 step 7): the frozen questions sent to the live site, timed and costed.

Run from datasheet-rag, with SITE_URL (CloudFront) and API_URL (API Gateway) in .env:
    python -m rag.eval.deployed_eval plan --pass first          free, wakes Aurora: calls, cost, minutes, daily count
    python -m rag.eval.deployed_eval run --pass first           paid: prints the plan, asks, then asks 49 questions
    python -m rag.eval.deployed_eval run --pass cached --yes    the same questions again, rerank scores now cached
    python -m rag.eval.deployed_eval cold --trials 3 --yes      waits idle before each trial, then asks q01
    python -m rag.eval.deployed_eval logs                       free: Lambda's CloudWatch lines joined to the rows
    python -m rag.eval.deployed_eval summary --aurora-acu-hours 0.5

Questions go out DEPLOYED_PACE_S apart, as a visitor's would, so the reranker's 3 a minute is never passed. Each
answer is appended to runs/deployed/<pass>.csv as it arrives, and a re-run skips the questions already answered. The
paid calls happen in the Lambda function, whose cost log is in its /tmp: the spend comes from each answer's
`spent_usd` and is checked against the `{"cost": ...}` lines in CloudWatch.
"""
import argparse
import csv
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from rag import config, db, rerank, search, usage
from rag.eval import metrics, questions
from rag.eval.run_eval import _embed_usd, _median, _read_csv

PASSES = ("first", "cached")
BASES = {"site": "SITE_URL", "api": "API_URL"}
COLD_QUESTION = "q01"  # cached since step 4, so a cold trial costs only the answer
ROW_FIELDS = [
    "id", "type", "pass", "sent_at", "status", "attempts", "attempt_statuses", "first_client_ms", "client_ms",
    "answer", "cited", "not_contained", "invalid", "warnings", "search_ms", "rerank_ms", "generate_ms", "spent_usd",
    "x_cache", "cf_pop", "error",
]
LOG_FIELDS = ["request_id", "init_ms", "duration_ms", "billed_ms", "max_memory_mb", "connect_ms", "handler_total_ms",
              "log_cost_usd", "log_match"]
SUMMARY_FIELDS = [
    "pass", "questions", "status_200", "retried", "citation_hit_rate", "unanswerable_refused", "answerable_refused",
    "invalid_labels", "same_as_local", "client_ms_p50", "client_ms_p90", "client_ms_max", "lambda_ms_p50",
    "connect_ms_p50", "connect_ms_max", "search_ms_p50", "rerank_ms_p50", "generate_ms_p50", "cold_inits",
    "model_usd_total", "model_usd_per_query", "log_cost_usd_total", "lambda_usd_per_query", "api_usd_per_query",
    "aurora_usd_per_query", "run_at",
]
MATCH_BEFORE_S = 5  # a Lambda START may carry a timestamp this much before our send time (clock skew)
MATCH_AFTER_S = 15  # ... or this much after it (CloudFront, API Gateway, skew); questions are 21 s apart
RETRY_STATUSES = {0, 429, 503, 504}  # 0: no answer at all (timeout, connection error)


class DailyLimit(Exception):
    """The API's daily cap is used up, or the demo is switched off: nothing more can be asked now."""


def base_url(base: str) -> str:
    name = BASES[base]
    url = os.environ.get(name, "").rstrip("/")
    if not url:
        sys.exit(f"Set {name} in datasheet-rag\\.env first.")
    # The CloudFront console shows the domain name without a scheme.
    return url if "://" in url else "https://" + url


def pass_path(pass_: str) -> Path:
    return config.DEPLOYED_DIR / f"{pass_}.csv"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _epoch_ms(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp() * 1000


def send(url: str, question: str, opener=urllib.request.urlopen, clock=time.perf_counter) -> dict:
    """One POST /api/ask: status (0 when nothing came back), lower-case headers, parsed body (None when it is not
    JSON), body text and client ms. Never raises for an HTTP or network error."""
    body = json.dumps({"question": question, "document": None}).encode()
    request = urllib.request.Request(url + "/api/ask", data=body, method="POST",
                                     headers={"content-type": "application/json"})
    start = clock()
    try:
        with opener(request, timeout=config.DEPLOYED_TIMEOUT_S) as response:
            status, head, text = response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        status, head, text = error.code, error.headers, error.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as error:  # timeout, refused, DNS, a body cut off
        status, head, text = 0, {}, f"{type(error).__name__}: {error}"
    client_ms = (clock() - start) * 1000
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return {"status": status, "headers": {k.lower(): v for k, v in dict(head or {}).items()}, "body": parsed,
            "text": text, "client_ms": client_ms}


def retry_decision(status: int, body) -> str | None:
    """"stop" for the daily-limit 429 or the demo switched off, "retry" for throttling, a gateway 503/504 or no
    answer, else None."""
    if status == 429 and isinstance(body, dict) and body.get("limit") == "daily":
        return "stop"
    if status == 503 and isinstance(body, dict) and body.get("off"):
        return "stop"
    return "retry" if status in RETRY_STATUSES else None


def ask_with_retries(url: str, question: str, sender=send, sleep=time.sleep, now=_now_iso) -> dict:
    """Send until an answer that is not retried, or DEPLOYED_MAX_ATTEMPTS sends. Returns the last response plus
    `sent_at` (of the last send), `attempts`, `attempt_statuses` and `first_client_ms`. Raises DailyLimit."""
    statuses, first_ms = [], None
    for attempt in range(1, max(config.DEPLOYED_MAX_ATTEMPTS, 1) + 1):
        if attempt > 1:
            sleep(config.DEPLOYED_RETRY_WAIT_S)
        sent_at = now()
        response = sender(url, question)
        statuses.append(response["status"])
        first_ms = response["client_ms"] if first_ms is None else first_ms
        decision = retry_decision(response["status"], response["body"])
        if decision == "stop":
            raise DailyLimit(response["body"].get("error", "daily limit"))
        if decision is None:
            break
    return {**response, "sent_at": sent_at, "attempts": attempt, "attempt_statuses": statuses,
            "first_client_ms": first_ms}


def _cited(sources: list[dict]) -> str:
    return "; ".join(f"{source['document']}:{source['page']}" for source in sources)


def result_row(question: dict, pass_: str, response: dict) -> dict:
    """One row of runs/deployed/<pass>.csv from a question and the response of `ask_with_retries`."""
    body = response["body"] if isinstance(response["body"], dict) else {}
    ok = response["status"] == 200
    timings = body.get("timings_ms") or {}
    warnings = body.get("warnings") or []
    return {
        "id": question["id"], "type": question["type"], "pass": pass_, "sent_at": response["sent_at"],
        "status": response["status"], "attempts": response["attempts"],
        "attempt_statuses": "; ".join(str(status) for status in response["attempt_statuses"]),
        "first_client_ms": round(response["first_client_ms"], 1), "client_ms": round(response["client_ms"], 1),
        "answer": body.get("answer") if ok else None,
        "cited": _cited(body.get("sources") or []) if ok else None,
        "not_contained": body.get("not_contained") if ok else None,
        # The API reports invalid labels only as a warning (pipeline.warnings).
        "invalid": any("do not exist" in warning for warning in warnings) if ok else None,
        "warnings": " | ".join(warnings),
        "search_ms": timings.get("search"), "rerank_ms": timings.get("rerank"), "generate_ms": timings.get("generate"),
        "spent_usd": body.get("spent_usd"),
        "x_cache": response["headers"].get("x-cache"), "cf_pop": response["headers"].get("x-amz-cf-pop"),
        "error": None if ok else (body.get("error") or body.get("message") or response["text"][:200]),
    }


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def done_ids(path: Path) -> set[str]:
    """Ids already answered with a 200 in this pass's file."""
    return {row["id"] for row in _read_csv(path) if row.get("status") == "200"}


def latest_rows(path: Path) -> list[dict]:
    """The last row written for each id, in the order the ids first appear (a failed question re-run adds a row)."""
    latest = {}
    for row in _read_csv(path):
        latest[row["id"]] = row
    return list(latest.values())


def run_pass(url: str, questions_: list[dict], pass_: str, path: Path | None = None, sender=send,
             sleep=time.sleep, clock=time.monotonic, now=_now_iso) -> int:
    """Ask every question not yet answered in this pass, DEPLOYED_PACE_S apart. Returns the number asked."""
    path = pass_path(pass_) if path is None else path
    done = done_ids(path)
    todo = [question for question in questions_ if question["id"] not in done]
    if len(todo) < len(questions_):
        print(f"{len(questions_) - len(todo)} already answered in {path.name}, {len(todo)} to ask.")
    last = None
    for number, question in enumerate(todo, start=1):
        if last is not None:
            wait = config.DEPLOYED_PACE_S - (clock() - last)
            if wait > 0:
                sleep(wait)
        response = ask_with_retries(url, question["question"], sender, sleep, now)
        last = clock()  # paced from the last send's answer, so a retried question does not shorten the gap
        row = result_row(question, pass_, response)
        append_row(path, row)
        shown = f"cites {row['cited'] or '-'}" if row["status"] == 200 else row["error"]
        tries = f" ({row['attempts']} tries)" if row["attempts"] > 1 else ""
        print(f"{number:>2}/{len(todo)} {question['id']} {row['status']} in {row['client_ms'] / 1000:.1f} s{tries}: "
              f"{shown}", flush=True)
    return len(todo)


def run_cold(url: str, question: dict, trials: int, idle_s: float, path: Path | None = None, sender=send,
             sleep=time.sleep, now=_now_iso) -> list[dict]:
    """`trials` times: wait `idle_s` with no call, then ask one question. Rows are appended to cold.csv."""
    path = pass_path("cold") if path is None else path
    rows = []
    for trial in range(1, trials + 1):
        print(f"Trial {trial}/{trials}: waiting {idle_s / 60:.0f} minutes idle ...", flush=True)
        sleep(idle_s)
        row = result_row(question, "cold", ask_with_retries(url, question["question"], sender, sleep, now))
        append_row(path, row)
        rows.append(row)
        print(f"  {row['status']} in {row['client_ms'] / 1000:.1f} s (statuses {row['attempt_statuses']})",
              flush=True)
    return rows


# --- Dry run --------------------------------------------------------------------------------------------------


def _no_paid_call(text: str):
    raise RuntimeError("the plan must not embed; only cached question vectors are used")


def _local_tokens() -> tuple[float, float]:
    """Mean input and output tokens per answer in the local run of the frozen setup, for the answer cost."""
    rows = _read_csv(config.EVAL_DIR / f"{config.DEPLOYED_LOCAL_REFERENCE}.csv")
    inputs = [float(row["input_tokens"]) for row in rows if row.get("input_tokens")]
    outputs = [float(row["output_tokens"]) for row in rows if row.get("output_tokens")]
    return (sum(inputs) / len(inputs) if inputs else 5000.0), (sum(outputs) / len(outputs) if outputs else 100.0)


def answer_usd(answers: int) -> float:
    input_tokens, output_tokens = _local_tokens()
    return answers * (input_tokens / 1000 * config.GENERATE_USD_PER_1K_INPUT
                      + output_tokens / 1000 * config.GENERATE_USD_PER_1K_OUTPUT)


def plan_pass(conn, questions_: list[dict]) -> dict:
    """What a pass would pay for, read from Aurora with no paid call: embeds, rerank queries, answers."""
    texts = [question["question"] for question in questions_]
    uncached = set(search.uncached_questions(conn, texts))
    rerank_calls = 0
    for text in texts:
        if text in uncached:
            rerank_calls += 1
            continue
        hits = search.search(conn, text, config.SEARCH_MODE, embed_fn=_no_paid_call)
        rerank_calls += rerank.calls_needed(conn, text, hits)
    cost = {"embed": _embed_usd(sorted(uncached)), "rerank": rerank_calls * config.RERANK_USD_PER_QUERY,
            "generate": answer_usd(len(texts))}
    used = usage.count("asks")  # the deployed daily cap, kept in DynamoDB since step 7b
    return {"questions": len(texts), "embed_calls": len(uncached), "rerank_calls": rerank_calls,
            "generate_calls": len(texts), "cost": cost, "total": sum(cost.values()),
            "minutes": len(texts) * config.DEPLOYED_PACE_S / 60, "daily_asks": used,
            "daily_left": config.DEPLOYED_DAILY_LIMIT - used}


def print_plan(plan: dict) -> None:
    cost = plan["cost"]
    print(f"Questions:              {plan['questions']:>4}")
    print(f"Embed calls (Titan):    {plan['embed_calls']:>4}  ${cost['embed']:.4f}")
    print(f"Rerank calls (Cohere):  {plan['rerank_calls']:>4}  ${cost['rerank']:.4f}")
    print(f"Answers (Nova Lite):    {plan['generate_calls']:>4}  ${cost['generate']:.4f}  (local mean tokens)")
    print(f"Estimated cost:         ${plan['total']:.4f}")
    print(f"Estimated time:         {plan['minutes']:.0f} minutes ({config.DEPLOYED_PACE_S} s apart)")
    print(f"Daily cap used today:   {plan['daily_asks']} of {config.DEPLOYED_DAILY_LIMIT}")


# --- CloudWatch -----------------------------------------------------------------------------------------------

_DURATION = re.compile(r"\tDuration: ([\d.]+) ms")
_BILLED = re.compile(r"Billed Duration: ([\d.]+) ms")
_MEMORY = re.compile(r"Max Memory Used: (\d+) MB")
_INIT = re.compile(r"Init Duration: ([\d.]+) ms")
_REQUEST_ID = re.compile(r"RequestId: ([0-9a-f-]+)")


def parse_report(message: str) -> dict:
    """The numbers of a Lambda REPORT line; init_ms is None when the call was not a cold start."""
    def number(pattern):
        found = pattern.search(message)
        return float(found.group(1)) if found else None

    return {"duration_ms": number(_DURATION), "billed_ms": number(_BILLED), "max_memory_mb": number(_MEMORY),
            "init_ms": number(_INIT)}


def group_invocations(events: list[dict]) -> list[dict]:
    """One dict per Lambda request from CloudWatch events (logStreamName, timestamp, message): request id, START
    time in epoch ms, the REPORT numbers, our handler's JSON line and the sum of its `{"cost"}` lines. A container
    runs one request at a time, so every line between START and REPORT in a stream belongs to that request."""
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1]["logStreamName"], pair[1]["timestamp"], pair[0]))
    current, invocations = {}, []
    for _, event in ordered:
        stream, message = event["logStreamName"], event["message"].strip()
        if message.startswith("START RequestId:"):
            found = _REQUEST_ID.search(message)
            current[stream] = {"request_id": found.group(1) if found else None, "start_ms": event["timestamp"],
                               "handler": None, "cost_usd": 0.0, "costs": 0}
            invocations.append(current[stream])
            continue
        invocation = current.get(stream)
        if invocation is None:
            continue
        if message.startswith("REPORT RequestId:"):
            invocation.update(parse_report(event["message"]))
            current.pop(stream)
            continue
        if not message.startswith("{"):
            continue
        try:
            line = json.loads(message)
        except json.JSONDecodeError:
            continue
        if isinstance(line, dict) and "cost" in line:
            invocation["cost_usd"] += float(line["cost"].get("est_cost_usd") or 0)
            invocation["costs"] += 1
        elif isinstance(line, dict) and "status" in line:
            invocation["handler"] = line
    return invocations


def _same_timings(row: dict, handler: dict) -> bool:
    logged = handler.get("timings_ms") or {}
    for key in ("search", "rerank", "generate"):
        ours, theirs = row.get(f"{key}_ms"), logged.get(key)
        if ours in (None, "") and theirs is None:
            continue
        if ours in (None, "") or theirs is None or abs(float(ours) - float(theirs)) > 0.01:
            return False
    return True


def match_rows(rows: list[dict], invocations: list[dict]) -> list[dict]:
    """Each row with the LOG_FIELDS of the /api/ask request it sent: the first unused one whose START is in
    [sent - MATCH_BEFORE_S, sent + MATCH_AFTER_S]. log_match is "ok", "timings differ" (the handler logged other
    timings than the answer carried) or "none"."""
    asks = sorted((inv for inv in invocations if (inv.get("handler") or {}).get("path") == "/api/ask"),
                  key=lambda inv: inv["start_ms"])
    used, matched = set(), []
    for row in rows:
        sent = _epoch_ms(row["sent_at"])
        found = next((inv for inv in asks
                      if id(inv) not in used
                      and sent - MATCH_BEFORE_S * 1000 <= inv["start_ms"] <= sent + MATCH_AFTER_S * 1000), None)
        if found is None:
            matched.append({**row, **dict.fromkeys(LOG_FIELDS), "log_match": "none"})
            continue
        used.add(id(found))
        handler = found["handler"]
        matched.append({
            **row, "request_id": found["request_id"], "init_ms": found.get("init_ms"),
            "duration_ms": found.get("duration_ms"), "billed_ms": found.get("billed_ms"),
            "max_memory_mb": found.get("max_memory_mb"), "connect_ms": handler.get("connect_ms"),
            "handler_total_ms": handler.get("total_ms"), "log_cost_usd": round(found["cost_usd"], 8),
            "log_match": "ok" if str(row.get("status")) != "200" or _same_timings(row, handler) else "timings differ",
        })
    return matched


def fetch_events(client, start_ms: int, end_ms: int, group: str | None = None) -> list[dict]:
    group = group or f"/aws/lambda/{config.LAMBDA_FUNCTION}"
    events, token = [], None
    while True:
        kwargs = {"logGroupName": group, "startTime": int(start_ms), "endTime": int(end_ms)}
        if token:
            kwargs["nextToken"] = token
        page = client.filter_log_events(**kwargs)
        events.extend(page.get("events", []))
        token = page.get("nextToken")
        if not token:
            return events


def join_logs(path: Path, client) -> list[dict]:
    """Rewrite a pass file with the CloudWatch numbers of each row's request."""
    rows = _read_csv(path)
    if not rows:
        return []
    sent = [_epoch_ms(row["sent_at"]) for row in rows]
    events = fetch_events(client, min(sent) - 60_000, max(sent) + 120_000)
    matched = match_rows(rows, group_invocations(events))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS + LOG_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(matched)
    return matched


# --- Summary --------------------------------------------------------------------------------------------------


def percentile(values, q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..100) of the values that are not None or empty."""
    values = sorted(float(value) for value in values if value not in (None, ""))
    if not values:
        return None
    position = (len(values) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def _flag(value) -> bool:
    return str(value).strip().lower() == "true"


def _has_invalid(value) -> bool:
    """A deployed row stores True / False; the local CSV stores the invalid labels themselves ("" or "11; 12")."""
    text = str(value or "").strip()
    return text.lower() not in ("", "false", "none", "[]")


def _answer(row: dict) -> dict:
    """A row as the answer dict of metrics.py: not_contained and sources (document, page)."""
    sources = []
    for item in (row.get("cited") or "").split(";"):
        document, _, page = item.strip().rpartition(":")
        if document and page.isdigit():
            sources.append({"document": document, "page": int(page)})
    return {"not_contained": _flag(row.get("not_contained")), "sources": sources}


def same_answer(row: dict, local: dict) -> bool:
    """True when both refused, or neither did and they cite the same set of pages."""
    ours, theirs = _answer(row), _answer(local)
    if ours["not_contained"] or theirs["not_contained"]:
        return ours["not_contained"] == theirs["not_contained"]
    return ({(s["document"], s["page"]) for s in ours["sources"]}
            == {(s["document"], s["page"]) for s in theirs["sources"]})


def lambda_usd(billed_ms) -> float:
    return float(billed_ms) / 1000 * config.LAMBDA_MEMORY_MB / 1024 * config.LAMBDA_USD_PER_GB_S \
        + config.LAMBDA_USD_PER_REQUEST


def _numbers(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) not in (None, "")]


def summary_row(pass_: str, rows: list[dict], truths_by_id: dict, local_by_id: dict,
                aurora_usd_per_query: float | None) -> dict:
    answered = [row for row in rows if str(row["status"]) == "200"]
    truths = [truths_by_id[row["id"]] for row in answered]
    answers = [_answer(row) for row in answered]
    refusals = metrics.refusal_rates(answers, truths) if answered else {}
    compared = [row for row in answered if row["id"] in local_by_id]
    spent = _numbers(answered, "spent_usd")
    billed = _numbers(rows, "billed_ms")
    return {
        "pass": pass_, "questions": len(rows), "status_200": len(answered),
        "retried": sum(1 for row in rows if int(row.get("attempts") or 1) > 1),
        "citation_hit_rate": metrics.citation_hit_rate(answers, truths) if answered else None,
        "unanswerable_refused": refusals.get("unanswerable_refused"),
        "answerable_refused": refusals.get("answerable_refused"),
        "invalid_labels": sum(1 for row in answered if _has_invalid(row.get("invalid"))),
        "same_as_local": (f"{sum(same_answer(row, local_by_id[row['id']]) for row in compared)}/{len(compared)}"
                          if compared else None),
        "client_ms_p50": percentile(_numbers(rows, "client_ms"), 50),
        "client_ms_p90": percentile(_numbers(rows, "client_ms"), 90),
        "client_ms_max": percentile(_numbers(rows, "client_ms"), 100),
        "lambda_ms_p50": percentile(_numbers(rows, "duration_ms"), 50),
        "connect_ms_p50": percentile(_numbers(rows, "connect_ms"), 50),
        "connect_ms_max": percentile(_numbers(rows, "connect_ms"), 100),
        "search_ms_p50": _median(_numbers(answered, "search_ms")),
        "rerank_ms_p50": _median(_numbers(answered, "rerank_ms")),
        "generate_ms_p50": _median(_numbers(answered, "generate_ms")),
        "cold_inits": sum(1 for row in rows if row.get("init_ms") not in (None, "")),
        "model_usd_total": sum(spent),
        "model_usd_per_query": sum(spent) / len(spent) if spent else None,
        "log_cost_usd_total": sum(_numbers(rows, "log_cost_usd")) if any(row.get("request_id") for row in rows)
        else None,
        "lambda_usd_per_query": sum(lambda_usd(ms) for ms in billed) / len(billed) if billed else None,
        "api_usd_per_query": config.HTTP_API_USD_PER_REQUEST,
        "aurora_usd_per_query": aurora_usd_per_query,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def local_row(questions_: list[dict]) -> dict:
    """The local run of the frozen setup scored the same way, for the comparison line."""
    rows = _read_csv(config.EVAL_DIR / f"{config.DEPLOYED_LOCAL_REFERENCE}.csv")
    truths_by_id = {question["id"]: question["sources"] for question in questions_}
    rows = [{**row, "status": "200"} for row in rows if row["id"] in truths_by_id]
    row = summary_row(f"local:{config.DEPLOYED_LOCAL_REFERENCE}", rows, truths_by_id, {}, None)
    for key in ("client_ms_p50", "client_ms_p90", "client_ms_max", "model_usd_total", "model_usd_per_query",
                "api_usd_per_query"):
        row[key] = None
    return row


def summarise(questions_: list[dict], aurora_acu_hours: float | None) -> list[dict]:
    truths_by_id = {question["id"]: question["sources"] for question in questions_}
    local_by_id = {row["id"]: row for row in _read_csv(config.EVAL_DIR / f"{config.DEPLOYED_LOCAL_REFERENCE}.csv")}
    # Every cold trial asks the same question, so each of its rows counts; a pass keeps the last row per question.
    by_pass = {pass_: latest_rows(pass_path(pass_)) for pass_ in PASSES}
    by_pass["cold"] = _read_csv(pass_path("cold"))
    total = sum(len(rows) for rows in by_pass.values())
    aurora = (aurora_acu_hours * config.AURORA_USD_PER_ACU_HOUR / total
              if aurora_acu_hours is not None and total else None)
    summary = [summary_row(pass_, rows, truths_by_id, local_by_id, aurora) for pass_, rows in by_pass.items() if rows]
    return [local_row(questions_), *summary]


def _shown(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6f}" if abs(value) < 0.01 and value else f"{value:.3f}" if value < 10 else f"{value:,.0f}"
    return str(value)


# --- CLI ------------------------------------------------------------------------------------------------------


def _confirm(args, text: str) -> bool:
    return args.yes or input(f"\n{text} [y/N] ").strip().lower() == "y"


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Evaluate the deployed pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="free (wakes Aurora): what a pass would pay for")
    run = sub.add_parser("run", help="paid: one pass over the questions")
    for each in (plan, run):
        each.add_argument("--pass", dest="pass_", choices=PASSES, default="first")
    run.add_argument("--base", choices=list(BASES), default="site", help="site: CloudFront (default); api: API Gateway")
    run.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    cold = sub.add_parser("cold", help="paid: idle waits, then one cached question per trial")
    cold.add_argument("--trials", type=int, default=3)
    cold.add_argument("--idle-minutes", type=float, default=10)
    cold.add_argument("--base", choices=list(BASES), default="site")
    cold.add_argument("--yes", action="store_true")
    sub.add_parser("logs", help="free: join Lambda's CloudWatch lines to every pass file")
    summary = sub.add_parser("summary", help=f"free: write {config.RESULTS_DEPLOYED.name}")
    summary.add_argument("--aurora-acu-hours", type=float, help="ACU-hours read in the RDS console for the runs")
    args = parser.parse_args(argv)
    questions_ = questions.load_questions()

    if args.command in ("plan", "run"):
        done = set() if args.command == "plan" else done_ids(pass_path(args.pass_))
        todo = [q for q in questions_ if q["id"] not in done]
        with db.connect(target="aurora") as conn:
            plan_ = plan_pass(conn, todo)
        print_plan(plan_)
        if args.command == "plan":
            print("\nNothing was called.")
            return
        if len(todo) > plan_["daily_left"]:
            sys.exit(f"Only {plan_['daily_left']} questions are left in today's cap; run again after 00:00 UTC.")
        if not todo or not _confirm(args, "Run?"):
            print("Nothing to ask." if not todo else "Stopped. Nothing was called.")
            return
        try:
            run_pass(base_url(args.base), questions_, args.pass_)
        except DailyLimit as error:
            sys.exit(f"Stopped: {error}")
        print(f"Rows: {pass_path(args.pass_)}. Next: python -m rag.eval.deployed_eval logs")
    elif args.command == "cold":
        # No plan from Aurora here: reading it would wake the cluster the trial wants paused.
        question = next(q for q in questions_ if q["id"] == COLD_QUESTION)
        print(f"{args.trials} trials of {question['id']} (cached), {args.idle_minutes:.0f} minutes idle before each: "
              f"about ${answer_usd(args.trials):.4f}, {args.trials * args.idle_minutes:.0f} minutes. "
              "Do not use the site meanwhile.")
        if not _confirm(args, "Run?"):
            print("Stopped. Nothing was called.")
            return
        try:
            run_cold(base_url(args.base), question, args.trials, args.idle_minutes * 60)
        except DailyLimit as error:
            sys.exit(f"Stopped: {error}")
    elif args.command == "logs":
        import boto3

        client = boto3.client("logs", region_name=config.AWS_REGION)
        for pass_ in (*PASSES, "cold"):
            if pass_path(pass_).exists():
                rows = join_logs(pass_path(pass_), client)
                counts = {}
                for row in rows:
                    counts[row["log_match"]] = counts.get(row["log_match"], 0) + 1
                print(f"{pass_}: {len(rows)} rows, matches {counts}")
    else:
        rows = summarise(questions_, args.aurora_acu_hours)
        config.RESULTS_DEPLOYED.parent.mkdir(parents=True, exist_ok=True)
        with config.RESULTS_DEPLOYED.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        for field in SUMMARY_FIELDS[1:-1]:
            print(f"{field:<22}" + "".join(f"{_shown(row[field]):>22}" for row in rows))
        print(f"{'':<22}" + "".join(f"{row['pass']:>22}" for row in rows))
        for row in rows:
            if row["retried"]:
                # Only the answered send is costed and timed: an earlier 504 may still have run and paid in Lambda.
                print(f"WARNING: {row['retried']} questions of {row['pass']} were retried; the spend and wait of "
                      "their earlier sends are not counted. Check the Lambda log for them.")
        print(f"\nWritten: {config.RESULTS_DEPLOYED}")


if __name__ == "__main__":
    main(sys.argv[1:])
