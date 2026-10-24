"""Uploaded PDFs (Cycle 2 step 9a): their ids, their status records, the one-at-a-time lock, and the presigned POST.

No PDF library here, so the query Lambda can import it; parsing and embedding are in `rag.ingest`.

An upload's record says how far it got, per pipeline:
    {"upload_id", "filename", "status": "waiting" | "ingesting" | "ready" | "failed",
     "ours": "waiting" | "ready" | "failed", "kb": "waiting" | "syncing" | "ready" | "failed" | "off",
     "pages", "chunks", "embedded", "error", "kb_job", "created_at", "expires_at"}
`status` is "ready" once our pipeline can answer; the Knowledge Base side follows on its own ("kb").

Deployed, records live in the DynamoDB table USAGE_TABLE next to the daily counters (item `upload#<id>`, with the
same `expires_at` TTL attribute), so checking an upload never wakes Aurora. The local server keeps them in memory.
"""
import functools
import re
import secrets
import threading
import time
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath

from botocore.exceptions import ClientError

from rag import config, usage

_ID = re.compile(r"[0-9a-f]{16}")
LOCK_KEY = "ingest_lock"
FIELDS = ("upload_id", "filename", "status", "ours", "kb", "pages", "chunks", "embedded", "error", "kb_job",
          "created_at", "expires_at")
PUBLIC_FIELDS = ("upload_id", "filename", "status", "ours", "kb", "pages", "chunks", "embedded", "error",
                 "expires_at")
TOO_LARGE = f"The file is larger than {config.UPLOAD_MAX_BYTES // (1024 * 1024)} MB."
LATE = "The PDF arrived after its place had expired. Upload it again."


def new_id() -> str:
    return secrets.token_hex(8)


def valid_id(upload_id) -> bool:
    return isinstance(upload_id, str) and _ID.fullmatch(upload_id) is not None


def document_name(upload_id: str) -> str:
    """The upload's name in the database and in the KB sidecar: upload-<id>."""
    return config.UPLOAD_PREFIX + upload_id


def clean_filename(name) -> str | None:
    """The file name as shown back to its uploader: no folders, no control characters, at most UPLOAD_FILENAME_CHARS,
    ending in .pdf. None when nothing usable is left or it is not a .pdf name."""
    if not isinstance(name, str):
        return None
    name = PureWindowsPath(PurePosixPath(name).name).name  # a browser may send C:\fakepath\x.pdf
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if not name.lower().endswith(".pdf") or len(name) <= len(".pdf"):
        return None
    if len(name) > config.UPLOAD_FILENAME_CHARS:
        name = name[:config.UPLOAD_FILENAME_CHARS - 4].rstrip() + ".pdf"
    return name


def title_of(filename: str) -> str:
    return filename[:-4] if filename.lower().endswith(".pdf") else filename


def new_record(upload_id: str, filename: str, kb: bool, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    return {"upload_id": upload_id, "filename": filename, "status": "waiting", "ours": "waiting",
            "kb": "waiting" if kb else "off", "pages": None, "chunks": None, "embedded": 0, "error": None,
            "kb_job": None, "created_at": int(now), "expires_at": int(now + config.UPLOAD_KEEP_HOURS * 3600)}


def public(record: dict) -> dict:
    """What the page may see of a record."""
    return {key: record.get(key) for key in PUBLIC_FIELDS}


def kb_configured() -> bool:
    """True when an upload can also go to the Knowledge Base: its uploads data source and the bucket are set, and so
    is the curated data source, whose filter is all that keeps uploads out of the searches that name no document."""
    return bool(config.KB_ID and config.KB_UPLOAD_DATA_SOURCE_ID and config.KB_CURATED_DATA_SOURCE_ID
                and config.UPLOAD_BUCKET)


class MemoryStore:
    """Records and the lock in memory, for the local server (one process, a background ingest thread)."""

    def __init__(self, clock=time.time):
        self._records, self._lock_owner, self._lock_until = {}, None, 0.0
        self._mutex, self._clock = threading.Lock(), clock

    def create(self, record: dict) -> None:
        with self._mutex:
            self._records[record["upload_id"]] = dict(record)

    def get(self, upload_id: str) -> dict | None:
        with self._mutex:
            record = self._records.get(upload_id)
            if record is None or record["expires_at"] <= self._clock():
                return None
            return dict(record)

    def update(self, upload_id: str, **fields) -> None:
        with self._mutex:
            if upload_id in self._records:
                self._records[upload_id].update(fields)

    def take_lock(self, owner: str, seconds: float) -> bool:
        """Hold the lock for `seconds`. False when someone else holds it; the holder may take it again (extend)."""
        with self._mutex:
            now = self._clock()
            if self._lock_owner not in (None, owner) and self._lock_until > now:
                return False
            self._lock_owner, self._lock_until = owner, now + seconds
            return True

    def release_lock(self, owner: str) -> None:
        with self._mutex:
            if self._lock_owner == owner:
                self._lock_owner, self._lock_until = None, 0.0


# ---------------------------------------------------------------- DynamoDB

def _to_attr(value) -> dict:
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int | float):
        return {"N": str(value)}
    return {"S": str(value)}


