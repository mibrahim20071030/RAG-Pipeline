"""Cycle 2 step 9b tests for rag.records: the answer, rating and note records, in memory and in DynamoDB (a fake
client that evaluates the conditions DynamoStore sends). No AWS call."""
import re

import pytest
from botocore.exceptions import ClientError

from rag import config, records


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}}, "Op")


def _value(attr):
    return records._from_attr(attr)


class FakeDynamo:
    """Items by `pk`, with the conditions DynamoStore writes; Scan pages after `page` items."""

    def __init__(self, page=2):
        self.items, self.page, self.scans = {}, page, 0

    def put_item(self, TableName, Item, ConditionExpression, ExpressionAttributeValues):
        assert ConditionExpression == "attribute_not_exists(pk) OR expires_at < :now"
        old = self.items.get(Item["pk"]["S"])
        now = int(ExpressionAttributeValues[":now"]["N"])
        if old is not None:
            expires = _value(old.get("expires_at", {"NULL": True}))
            if expires is None or not expires < now:
                raise _failed()
        self.items[Item["pk"]["S"]] = Item

    def get_item(self, TableName, Key, ConsistentRead):
        assert ConsistentRead
        item = self.items.get(Key["pk"]["S"])
        return {"Item": item} if item else {}

    def update_item(self, TableName, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        item = self.items.get(Key["pk"]["S"])
        groups = ConditionExpression.split(" AND ")
        assert groups[0] == "attribute_exists(pk)"
        assert groups[1] == "(attribute_not_exists(#exp) OR attribute_type(#exp, :null) OR #exp > :now)"
        if item is None:
            raise _failed()
        expires = _value(item.get("expires_at", {"NULL": True}))
        if expires is not None and not expires > int(ExpressionAttributeValues[":now"]["N"]):
            raise _failed()
        for group in groups[2:]:
            options = re.findall(r"(#e\d+) = (:e\d+_\d+)", group)
            if not any(item.get(ExpressionAttributeNames[name]) == ExpressionAttributeValues[value]
                       for name, value in options):
                raise _failed()
        for assignment in UpdateExpression.removeprefix("SET ").split(", "):
            name, value = assignment.split(" = ")
            item[ExpressionAttributeNames[name]] = ExpressionAttributeValues[value]

    def scan(self, TableName, FilterExpression, ExpressionAttributeValues, ExclusiveStartKey=None):
        assert FilterExpression == "begins_with(pk, :prefix)"
        self.scans += 1
        keys = sorted(self.items)
        start = keys.index(ExclusiveStartKey["pk"]["S"]) + 1 if ExclusiveStartKey else 0
        chunk = keys[start:start + self.page]
        prefix = ExpressionAttributeValues[":prefix"]["S"]
        response = {"Items": [self.items[key] for key in chunk if key.startswith(prefix)]}
        if start + self.page < len(keys):
            response["LastEvaluatedKey"] = {"pk": {"S": chunk[-1]}}
        return response


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(params=["memory", "dynamo"])
def store(request, clock, monkeypatch):
    monkeypatch.setattr(config, "USAGE_TABLE", "test-table")
    if request.param == "memory":
        return records.MemoryStore(clock=clock)
    return records.DynamoStore(client=FakeDynamo(), clock=clock)


def test_ids_are_128_bit_hex():
    a, b = records.new_id(), records.new_id()
    assert a != b and records.valid_id(a) and len(a) == 32
    assert not records.valid_id("0" * 31) and not records.valid_id(a.upper()) and not records.valid_id(None)


def test_a_record_is_stored_once(store):
    assert store.put_new("answer#1", {"question": "q", "expires_at": 2_000_000}) is True
    assert store.put_new("answer#1", {"question": "other", "expires_at": 2_000_000}) is False
    assert store.get("answer#1")["question"] == "q"


def test_lists_and_dicts_come_back_as_they_were_saved(store):
    record = {"passages": [{"page": 4, "score": 0.25, "text": "±0.5 °C"}], "setup": {"k": 10}, "flag": True,
              "none": None, "n": 3, "x": 0.5, "expires_at": None}
    store.put_new("answer#2", record)
    assert store.get("answer#2") == record


def test_an_expired_record_is_gone_and_can_be_replaced(store, clock):
    store.put_new("answer#3", {"question": "old", "expires_at": int(clock.now) + 10})
    clock.now += 11
    assert store.get("answer#3") is None
    assert store.put_new("answer#3", {"question": "new", "expires_at": int(clock.now) + 10})
    assert store.get("answer#3")["question"] == "new"


def test_a_record_without_expiry_is_kept(store, clock):
    store.put_new("correction#1", {"status": "draft", "expires_at": None})
    clock.now += 10 ** 9
    assert store.get("correction#1")["status"] == "draft"


def test_update_checks_the_expected_values(store):
    store.put_new("feedback#1", {"status": "pending", "expires_at": None})
    assert store.update("feedback#1", expect={"status": ("pending", "diagnosed")}, status="diagnosed", note="n")
    assert store.update("feedback#1", expect={"status": "pending"}, status="closed") is False
    assert store.get("feedback#1") == {"status": "diagnosed", "note": "n", "expires_at": None}
    assert store.update("feedback#missing", status="x") is False


def test_an_expired_record_is_not_updated(store, clock):
    store.put_new("feedback#9", {"status": "pending", "expires_at": int(clock.now) + 10})
    clock.now += 11
    assert store.update("feedback#9", expect={"status": "pending"}, status="diagnosed") is False


def test_scan_reads_every_page_and_only_the_prefix(store, clock):
    for i in range(5):
        store.put_new(f"feedback#{i}", {"i": i, "expires_at": None})
    store.put_new("answer#9", {"i": 9, "expires_at": None})
    store.put_new("feedback#old", {"i": -1, "expires_at": int(clock.now) - 1})
    assert sorted(item["i"] for item in store.scan("feedback#")) == [0, 1, 2, 3, 4]


def test_answer_record_keeps_what_the_visitor_saw():
    result = {"answer": "35 V [1] [2]", "not_contained": False,
              "passages": [{"id": "note-abc", "document": "lm35", "page": 4, "page_label": None, "kind": "note",
                            "text": "x" * 5000, "score": 0.8, "correction_id": "abc"},
                           {"id": 7, "document": "lm35", "page": 4, "page_label": "3", "kind": "text", "text": "y"}]}
    body = {"sources": [{"label": 1, "document": "lm35", "page": 4}], "setup": {"pipeline": "ours"}}
    record = records.answer_record("a" * 32, "q?", "lm35", "ours", result, body, now=100)
    assert record["expires_at"] == 100 + config.ANSWER_KEEP_DAYS * 86400
    assert record["notes"] == ["abc"] and record["sources"] == body["sources"]
    assert len(record["passages"][0]["text"]) == records.PASSAGE_CHARS
    assert record["passages"][1] == {"id": 7, "document": "lm35", "page": 4, "page_label": "3", "kind": "text",
                                     "text": "y"}
    assert record["prompt_version"] == config.PROMPT_VERSION and record["model"] == config.GENERATE_MODEL_ID
