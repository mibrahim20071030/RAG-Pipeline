"""The local web UI: serves web/index.html, the JSON API of `rag.api`, and the datasheet PDFs.

Run from datasheet-rag:  python -m rag.server            then open http://127.0.0.1:8000
                         python -m rag.server --port 8080

Standard library only. It listens on 127.0.0.1, so only this machine can reach it: every question it answers
is paid for on the AWS account. One request at a time, on one database connection that is opened again after
Postgres restarts.

Uploads (step 9a): POST /api/uploads makes a place, the page then PUTs the PDF to /api/uploads/<id>/file, and the
upload is ingested in a background thread on its own connection, for our pipeline only (the Knowledge Base is in the
cloud). Records are kept in memory, so they go when the server stops; the stored chunks expire after 24 hours.

Feedback (step 9b): answers get an id, POST /api/feedback rates them, and /review.html is the reviewer's page, with
no login here (only this machine can reach the server). Answers and ratings are kept in memory; notes published from
here go to the local database's `corrections` table only (the Knowledge Base side is set up in the cloud).
"""
import argparse
import contextlib
import json
import os
import shutil
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg

from rag import api, config, db, records, review, uploads

_HTML_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                "font-src https://fonts.gstatic.com; img-src 'self' data:; "
                                "connect-src 'self'; frame-ancestors 'none'"),
    "Referrer-Policy": "no-referrer",
}


class _State:
    """What the handler needs: a database connection made on demand, and the functions it calls."""

    def __init__(self, connect, ask, documents, web_dir, pdf_dir, ingest=None):
        self.connect, self.ask, self.documents = connect, ask, documents
        self.web_dir, self.pdf_dir = web_dir, pdf_dir
        self._conn = None
        self._pdf_files = None
        self.uploads = uploads.MemoryStore()
        self.records = records.MemoryStore()  # answers, ratings and notes (step 9b)
        # ingest(store, upload_id, data, connect) runs one upload; by default in a background thread.
        self.ingest = ingest or _ingest_in_background

    def kb_answer(self, question: str, document: str | None = None) -> dict:
        """A KB answer with the notes published on this server."""
        from rag import corrections, kb

        return kb.answer_question(question, document=document,
                                  notes_fn=lambda q, d: corrections.kb_match(q, d, store=self.records))

    def pdf_files(self) -> set[str]:
        """File names of the stored datasheets that are on disk: the only files /pdfs/ serves. Read from the
        database once; the browser's PDF viewer makes several requests per document. A vendor's https address
        (a PDF that is not on disk) is not one of them."""
        if self._pdf_files is None:
            self._pdf_files = {unquote(doc["pdf_url"].removeprefix("/pdfs/"))
                               for doc in self.documents(self.conn()) if (doc["pdf_url"] or "").startswith("/pdfs/")}
        return self._pdf_files

    def conn(self):
        """The open connection, or a new one if there is none or Postgres dropped it. Raises OperationalError."""
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._conn = self.connect()
        return self._conn


