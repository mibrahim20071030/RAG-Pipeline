"""Uploads on the deployed site, both pipelines (Cycle 2 step 9c), with data/upload_cases.jsonl.

One PDF is uploaded through SITE_URL as the page does it (a place from /api/uploads, the file to S3 with the
presigned POST), and followed until both pipelines have it. Then each case is asked on both pipelines:
    answer   asked about the upload: passes when the answer has every `must_contain` value and cites one of
             `pages` of the upload
    refuse   passes when the answer is the refusal: an LM35 question asked about the upload (the upload does not
             have it, and no reviewer's note may reach an upload), or an upload question asked without `upload_id`
             (an upload is never in the curated search)
A curated answer (no `upload_id`) also gets its notes read from its record, which must be none; a record that
cannot be read fails the case (it was not checked).

Run from datasheet-rag, with SITE_URL in .env and the PDF at hand:
    python -m rag.eval.upload_eval run ..\\agentic-ai-foundations.pdf      prints the plan, asks, then runs
    python -m rag.eval.upload_eval rescore      free: the saved answers under the current refusal rule

Rows are appended to runs/deployed/uploads.csv as the answers arrive, with the run's upload_id, so a second run
adds to the first. Asks are retried as `deployed_eval` does (429, 503, 504, no answer) and the run stops at the daily
cap. Costs one of the UPLOAD_DAILY_LIMIT uploads (its Titan embeds, about $0.0004 for 59 passages) and 2 asks per
case.
"""
import argparse
import csv
import functools
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from rag import config, uploads
from rag.eval import deployed_eval
from rag.eval.correction_eval import contains_all

CASES = config.ROOT / "data" / "upload_cases.jsonl"
OUT = config.DEPLOYED_DIR / "uploads.csv"
KINDS = ("answer", "refuse")
# Rough cost per ask, for the plan: a question about an upload is always new to our reranker.
OURS_USD, KB_USD, UPLOAD_USD = 0.0022, 0.0012, 0.0004
WAIT_S, WAIT_MAX_S = 10, 900
ROW_FIELDS = ["upload_id", "id", "kind", "pipeline", "upload", "status", "attempts", "passed", "refused", "noted",
              "cited", "notes_used", "client_ms", "spent_usd", "answer"]


