"""Cycle 2 step 9a tests for rag.uploads: upload ids, file names, records and the one-at-a-time lock, in memory and in
DynamoDB, and the presigned POST. No AWS call: fake clients."""
import pytest
from botocore.exceptions import ClientError

from rag import config, uploads

ID = "0123456789abcdef"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- ids and names

def test_new_ids_are_valid_and_different():
    ids = {uploads.new_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(uploads.valid_id(upload_id) for upload_id in ids)


@pytest.mark.parametrize("value", ["", "0123", "0123456789ABCDEF", "0123456789abcdeg", "../../etc/passwd", None, 7,
                                   ID + "0"])
def test_invalid_ids(value):
    assert not uploads.valid_id(value)


def test_document_name_has_the_upload_prefix():
    assert uploads.document_name(ID) == config.UPLOAD_PREFIX + ID == "upload-" + ID


@pytest.mark.parametrize(("given", "expected"), [
    ("sensor.pdf", "sensor.pdf"),
    ("Sensor Rev 2.PDF", "Sensor Rev 2.PDF"),
    (r"C:\fakepath\sensor.pdf", "sensor.pdf"),
    ("folder/sub/sensor.pdf", "sensor.pdf"),
    ("sen\x00so\nr.pdf", "sensor.pdf"),
    ("  spaced.pdf  ", "spaced.pdf"),
])
def test_clean_filename(given, expected):
    assert uploads.clean_filename(given) == expected


@pytest.mark.parametrize("given", ["notes.txt", "pdf", ".pdf", "", None, 12, "sensor.pdf.exe"])
def test_clean_filename_refuses_what_is_not_a_pdf_name(given):
    assert uploads.clean_filename(given) is None


def test_long_filename_is_cut_and_keeps_its_extension():
    name = uploads.clean_filename("x" * 500 + ".pdf")
    assert len(name) <= config.UPLOAD_FILENAME_CHARS and name.endswith(".pdf")


def test_title_drops_the_extension():
    assert uploads.title_of("Sensor Rev 2.PDF") == "Sensor Rev 2"


def test_new_record_and_its_public_part():
    record = uploads.new_record(ID, "s.pdf", kb=True, now=1000)
    assert record["status"] == "waiting" and record["ours"] == "waiting" and record["kb"] == "waiting"
    assert record["expires_at"] == 1000 + config.UPLOAD_KEEP_HOURS * 3600
    assert uploads.new_record(ID, "s.pdf", kb=False, now=1000)["kb"] == "off"
    shown = uploads.public({**record, "kb_job": "JOB1234567", "created_at": 5})
    assert "kb_job" not in shown and "created_at" not in shown
    assert shown["upload_id"] == ID and shown["filename"] == "s.pdf"


def test_kb_configured_needs_the_kb_its_uploads_source_and_the_bucket(monkeypatch):
    monkeypatch.setattr(config, "KB_ID", "KB12345678")
    monkeypatch.setattr(config, "KB_UPLOAD_DATA_SOURCE_ID", "DS12345678")
    monkeypatch.setattr(config, "KB_CURATED_DATA_SOURCE_ID", "DSCURATED1")
    monkeypatch.setattr(config, "UPLOAD_BUCKET", "bucket")
    assert uploads.kb_configured()
    monkeypatch.setattr(config, "KB_CURATED_DATA_SOURCE_ID", "")
    assert not uploads.kb_configured()  # without it, uploads would reach searches that name no document
    monkeypatch.setattr(config, "KB_CURATED_DATA_SOURCE_ID", "DSCURATED1")
    monkeypatch.setattr(config, "KB_UPLOAD_DATA_SOURCE_ID", "")
    assert not uploads.kb_configured()


# ---------------------------------------------------------------- the memory store

def test_memory_store_round_trip_and_expiry():
    clock = Clock()
    store = uploads.MemoryStore(clock=clock)
    store.create(uploads.new_record(ID, "s.pdf", kb=False, now=clock.now))
    store.update(ID, status="ingesting", chunks=12)
    assert store.get(ID)["status"] == "ingesting" and store.get(ID)["chunks"] == 12
    store.update("ffffffffffffffff", status="ready")  # unknown: nothing happens
    clock.now += config.UPLOAD_KEEP_HOURS * 3600
    assert store.get(ID) is None


def test_memory_store_get_returns_a_copy():
    store = uploads.MemoryStore()
    store.create(uploads.new_record(ID, "s.pdf", kb=False))
    store.get(ID)["status"] = "ready"
    assert store.get(ID)["status"] == "waiting"


def test_memory_lock_one_holder_at_a_time_until_it_expires():
    clock = Clock()
    store = uploads.MemoryStore(clock=clock)
    assert store.take_lock("a", 60)
    assert not store.take_lock("b", 60)
    assert store.take_lock("a", 600)  # the holder extends it
    clock.now += 601
    assert store.take_lock("b", 60)  # expired, so another may take it
    store.release_lock("a")  # not the holder: no effect
    assert not store.take_lock("c", 60)
    store.release_lock("b")
    assert store.take_lock("c", 60)


# ---------------------------------------------------------------- the DynamoDB store

def _failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}}, "PutItem")


