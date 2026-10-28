"""Call the deployed API (API Gateway HTTP API in front of the Lambda function) the way a browser would.

Run from datasheet-rag, with API_URL (the invoke URL, no trailing path) in .env or the environment. With
--base site, every command goes to SITE_URL instead (the CloudFront address, which passes /api/* on to the API):
    python scripts\\call_api.py ask "What is ...?" --yes       paid: about $0.0023 new, $0.0003 cached
    python scripts\\call_api.py ask "..." --pipeline kb --yes  paid: the Knowledge Base pipeline, about $0.0012
    python scripts\\call_api.py limits                         free: refused requests, none of which may wake Aurora
    python scripts\\call_api.py throttle --route ask --count 6 free: empty questions at once (Lambda answers 400)
    python scripts\\call_api.py usage                          free: today's count of the daily cap, read from DynamoDB
    python scripts\\call_api.py --base site page               free: the page itself, and http:// sent on to https://
    python scripts\\call_api.py upload <file.pdf> --yes         paid: one upload (Titan, about $0.001 for 100 chunks)
    python scripts\\call_api.py upload-status <id>              free: the upload's record
    python scripts\\call_api.py ask "..." --upload <id> --yes   paid: a question about that upload

No AWS key is used except by `usage`, which reads the DynamoDB table USAGE_TABLE with the dev user's key.
"""
import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import config, uploads, usage  # config also reads .env

JSON = {"content-type": "application/json"}
BASES = {"api": ("API_URL", "the API's invoke URL"), "site": ("SITE_URL", "the CloudFront address")}
base = "api"  # set by --base


def base_url() -> str:
    name, what = BASES[base]
    url = os.environ.get(name, "").rstrip("/")
    if not url:
        sys.exit(f"Set {name} ({what}) in datasheet-rag\\.env first.")
    # The CloudFront console shows the domain name without a scheme.
    return url if "://" in url else "https://" + url


def call(method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict, str, float]:
    """Status, response headers (lower-case names), body text and seconds taken. Never raises for an HTTP error."""
    request = urllib.request.Request(base_url() + path, data=body, method=method, headers=headers or {})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            status, head, text = response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        status, head, text = error.code, error.headers, error.read().decode("utf-8", "replace")
    return status, {k.lower(): v for k, v in head.items()}, text, time.perf_counter() - start


def show(status: int, text: str, seconds: float) -> None:
    print(f"Status {status} in {seconds:.2f} s")
    try:
        print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        print(text)


def ask_body(question: str, document: str | None = None, pipeline: str | None = None,
             upload_id: str | None = None) -> bytes:
    body = {"question": question, "document": document}
    if pipeline:
        body["pipeline"] = pipeline
    if upload_id:
        body["upload_id"] = upload_id
    return json.dumps(body).encode()


def upload_body(filename: str, size: int) -> bytes:
    return json.dumps({"filename": filename, "size": size}).encode()


def cmd_upload(path: Path, wait: bool) -> None:
    """One upload as the page makes it: a place from /api/uploads, the file to S3, then the record until ready."""
    data = path.read_bytes()
    status, _, text, seconds = call("POST", "/api/uploads", upload_body(path.name, len(data)), JSON)
    show(status, text, seconds)
    if status != 200:
        return
    place = json.loads(text)
    if "post" not in place:
        sys.exit("The answer has no presigned POST (a local server wants a PUT instead).")
    body, content_type = uploads.multipart(place["post"]["fields"], data, path.name)
    request = urllib.request.Request(place["post"]["url"], data=body, method="POST",
                                     headers={"content-type": content_type})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(f"S3 POST -> {response.status} in {time.perf_counter() - start:.2f} s")
    except urllib.error.HTTPError as error:
        sys.exit(f"S3 POST -> {error.code}: {error.read().decode('utf-8', 'replace')[:400]}")
    print(f"upload_id {place['upload_id']}")
    while wait:
        time.sleep(10)
        status, _, text, _ = call("GET", f"/api/uploads/{place['upload_id']}")
        record = json.loads(text) if text.startswith("{") else {}
        print(f"  {time.strftime('%H:%M:%S')}  {status}  status {record.get('status')}, ours {record.get('ours')}, "
              f"kb {record.get('kb')}, {record.get('embedded')} of {record.get('chunks')} embedded"
              f"{', ' + record['error'] if record.get('error') else ''}", flush=True)
        if status != 200 or record.get("status") == "failed" or uploads.settled(record):
            break