def load_cases(path: Path | str | None = None) -> list[dict]:
    """The cases in file order. Raises ValueError for a case that is missing a field or has an unknown kind."""
    cases = []
    for number, line in enumerate(Path(path or CASES).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        missing = {"id", "kind", "upload", "question", "must_contain", "pages"} - set(case)
        if missing or case["kind"] not in KINDS:
            raise ValueError(f"line {number}: missing {sorted(missing)} or unknown kind {case.get('kind')!r}")
        if case["kind"] == "answer" and not case["pages"]:
            raise ValueError(f"line {number}: an answer case needs the pages it may cite")
        cases.append(case)
    return cases


def score(case: dict, body: dict, upload_id: str | None, notes: str | None) -> dict:
    """Whether one answer passes its case. `notes` is what the answer's record says it was given: "" for none, None
    when it could not be read (a curated answer then fails: the isolation was not checked); for an answer about an
    upload, which has no record, pass ""."""
    document = f"upload-{upload_id}" if upload_id else None
    cited = [(s.get("document"), s.get("page")) for s in body.get("sources") or []]
    refused = bool(body.get("not_contained"))
    if notes is None:
        return {"passed": False, "refused": refused, "noted": None,
                "cited": "; ".join(f"{doc}:{page}" for doc, page in cited)}
    noted = any(s.get("note") for s in body.get("sources") or []) or bool(notes)
    if case["kind"] == "refuse":
        passed = refused and not noted
    else:
        cites_page = any(doc == document and page in case["pages"] for doc, page in cited)
        passed = (not refused and cites_page and contains_all(body.get("answer") or "", case["must_contain"])
                  and not noted)
    return {"passed": passed, "refused": refused, "noted": noted,
            "cited": "; ".join(f"{doc}:{page}" for doc, page in cited)}


def plan(cases: list[dict]) -> dict:
    asks = len(cases)
    cost = asks * OURS_USD + asks * KB_USD + UPLOAD_USD
    return {"uploads": 1, "asks_ours": asks, "asks_kb": asks, "cost": cost,
            "minutes": (2 * asks * config.DEPLOYED_PACE_S) / 60 + 6}


# ---------------------------------------------------------------- the upload, as the page makes it

def _request(url: str, method: str, body: bytes | None = None, headers: dict | None = None,
             opener=urllib.request.urlopen) -> tuple[int, str]:
    """Status and body text; status 0 when nothing came back (timeout, reset, DNS). Never raises for those."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with opener(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as error:
        return 0, f"{type(error).__name__}: {error}"


def upload(site: str, pdf: Path, request=_request) -> str:
    """Upload the PDF; its upload_id. Raises RuntimeError when the site or S3 refuses."""
    data = pdf.read_bytes()
    status, text = request(site + "/api/uploads", "POST", json.dumps({"filename": pdf.name, "size": len(data)}).encode(),
                           {"content-type": "application/json"})
    if status != 200:
        raise RuntimeError(f"/api/uploads answered {status}: {text[:300]}")
    place = json.loads(text)
    body, content_type = uploads.multipart(place["post"]["fields"], data, pdf.name)
    status, text = request(place["post"]["url"], "POST", body, {"content-type": content_type})
    if status not in (200, 201, 204):
        raise RuntimeError(f"S3 answered {status}: {text[:300]}")
    return place["upload_id"]


def wait_ready(site: str, upload_id: str, request=_request, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Poll the upload's record until both pipelines have it; the last record. Raises RuntimeError when the upload
    or its KB side failed, when the poll is refused (an unknown or expired id, a server error), or after
    WAIT_MAX_S. A poll with no answer at all (status 0) is tried again."""
    start = clock()
    while True:
        sleep(WAIT_S)
        status, text = request(f"{site}/api/uploads/{upload_id}", "GET")
        if status not in (0, 200):
            raise RuntimeError(f"The upload's record answered {status}: {text[:200]}")
        record = json.loads(text) if status == 200 and text.startswith("{") else {}
        print(f"  {time.strftime('%H:%M:%S')}  status {record.get('status')}, ours {record.get('ours')}, "
              f"kb {record.get('kb')}, {record.get('embedded')} of {record.get('chunks')} embedded", flush=True)
        if record.get("status") == "failed":
            raise RuntimeError(f"The upload failed: {record.get('error')}")
        if uploads.settled(record):
            if record.get("kb") != "ready":
                raise RuntimeError(f"Ours is ready but the KB side is {record.get('kb')}: the KB cases cannot run.")
            return record
        if clock() - start > WAIT_MAX_S:
            raise RuntimeError(f"Not ready after {WAIT_MAX_S // 60} minutes: {record}")


# ---------------------------------------------------------------- the run

def append_row(row: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def run(site: str, pdf: Path | None, cases: list[dict], sender=deployed_eval.send,
        notes_fn=deployed_eval.notes_used, sleep=time.sleep, request=_request, path: Path = OUT,
        upload_id: str | None = None) -> list[dict]:
    """Upload (or, with `upload_id`, reuse an upload made earlier), wait, then ask every case on both pipelines;
    each row is appended to `path` as it arrives. Raises deployed_eval.DailyLimit at the daily cap (the rows so far
    are kept)."""
    upload_id = upload_id or upload(site, pdf, request)
    print(f"upload_id {upload_id}; waiting for both pipelines ...", flush=True)
    wait_ready(site, upload_id, request, sleep)
    rows = []
    for pipeline in config.PIPELINES:
        for case in cases:
            if rows:
                sleep(config.DEPLOYED_PACE_S)
            asked_id = upload_id if case["upload"] else None
            ask = functools.partial(sender, pipeline=pipeline, upload_id=asked_id)
            response = deployed_eval.ask_with_retries(site, case["question"], ask, sleep)
            body = response["body"] if isinstance(response["body"], dict) else {}
            ok = response["status"] == 200
            # An answer about an upload has no record and is never given a note (only curated answers are).
            notes = (notes_fn(body.get("answer_id")) if not asked_id else "") if ok else None
            result = score(case, body, asked_id, notes) if ok else \
                {"passed": False, "refused": None, "noted": None, "cited": ""}
            row = {"upload_id": upload_id, "id": case["id"], "kind": case["kind"], "pipeline": pipeline,
                   "upload": case["upload"], "status": response["status"], "attempts": response["attempts"],
                   **result, "notes_used": "(unread)" if ok and notes is None else (notes or ""),
                   "client_ms": round(response["client_ms"], 1), "spent_usd": body.get("spent_usd"),
                   "answer": body.get("answer") or body.get("error") or body.get("message")}
            append_row(row, path)
            rows.append(row)
            print(f"  {pipeline:4} {case['id']} {response['status']}  {'pass' if row['passed'] else 'FAIL'}  "
                  f"cites {row['cited'] or '-'}", flush=True)
    return rows


def rescore(path: Path = OUT) -> list[str]:
    """Re-check the saved refusal cases with the current `generate.is_not_contained` (no call); rewrite the file.
    Returns the "<pipeline> <id>" of each row whose result changed."""
    from rag import generate

    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    changed = []
    for row in rows:
        if row["kind"] != "refuse" or row["status"] != "200":
            continue
        sources = [{"document": doc, "page": page} for doc, _, page in
                   (item.strip().rpartition(":") for item in (row["cited"] or "").split(";")) if doc]
        refused = generate.is_not_contained(row["answer"] or "", sources)
        passed = refused and row["noted"] == "False" and row["notes_used"] != "(unread)"
        if (str(refused), str(passed)) != (row["refused"], row["passed"]):
            row["refused"], row["passed"] = str(refused), str(passed)
            changed.append(f"{row['pipeline']} {row['id']}")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return changed


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Uploads on the deployed site, both pipelines.")
    commands = parser.add_subparsers(dest="command", required=True)
    go = commands.add_parser("run", help="paid: one upload, then every case on both pipelines")
    go.add_argument("pdf", type=Path)
    go.add_argument("--upload-id", help="reuse this upload (made by an earlier run, not expired) instead of uploading")
    go.add_argument("--yes", action="store_true")
    commands.add_parser("rescore", help="free: the saved answers under the current refusal rule")
    args = parser.parse_args(argv)
    if args.command == "rescore":
        changed = rescore()
        print(f"Changed: {', '.join(changed) or 'none'}. Rows: {OUT}")
        return
    cases = load_cases()
    p = plan(cases)
    made = f"the upload {args.upload_id}" if args.upload_id else f"1 upload ({args.pdf.name})"
    print(f"{made}, {p['asks_ours']} asks on ours and {p['asks_kb']} on the KB. "
          f"About ${p['cost']:.3f}, about {p['minutes']:.0f} minutes.")
    if not args.upload_id and not args.pdf.exists():
        sys.exit(f"No such file: {args.pdf}")
    if not (args.yes or input("\nRun? [y/N] ").strip().lower() == "y"):
        print("Stopped. Nothing was called.")
        return
    try:
        rows = run(deployed_eval.base_url("site"), args.pdf, cases, upload_id=args.upload_id)
    except deployed_eval.DailyLimit as error:
        sys.exit(f"Stopped: {error}. The rows so far are in {OUT}.")
    for pipeline in config.PIPELINES:
        part = [row for row in rows if row["pipeline"] == pipeline]
        print(f"{pipeline:4} passed {sum(row['passed'] for row in part)}/{len(part)}")
    spent = sum(float(row["spent_usd"]) for row in rows if row["spent_usd"] not in (None, ""))
    print(f"Asks spent ${spent:.4f} (the upload's embeds are not in that). Rows: {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