def _from_attr(attr: dict):
    if "NULL" in attr:
        return None
    if "BOOL" in attr:
        return attr["BOOL"]
    if "N" in attr:
        number = attr["N"]
        return int(number) if number.lstrip("-").isdigit() else float(number)
    return attr.get("S")


# The same table, client and refused-condition check as the daily counters.
_condition_failed = usage._condition_failed


class DynamoStore:
    """Records and the lock as items of the DynamoDB table USAGE_TABLE."""

    def __init__(self, client=None, clock=time.time):
        self._client, self._clock = client, clock

    @property
    def client(self):
        return self._client or usage._client()

    @staticmethod
    def _table() -> str:
        return usage._table()

    @staticmethod
    def _key(upload_id: str) -> dict:
        return {"pk": {"S": f"upload#{upload_id}"}}

    def create(self, record: dict) -> None:
        item = {"pk": {"S": f"upload#{record['upload_id']}"}} | {key: _to_attr(record.get(key)) for key in FIELDS}
        self.client.put_item(TableName=self._table(), Item=item, ConditionExpression="attribute_not_exists(pk)")

    def get(self, upload_id: str) -> dict | None:
        item = self.client.get_item(TableName=self._table(), Key=self._key(upload_id), ConsistentRead=True).get("Item")
        if not item:
            return None
        record = {key: _from_attr(item[key]) for key in FIELDS if key in item}
        # TTL deletes expired items some time later, not at once.
        if (record.get("expires_at") or 0) <= self._clock():
            return None
        return record

    def update(self, upload_id: str, **fields) -> None:
        if not fields:
            return
        names = {f"#f{i}": key for i, key in enumerate(fields)}
        values = {f":v{i}": _to_attr(value) for i, value in enumerate(fields.values())}
        try:
            self.client.update_item(
                TableName=self._table(), Key=self._key(upload_id),
                UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames=names, ExpressionAttributeValues=values,
            )
        except ClientError as error:
            if not _condition_failed(error):  # a record that is gone (expired) stays gone
                raise

    def take_lock(self, owner: str, seconds: float) -> bool:
        now = self._clock()
        try:
            self.client.put_item(
                TableName=self._table(),
                Item={"pk": {"S": LOCK_KEY}, "owner": {"S": owner}, "expires_at": {"N": str(int(now + seconds))}},
                ConditionExpression="attribute_not_exists(pk) OR expires_at < :now OR #owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":now": {"N": str(int(now))}, ":owner": {"S": owner}},
            )
        except ClientError as error:
            if _condition_failed(error):
                return False
            raise
        return True

    def release_lock(self, owner: str) -> None:
        try:
            self.client.delete_item(TableName=self._table(), Key={"pk": {"S": LOCK_KEY}},
                                    ConditionExpression="#owner = :owner",
                                    ExpressionAttributeNames={"#owner": "owner"},
                                    ExpressionAttributeValues={":owner": {"S": owner}})
        except ClientError as error:
            if not _condition_failed(error):  # someone else's lock, or already gone
                raise


# ---------------------------------------------------------------- S3

@functools.cache
def _s3():
    import boto3
    from botocore.config import Config

    # SigV4 with the regional endpoint: a presigned POST signed the old way is refused for new buckets.
    return boto3.client("s3", region_name=config.AWS_REGION, config=Config(signature_version="s3v4"))


def incoming_key(upload_id: str) -> str:
    return f"{config.UPLOAD_INCOMING}{upload_id}.pdf"


def presign_post(upload_id: str, client=None) -> dict:
    """{"post": {"url", "fields"}}: what the browser needs to send the PDF straight to S3. S3 itself refuses a file
    over UPLOAD_MAX_BYTES or of another content type, and the form expires after UPLOAD_POST_EXPIRES_S."""
    if not config.UPLOAD_BUCKET:
        raise RuntimeError("UPLOAD_BUCKET is not set.")
    post = (client or _s3()).generate_presigned_post(
        Bucket=config.UPLOAD_BUCKET, Key=incoming_key(upload_id),
        Fields={"Content-Type": "application/pdf"},
        Conditions=[{"Content-Type": "application/pdf"}, ["content-length-range", 1, config.UPLOAD_MAX_BYTES]],
        ExpiresIn=config.UPLOAD_POST_EXPIRES_S,
    )
    return {"post": {"url": post["url"], "fields": post["fields"]}}


def default_store():
    """Where the records are: DynamoDB (USAGE_TABLE). The local server passes its own MemoryStore instead."""
    return DynamoStore()