class FakeDynamo:
    """Just the calls and conditions DynamoStore sends, on items kept by `pk`."""

    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item, ConditionExpression, ExpressionAttributeNames=None,
                 ExpressionAttributeValues=None):
        pk = Item["pk"]["S"]
        old = self.items.get(pk)
        if ConditionExpression == "attribute_not_exists(pk)":
            if old:
                raise _failed()
        elif ConditionExpression.startswith("attribute_not_exists(pk) OR expires_at < :now"):
            values = ExpressionAttributeValues
            if old and not (int(old["expires_at"]["N"]) < int(values[":now"]["N"])
                            or old["owner"]["S"] == values[":owner"]["S"]):
                raise _failed()
        else:
            raise AssertionError(ConditionExpression)
        self.items[pk] = Item

    def get_item(self, TableName, Key, ConsistentRead):
        assert ConsistentRead
        item = self.items.get(Key["pk"]["S"])
        return {"Item": item} if item else {}

    def update_item(self, TableName, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        assert ConditionExpression == "attribute_exists(pk)"
        item = self.items.get(Key["pk"]["S"])
        if item is None:
            raise _failed()
        for assignment in UpdateExpression.removeprefix("SET ").split(", "):
            name, value = assignment.split(" = ")
            item[ExpressionAttributeNames[name]] = ExpressionAttributeValues[value]

    def delete_item(self, TableName, Key, ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        item = self.items.get(Key["pk"]["S"])
        if item is None or item["owner"]["S"] != ExpressionAttributeValues[":owner"]["S"]:
            raise _failed()
        del self.items[Key["pk"]["S"]]


@pytest.fixture
def dynamo(monkeypatch):
    monkeypatch.setattr(config, "USAGE_TABLE", "datasheet-rag-usage")
    clock = Clock()
    return uploads.DynamoStore(client=FakeDynamo(), clock=clock), clock


def test_dynamo_record_round_trip_keeps_types(dynamo):
    store, clock = dynamo
    store.create(uploads.new_record(ID, "s.pdf", kb=True, now=clock.now))
    record = store.get(ID)
    assert record["pages"] is None and record["embedded"] == 0 and record["filename"] == "s.pdf"
    store.update(ID, pages=12, chunks=40, status="ready", error=None)
    record = store.get(ID)
    assert (record["pages"], record["chunks"], record["status"], record["error"]) == (12, 40, "ready", None)


def test_dynamo_create_twice_is_refused(dynamo):
    store, clock = dynamo
    store.create(uploads.new_record(ID, "s.pdf", kb=False, now=clock.now))
    with pytest.raises(ClientError):
        store.create(uploads.new_record(ID, "s.pdf", kb=False, now=clock.now))


def test_dynamo_expired_record_is_not_returned_before_ttl_deletes_it(dynamo):
    store, clock = dynamo
    store.create(uploads.new_record(ID, "s.pdf", kb=False, now=clock.now))
    clock.now += config.UPLOAD_KEEP_HOURS * 3600 + 1
    assert store.get(ID) is None


def test_dynamo_update_of_a_missing_record_does_nothing(dynamo):
    store, _ = dynamo
    store.update(ID, status="ready")
    assert store.get(ID) is None


def test_dynamo_lock(dynamo):
    store, clock = dynamo
    assert store.take_lock("a", 60)
    assert not store.take_lock("b", 60)
    assert store.take_lock("a", 600)
    clock.now += 601
    assert store.take_lock("b", 60)
    store.release_lock("a")  # not the holder: no error, no effect
    assert not store.take_lock("c", 60)
    store.release_lock("b")
    assert store.take_lock("c", 60)


def test_dynamo_store_needs_the_table_name(monkeypatch):
    monkeypatch.setattr(config, "USAGE_TABLE", "")
    with pytest.raises(RuntimeError, match="USAGE_TABLE"):
        uploads.DynamoStore(client=FakeDynamo()).get(ID)


# ---------------------------------------------------------------- the presigned POST

class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_post(self, **kwargs):
        self.calls.append(kwargs)
        return {"url": "https://bucket.s3.amazonaws.com/", "fields": {"key": kwargs["Key"], "policy": "p"}}


def test_presign_post_limits_size_type_and_key(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_BUCKET", "bucket")
    s3 = FakeS3()
    result = uploads.presign_post(ID, client=s3)
    assert result == {"post": {"url": "https://bucket.s3.amazonaws.com/",
                               "fields": {"key": f"incoming/{ID}.pdf", "policy": "p"}}}
    call = s3.calls[0]
    assert call["Bucket"] == "bucket" and call["Key"] == uploads.incoming_key(ID) == f"incoming/{ID}.pdf"
    assert ["content-length-range", 1, config.UPLOAD_MAX_BYTES] in call["Conditions"]
    assert {"Content-Type": "application/pdf"} in call["Conditions"]
    assert call["Fields"] == {"Content-Type": "application/pdf"}  # a condition needs its field
    assert call["ExpiresIn"] == config.UPLOAD_POST_EXPIRES_S


def test_presign_post_needs_the_bucket(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_BUCKET", "")
    with pytest.raises(RuntimeError, match="UPLOAD_BUCKET"):
        uploads.presign_post(ID, client=FakeS3())
