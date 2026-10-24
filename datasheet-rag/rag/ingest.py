"""Ingest one uploaded PDF (Cycle 2 step 9a): check it, parse it, chunk it, embed it, store it, hand it to the KB.

The same steps as the curated datasheets (`rag.parse`, `rag.chunk` through `load.strategy_inputs`, `db.replace_chunks`),
with the limits of an untrusted file around them. Deployed, `handler.ingest_handler` runs `run_upload` when the file
lands in S3; locally, `rag.server` runs it in a background thread.

The text is embedded at UPLOAD_EMBED_PER_MIN (40 of Titan's 60 calls a minute), so a 300-chunk PDF takes about 8
minutes and visitors' questions still get through. The database is connected only after that, for the one write at
the end: an open connection keeps Aurora from pausing. An upload's vectors are not put in `embedding_cache`, so nothing
of its text outlives the upload; its chunks are deleted by the first upload or question after it expires (and no
search reads them once it has).
"""
import json
import tempfile
import time
import traceback
from datetime import UTC, datetime, timedelta
from itertools import batched
from pathlib import Path

import pdfplumber

from rag import config, db, embed, kb, load, parse, uploads

FAILED = "The PDF could not be processed. Try another file."


class UploadRejected(Exception):
    """An upload refused for a reason its uploader can act on; the message is shown to them."""


def check_pdf(data: bytes) -> None:
    """Refuse what is clearly not an acceptable PDF before any parsing: empty, too large, or no PDF header."""
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise UploadRejected(uploads.TOO_LARGE)
    if b"%PDF-" not in data[:1024]:  # PDF readers accept the header anywhere in the first 1,024 bytes
        raise UploadRejected("The file is not a PDF.")


def page_count(path: Path) -> int:
    """Pages in the file, read from its page tree without extracting any text."""
    try:
        with pdfplumber.open(path) as pdf:
            return len(pdf.pages)
    except Exception as error:
        raise UploadRejected("The file could not be read as a PDF (it may be damaged or password-protected).") \
            from error


def parse_upload(data: bytes, upload_id: str, filename: str) -> dict:
    """The parsed document, as `parse.parse_pdf` gives it, named upload-<id> and with the file's title as its part.
    Refuses a PDF with too many pages, a scanned one, or one with no text."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{uploads.document_name(upload_id)}.pdf"
        path.write_bytes(data)
        pages = page_count(path)
        if pages > config.UPLOAD_MAX_PAGES:
            raise UploadRejected(f"The PDF has {pages} pages; the limit is {config.UPLOAD_MAX_PAGES}.")
        try:
            parsed = parse.parse_pdf(path)
        except parse.ScannedPdfError as error:
            raise UploadRejected("The PDF is scanned (its pages are images with no text to read).") from error
        except Exception as error:
            raise UploadRejected("The PDF could not be read.") from error
    if not any(page["text"].strip() for page in parsed["pages"]):
        raise UploadRejected("The PDF has no text to read.")
    parsed["file"] = filename
    parsed["part"] = uploads.title_of(filename)
    return parsed


def paced_embed(texts: list[str], label: str, embed_fn=None, progress=None, sleep=time.sleep,
                clock=time.monotonic) -> list:
    """Vectors for the texts, in batches of UPLOAD_EMBED_BATCH, no faster than UPLOAD_EMBED_PER_MIN.
    `progress(done)` is called after each batch."""
    embed_fn = embed_fn or embed.embed_texts
    vectors = []
    for batch in batched(texts, config.UPLOAD_EMBED_BATCH):
        started = clock()
        vectors += embed_fn(list(batch), label=label)
        if progress:
            progress(len(vectors))
        if len(vectors) < len(texts):
            wait = len(batch) * 60 / config.UPLOAD_EMBED_PER_MIN - (clock() - started)
            if wait > 0:
                sleep(wait)
    return vectors


def ingest_pdf(connect, data: bytes, upload_id: str, filename: str, embed_fn=None, progress=None,
               sleep=time.sleep, now: datetime | None = None) -> dict:
    """Check, parse, chunk and embed one upload and store it as document upload-<id>, for UPLOAD_KEEP_HOURS.

    `connect()` gives a context manager yielding a database connection, entered only for the final write.
    `progress(**fields)` is told the page and chunk counts, then how many chunks are embedded. Raises UploadRejected
    with a message for the uploader; anything else is a failure of ours."""
    check_pdf(data)
    parsed = parse_upload(data, upload_id, filename)
    chunks, inputs = load.strategy_inputs(parsed, config.UPLOAD_STRATEGY)
    if not chunks:
        raise UploadRejected("The PDF has no text to read.")
    if len(chunks) > config.UPLOAD_MAX_CHUNKS:
        raise UploadRejected(f"The PDF has too much text: {len(chunks)} passages; the limit is "
                             f"{config.UPLOAD_MAX_CHUNKS} (about {config.UPLOAD_MAX_CHUNKS // 3} pages of tables).")
    report = progress or (lambda **fields: None)
    report(pages=parsed["page_count"], chunks=len(chunks), embedded=0)

    vectors = paced_embed(inputs, f"upload/{upload_id}", embed_fn, lambda done: report(embedded=done), sleep)
    expires_at = (now or datetime.now(UTC)) + timedelta(hours=config.UPLOAD_KEEP_HOURS)
    name = uploads.document_name(upload_id)
    with connect() as conn, conn.transaction():
        deleted = db.delete_expired_uploads(conn)
        document_id = db.store_upload_document(conn, name, filename, parsed["part"], expires_at)
        stored = db.replace_chunks(conn, document_id, config.UPLOAD_STRATEGY, chunks, vectors)
    return {"document": name, "pages": parsed["page_count"], "chunks": stored, "expired_deleted": deleted}


# ---------------------------------------------------------------- Knowledge Base

def kb_sidecar(upload_id: str, filename: str) -> dict:
    """The .metadata.json the KB reads with the PDF: the same `document` and `part` keys as the curated sidecars."""
    def text(value: str) -> dict:
        return {"value": {"type": "STRING", "stringValue": value}}
    return {"metadataAttributes": {config.KB_DOCUMENT_KEY: text(uploads.document_name(upload_id)),
                                   "part": text(uploads.title_of(filename)[:200])}}


def stage_for_kb(data: bytes, upload_id: str, filename: str, s3=None) -> str:
    """Put the checked PDF and its sidecar under UPLOAD_KB_PREFIX, the uploads data source's prefix. Returns the key."""
    s3 = s3 or uploads._s3()
    key = f"{config.UPLOAD_KB_PREFIX}{upload_id}.pdf"
    s3.put_object(Bucket=config.UPLOAD_BUCKET, Key=key, Body=data, ContentType="application/pdf")
    s3.put_object(Bucket=config.UPLOAD_BUCKET, Key=key + ".metadata.json",
                  Body=json.dumps(kb_sidecar(upload_id, filename)).encode("utf-8"), ContentType="application/json")
    return key