def _ingest_in_background(store, upload_id: str, data: bytes, connect) -> None:
    from rag import ingest  # pdfplumber, only when an upload is made

    def run():
        final = ingest.run_upload(store, upload_id, data, connect, to_kb=False)
        print(f"  upload {upload_id}: {final.get('status')}"
              f"{', ' + str(final.get('chunks')) + ' chunks' if final.get('chunks') else ''}"
              f"{' (' + final['error'] + ')' if final.get('error') else ''}", flush=True)
    threading.Thread(target=run, name=f"upload-{upload_id}", daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "datasheet-rag"
    # Seconds a client may stall while sending its request. The server answers one request at a time, so a client
    # that announces more bytes than it sends would otherwise hold it forever.
    timeout = 15

    # ------------------------------------------------------------------ responses

    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _not_found(self) -> None:
        self._json(404, {"error": "Not found."})

    # ------------------------------------------------------------------ checks

    def _host_ok(self) -> bool:
        """Only requests addressed to this machine. Stops a web page on another site from reaching the server
        through a DNS name that points at 127.0.0.1 (DNS rebinding)."""
        try:
            host = urlsplit("//" + (self.headers.get("Host") or "")).hostname or ""
        except ValueError:  # a malformed Host such as "[::1"
            return False
        return host.lower() in ("127.0.0.1", "localhost", "::1")

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json(403, {"error": "Open the page at http://127.0.0.1."})
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._page()
        if path == "/review.html":
            return self._page("review.html")
        if path.startswith(review.PREFIX):
            return self._review("GET", path, None)
        if path == "/api/documents":
            return self._documents()
        if path.startswith("/api/uploads/") and path.count("/") == 3:
            return self._json(*api.upload_status(path.removeprefix("/api/uploads/"), self.server.state.uploads))
        if path.startswith("/pdfs/"):
            return self._pdf(unquote(path.removeprefix("/pdfs/")))
        return self._not_found()

    do_HEAD = do_GET

    def _read_body(self, limit: int = config.API_MAX_BODY_BYTES) -> tuple[bytes | None, bool]:
        """(body, too_large); body is None when it is missing or over `limit`. Always read before any answer: closing
        with the body unread makes Windows reset the connection, and the client sees that instead of the answer."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return None, False
        body = self.rfile.read(min(length, max(limit, 1_000_000)))
        if length > limit:
            self.close_connection = True
            return None, True
        return body, False

    def do_POST(self) -> None:
        raw, too_large = self._read_body()
        if not self._host_ok():
            return self._json(403, {"error": "Open the page at http://127.0.0.1."})
        path = urlsplit(self.path).path
        if path not in ("/api/ask", "/api/uploads", "/api/feedback") and not path.startswith(review.PREFIX):
            return self._not_found()
        # A JSON content type cannot be sent across sites without a CORS preflight, which is never answered.
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"error": "Send the question as JSON."})
        if raw is None:
            return self._json(413 if too_large else 400, {"error": "The request body is missing or too large."})
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "The request body is not valid JSON."})

        state: _State = self.server.state
        if path == "/api/feedback":
            return self._json(*api.feedback(payload, state.records))
        if path.startswith(review.PREFIX):
            return self._review("POST", path, payload)
        if path == "/api/uploads":
            presign = lambda upload_id: {"put_url": f"/api/uploads/{upload_id}/file"}
            return self._json(*api.start_upload(payload, state.uploads, presign))
        start = time.perf_counter()
        try:
            # The kept connection, made only when the question passed its checks; this server never closes it.
            # KB answers match the notes published on this server (its records), not the deployed ones.
            status, body = state.ask(payload, lambda: contextlib.nullcontext(state.conn()),
                                     upload_store=state.uploads, answer_store=state.records,
                                     kb_answer_fn=state.kb_answer)
        except Exception:  # noqa: BLE001 (any other failure still gets an answer the page can show)
            traceback.print_exc()
            status, body = 500, {"error": "The server hit an unexpected error. Its console has the details."}
        elapsed = time.perf_counter() - start
        spent = body.get("spent_usd")
        print(f"  {status}  {elapsed:5.1f} s  {f'${spent:.4f}' if spent is not None else '       '}  "
              f"{str(payload.get('question', ''))[:70] if isinstance(payload, dict) else ''}", flush=True)
        self._json(status, body)

    def do_PUT(self) -> None:
        """The PDF of an upload made with POST /api/uploads: PUT /api/uploads/<id>/file, sent once."""
        raw, too_large = self._read_body(config.UPLOAD_MAX_BYTES)
        if not self._host_ok():
            return self._json(403, {"error": "Open the page at http://127.0.0.1."})
        parts = urlsplit(self.path).path.split("/")  # ["", "api", "uploads", "<id>", "file"]
        if len(parts) != 5 or parts[:3] != ["", "api", "uploads"] or parts[4] != "file":
            return self._not_found()
        upload_id = parts[3]
        if not (self.headers.get("Content-Type") or "").startswith("application/pdf"):
            return self._json(415, {"error": "Send the file as application/pdf."})
        if raw is None:
            return self._json(413 if too_large else 400, {"error": "The file is missing or too large."})
        state: _State = self.server.state
        record = state.uploads.get(upload_id) if uploads.valid_id(upload_id) else None
        if record is None or record["status"] != "waiting":
            return self._json(404, {"error": api.UPLOAD_UNKNOWN})
        # Off "waiting" before the thread starts, so a second PUT (a retry, a double click) cannot start another.
        state.uploads.update(upload_id, status="ingesting")
        state.ingest(state.uploads, upload_id, raw, state.connect)
        print(f"  upload {upload_id}: {len(raw):,} bytes received", flush=True)
        self._json(202, uploads.public(state.uploads.get(upload_id) or record))

    def _review(self, method: str, path: str, payload) -> None:
        """A reviewer route, without a login: only this machine can reach the server."""
        state: _State = self.server.state
        if path == review.PREFIX + "config":
            return self._json(200, {**review.config_body(), "auth": False})
        query = {name: values[-1] for name, values in parse_qs(urlsplit(self.path).query).items()}
        deps = review.Deps(store=state.records, open_conn=lambda: contextlib.nullcontext(state.conn()))
        try:
            status, body = review.route(method, path, query, payload, "local", deps)
        except Exception:  # noqa: BLE001 (any other failure still gets an answer the page can show)
            traceback.print_exc()
            status, body = 500, {"error": "The server hit an unexpected error. Its console has the details."}
        self._json(status, body)

    def _page(self, name: str = "index.html") -> None:
        page = self.server.state.web_dir / name
        if not page.is_file():
            return self._json(500, {"error": f"{page} is missing."})
        self._send(200, page.read_bytes(), "text/html; charset=utf-8", _HTML_HEADERS)

    def _documents(self) -> None:
        state: _State = self.server.state
        try:
            self._json(200, state.documents(state.conn()))
        except psycopg.OperationalError:
            self._json(503, {"error": api.NO_DATABASE})

    def _pdf(self, name: str) -> None:
        """A datasheet PDF, only if its file name is one of the stored documents' (an allow-list, so no path can
        reach any other file)."""
        state: _State = self.server.state
        try:
            allowed = state.pdf_files()
        except psycopg.OperationalError:
            return self._json(503, {"error": api.NO_DATABASE})
        path = state.pdf_dir / name
        if name not in allowed or not path.is_file():
            return self._not_found()
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'inline; filename="{name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)

    def log_message(self, format, *args) -> None:
        """Quiet: questions are printed by do_POST; everything else only when it fails."""
        if len(args) >= 2 and str(args[1]).startswith(("4", "5")) and self.command not in ("POST", "PUT"):
            sys.stderr.write(f"  {args[1]}  {self.command} {self.path}\n")


class _Server(HTTPServer):
    # HTTPServer sets SO_REUSEADDR. On Windows that lets a second server bind a port that is already in use, and
    # requests then reach either one. Off there, a second start fails with "Cannot listen on port".
    allow_reuse_address = os.name != "nt"


def make_server(host: str | None = None, port: int | None = None, connect=None, ask=None, documents=None,
                web_dir=None, pdf_dir=None, ingest=None) -> HTTPServer:
    """The HTTP server, not yet serving. Everything left as None comes from config and the real modules."""
    server = _Server((host or config.SERVER_HOST, config.SERVER_PORT if port is None else port), Handler)
    server.state = _State(connect or db.connect, ask or api.ask, documents or api.documents,
                          web_dir or config.WEB_DIR, pdf_dir or config.PDF_DIR, ingest)
    return server


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Serve the datasheet Q&A page on this machine.")
    parser.add_argument("--port", type=int, default=config.SERVER_PORT)
    args = parser.parse_args(argv)

    try:
        server = make_server(port=args.port)
    except OSError as error:
        sys.exit(f"Cannot listen on port {args.port} ({error}). Another program may use it; try --port 8080.")
    try:
        server.state.conn()
        print("Database: connected.")
    except psycopg.OperationalError:
        print(f"Database: not reachable. {api.NO_DATABASE}. The page will say so until it is up.")
    host, port = server.server_address[:2]
    print(f"Open http://{host}:{port}   (Ctrl+C stops the server)")
    print(f"Each new question costs about $0.0023 (reranker), a repeated one about $0.0003. "
          f"Calls are logged to {config.COST_LOG}.")
    print("  status  time  spent    question")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main(sys.argv[1:])