def cmd_limits() -> None:
    checks = [
        ("unknown route GET /api/nothing", "GET", "/api/nothing", None, {}),
        ("removed route GET /api/documents", "GET", "/api/documents", None, {}),
        ("empty question", "POST", "/api/ask", ask_body(""), JSON),
        ("unknown datasheet", "POST", "/api/ask", ask_body("What is the supply voltage?", "lm358"), JSON),
        ("unknown pipeline", "POST", "/api/ask", ask_body("What is the supply voltage?", None, "bedrock"), JSON),
        ("empty question on the KB pipeline", "POST", "/api/ask", ask_body("", None, "kb"), JSON),
        ("wrong method GET /api/ask", "GET", "/api/ask", None, {}),
        (f"question of {config.API_MAX_QUESTION_CHARS + 1} characters", "POST", "/api/ask",
         ask_body("x" * (config.API_MAX_QUESTION_CHARS + 1)), JSON),
        ("20 KB body", "POST", "/api/ask", ask_body("x" * 20_000), JSON),
        ("body that is not JSON", "POST", "/api/ask", b"question=hi", {"content-type": "application/x-www-form-urlencoded"}),
        ("upload of a .txt name", "POST", "/api/uploads", upload_body("notes.txt", 1000), JSON),
        ("upload of 11 MB", "POST", "/api/uploads", upload_body("big.pdf", 11 * 1024 * 1024), JSON),
        ("unknown upload GET /api/uploads/<id>", "GET", "/api/uploads/0123456789abcdef", None, {}),
        ("malformed upload id", "GET", "/api/uploads/not-an-id", None, {}),
        ("ask about an unknown upload", "POST", "/api/ask",
         ask_body("What is the supply voltage?", upload_id="0123456789abcdef"), JSON),
    ]
    for name, method, path, body, headers in checks:
        status, _, text, seconds = call(method, path, body, headers)
        print(f"{name}: {status} in {seconds:.2f} s  {text[:120]}")


def cmd_throttle(route: str, count: int) -> None:
    job = lambda: call("POST", "/api/ask", ask_body(""), JSON)  # an empty question: Lambda answers 400, free
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(lambda _: job(), range(count)))
    print(f"{count} requests to {route} at once: "
          + ", ".join(f"{n} x {status}" for status, n in sorted(Counter(r[0] for r in results).items())))
    for status, _, text, _ in results:
        if status == 429:
            print(f"  429 body: {text[:120]}")
            break


def cmd_page() -> None:
    """The page from the site: 200, HTML and its title; then the same address over http, which must be redirected."""
    status, head, text, seconds = call("GET", "/")
    title = "<title>Datasheet Q&amp;A</title>" in text
    print(f"GET / -> {status} in {seconds:.2f} s, {head.get('content-type')}, title found: {title}, "
          f"cache: {head.get('x-cache')}")
    url = urllib.parse.urlsplit(base_url())
    plain = http.client.HTTPConnection(url.hostname, 80, timeout=20)
    try:
        plain.request("GET", "/")
        response = plain.getresponse()
        print(f"http:// GET / -> {response.status}, Location {response.getheader('Location')}")
    except OSError as error:
        print(f"http:// GET / -> no answer ({type(error).__name__}: {error})")
    finally:
        plain.close()


def at_least_one(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    global base
    parser = argparse.ArgumentParser(description="Call the deployed API.")
    parser.add_argument("--base", choices=list(BASES), default="api",
                        help="api: API_URL, the API directly (default); site: SITE_URL, through CloudFront")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--document", choices=list(config.DOCUMENTS))
    ask.add_argument("--pipeline", choices=list(config.PIPELINES), default="ours")
    ask.add_argument("--upload", help="ask about this upload id")
    ask.add_argument("--yes", action="store_true", help="make the paid calls without stopping first")
    up = sub.add_parser("upload")
    up.add_argument("pdf", type=Path)
    up.add_argument("--no-wait", action="store_true", help="do not follow the record until it is ready")
    up.add_argument("--yes", action="store_true", help="make the paid calls without stopping first")
    up_status = sub.add_parser("upload-status")
    up_status.add_argument("upload_id")
    sub.add_parser("limits")
    throttle = sub.add_parser("throttle")
    throttle.add_argument("--route", choices=["ask"], required=True)
    throttle.add_argument("--count", type=at_least_one, default=6)
    sub.add_parser("usage")
    sub.add_parser("page")
    args = parser.parse_args(argv)
    base = args.base
    if args.command == "page" and base != "site":
        sys.exit("page checks the CloudFront site: run it with --base site.")

    if args.command == "ask":
        if not args.yes:
            sys.exit("This makes paid Bedrock calls: about $0.0023 for a new question, $0.0003 for a cached one, "
                     "$0.0012 on the Knowledge Base pipeline. Run again with --yes.")
        status, _, text, seconds = call("POST", "/api/ask", ask_body(args.question, args.document, args.pipeline,
                                                                     args.upload), JSON)
        show(status, text, seconds)
    elif args.command == "upload":
        if not args.yes:
            sys.exit("This makes paid Titan calls (about $0.0002 per 20 passages) and uses one of the day's "
                     f"{config.UPLOAD_DAILY_LIMIT or 5} uploads. Run again with --yes.")
        cmd_upload(args.pdf, wait=not args.no_wait)
    elif args.command == "upload-status":
        status, _, text, seconds = call("GET", f"/api/uploads/{args.upload_id}")
        show(status, text, seconds)
    elif args.command == "limits":
        cmd_limits()
    elif args.command == "throttle":
        cmd_throttle(args.route, args.count)
    elif args.command == "page":
        cmd_page()
    else:
        print(f"Questions counted today (UTC): {usage.count('asks')} of {config.DEPLOYED_DAILY_LIMIT} (ours), "
              f"{usage.count('kb_asks')} of {config.DEPLOYED_KB_DAILY_LIMIT} (Knowledge Base)")


if __name__ == "__main__":
    main(sys.argv[1:])