def start_kb_sync(upload_id: str, client=None, sleep=time.sleep, attempts: int = 4) -> str:
    """Start the uploads data source's ingestion job. Only one runs at a time per data source, so a conflict (an
    earlier job still running) is retried after 20 s."""
    from botocore.exceptions import ClientError

    for attempt in range(1, attempts + 1):
        try:
            return kb.start_ingestion(upload_id, client=client)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConflictException" or attempt == attempts:
                raise
            sleep(20)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- one upload, start to finish

def run_upload(store, upload_id: str, data: bytes, connect, to_kb: bool, ingest_fn=None, s3=None,
               kb_client=None) -> dict:
    """Ingest one upload and keep its record up to date; the lock is released at the end, whatever happens.

    `connect()` gives a database connection, used as a context manager and so closed after the write. With `to_kb`,
    a PDF stored for our pipeline is also sent to the Knowledge Base's uploads data source. Returns the final record."""
    record = store.get(upload_id)
    if record is None:
        return {"upload_id": upload_id, "status": "failed", "error": "unknown or expired upload"}
    # The lock this upload took in start_upload, now held for the whole ingest. If it expired and another upload took
    # it (a file sent long after its place was made), this one stops: two at once would share Titan's quota.
    if not store.take_lock(upload_id, config.UPLOAD_LOCK_INGEST_S):
        store.update(upload_id, status="failed", ours="failed", kb="off", error=uploads.LATE)
        return store.get(upload_id) or {}
    try:
        store.update(upload_id, status="ingesting")
        (ingest_fn or ingest_pdf)(connect, data, upload_id, record["filename"],
                                  progress=lambda **fields: store.update(upload_id, **fields))
        store.update(upload_id, status="ready", ours="ready")
    except UploadRejected as error:
        store.update(upload_id, status="failed", ours="failed", kb="off", error=str(error))
        store.release_lock(upload_id)
        return store.get(upload_id) or {}
    except Exception:  # noqa: BLE001 (the record must say it failed, whatever went wrong)
        traceback.print_exc()
        store.update(upload_id, status="failed", ours="failed", kb="off", error=FAILED)
        store.release_lock(upload_id)
        return store.get(upload_id) or {}

    if to_kb:
        try:
            stage_for_kb(data, upload_id, record["filename"], s3=s3)
            store.update(upload_id, kb="syncing", kb_job=start_kb_sync(upload_id, client=kb_client))
        except Exception:  # noqa: BLE001 (our pipeline can still answer; only the KB side failed)
            traceback.print_exc()
            store.update(upload_id, kb="failed")
    store.release_lock(upload_id)
    return store.get(upload_id) or {}
