"""Records of the feedback loop (Cycle 2 step 9b): saved answers, ratings and reviewer notes.

Deployed, they are items of the DynamoDB table USAGE_TABLE next to the daily counters and the uploads, so rating an
answer or reading the review queue never wakes Aurora. The local server keeps them in memory.

    answer#<answer_id>      the answer as the visitor saw it: question, datasheet, pipeline, passages, answer,
                            sources, notes used, model, prompt version, setup; kept ANSWER_KEEP_DAYS
    feedback#<answer_id>    one rating per answer: rating, reason, comment, a copy of the answer, then the
                            reviewer's diagnosis and note; kept FEEDBACK_KEEP_DAYS
    correction#<id>         a reviewer's note: datasheet, page, text, the question it answers, and how far
                            publishing got on each pipeline; kept until deleted

A comment is the visitor's own text: it is stored and shown to the reviewer as text, and never reaches the model.

Lists and dicts are stored as JSON text, so an item holds exactly what was saved (no float-to-Decimal conversion).
"""
import json
import re
import secrets
import threading
import time

from botocore.exceptions import ClientError

from rag import config, usage

_ID = re.compile(r"[0-9a-f]{32}")
ANSWER, FEEDBACK, CORRECTION = "answer#", "feedback#", "correction#"
# A saved passage keeps this much of its text: enough to review, and an item stays far under DynamoDB's 400 KB.
PASSAGE_CHARS = 2000


def new_id() -> str:
    """128 random bits as hex: an answer id cannot be guessed, so only its reader can rate it."""
    return secrets.token_hex(16)


def valid_id(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def days(n: float, now: float | None = None) -> int:
    """The epoch second `n` days from now, for `expires_at`."""
    return int((time.time() if now is None else now) + n * 86400)


class MemoryStore:
    """Records in memory, for the local server and the tests. The same calls as DynamoStore."""

    def __init__(self, clock=time.time):
        self._items, self._mutex, self._clock = {}, threading.Lock(), clock

    def _live(self, item: dict | None) -> bool:
        return item is not None and (item.get("expires_at") is None or item["expires_at"] > self._clock())

    def put_new(self, key: str, record: dict) -> bool:
        """Store a record that must not exist yet. False when it does."""
        with self._mutex:
            if self._live(self._items.get(key)):
                return False
            self._items[key] = json.loads(json.dumps(record))
            return True

    def get(self, key: str) -> dict | None:
        with self._mutex:
            item = self._items.get(key)
            return json.loads(json.dumps(item)) if self._live(item) else None

    def update(self, key: str, expect: dict | None = None, **fields) -> bool:
        """Set `fields` on a record that exists and, for each item of `expect`, has that value (a tuple: one of
        those values). False when it does not."""
        with self._mutex:
            item = self._items.get(key)
            if not self._live(item) or not _matches(item, expect):
                return False
            item.update(json.loads(json.dumps(fields)))
            return True

    def scan(self, prefix: str) -> list[dict]:
        with self._mutex:
            return [json.loads(json.dumps(item)) for key, item in self._items.items()
                    if key.startswith(prefix) and self._live(item)]


def _matches(item: dict, expect: dict | None) -> bool:
    for name, wanted in (expect or {}).items():
        allowed = wanted if isinstance(wanted, tuple) else (wanted,)
        if item.get(name) not in allowed:
            return False
    return True


# ---------------------------------------------------------------- DynamoDB

def _to_attr(value) -> dict:
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int | float):
        return {"N": str(value)}
    if isinstance(value, list | dict):
        return {"M": {"json": {"S": json.dumps(value, ensure_ascii=False)}}}
    return {"S": str(value)}


def _from_attr(attr: dict):
    if "NULL" in attr:
        return None
    if "BOOL" in attr:
        return attr["BOOL"]
    if "N" in attr:
        number = attr["N"]
        return int(number) if number.lstrip("-").isdigit() else float(number)
    if "M" in attr:
        return json.loads(attr["M"]["json"]["S"])
    return attr.get("S")


class DynamoStore:
    """Records as items of the DynamoDB table USAGE_TABLE (partition key `pk`, TTL attribute `expires_at`)."""

    def __init__(self, client=None, clock=time.time):
        self._client, self._clock = client, clock

    @property
    def client(self):
        return self._client or usage._client()

    def _live(self, record: dict) -> bool:
        # TTL deletes expired items some time later, not at once.
        return record.get("expires_at") is None or record["expires_at"] > self._clock()

    @staticmethod
    def _record(item: dict) -> dict:
        return {name: _from_attr(value) for name, value in item.items() if name != "pk"}

    def put_new(self, key: str, record: dict) -> bool:
        item = {"pk": {"S": key}} | {name: _to_attr(value) for name, value in record.items()}
        try:
            # An expired item that TTL has not deleted yet may be replaced.
            self.client.put_item(TableName=usage._table(), Item=item,
                                 ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
                                 ExpressionAttributeValues={":now": {"N": str(int(self._clock()))}})
        except ClientError as error:
            if usage._condition_failed(error):
                return False
            raise
        return True

    def get(self, key: str) -> dict | None:
        item = self.client.get_item(TableName=usage._table(), Key={"pk": {"S": key}},
                                    ConsistentRead=True).get("Item")
        if not item:
            return None
        record = self._record(item)
        return record if self._live(record) else None

    def update(self, key: str, expect: dict | None = None, **fields) -> bool:
        if not fields:
            return True
        # Live only, as `get` reads them: no expiry, or one still to come (TTL deletes late).
        names = {"#exp": "expires_at"}
        values = {":now": {"N": str(int(self._clock()))}, ":null": {"S": "NULL"}}
        conditions = ["attribute_exists(pk)",
                      "(attribute_not_exists(#exp) OR attribute_type(#exp, :null) OR #exp > :now)"]
        sets = []
        for i, (name, value) in enumerate(fields.items()):
            names[f"#f{i}"], values[f":v{i}"] = name, _to_attr(value)
            sets.append(f"#f{i} = :v{i}")
        for i, (name, wanted) in enumerate((expect or {}).items()):
            names[f"#e{i}"] = name
            allowed = wanted if isinstance(wanted, tuple) else (wanted,)
            options = []
            for j, value in enumerate(allowed):
                values[f":e{i}_{j}"] = _to_attr(value)
                options.append(f"#e{i} = :e{i}_{j}")
            conditions.append("(" + " OR ".join(options) + ")")
        try:
            self.client.update_item(
                TableName=usage._table(), Key={"pk": {"S": key}},
                UpdateExpression="SET " + ", ".join(sets), ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        except ClientError as error:
            if usage._condition_failed(error):
                return False
            raise
        return True

    def scan(self, prefix: str) -> list[dict]:
        """Every live record whose key starts with `prefix`. A Scan reads the whole table (counters, uploads,
        answers): fine at demo size, about 2,000 items at most with the expiries above."""
        records, start = [], None
        while True:
            request = {"TableName": usage._table(), "FilterExpression": "begins_with(pk, :prefix)",
                       "ExpressionAttributeValues": {":prefix": {"S": prefix}}}
            if start:
                request["ExclusiveStartKey"] = start
            response = self.client.scan(**request)
            records += [record for record in map(self._record, response.get("Items", [])) if self._live(record)]
            start = response.get("LastEvaluatedKey")
            if not start:
                return records


def default_store():
    """Where the records are deployed: DynamoDB (USAGE_TABLE). The local server passes its own MemoryStore."""
    return DynamoStore()


# ---------------------------------------------------------------- answers

def saved_passage(hit: dict) -> dict:
    passage = {"id": hit.get("id"), "document": hit.get("document"), "page": hit.get("page"),
               "page_label": hit.get("page_label"), "kind": hit.get("kind"),
               "text": (hit.get("text") or "")[:PASSAGE_CHARS]}
    if hit.get("correction_id"):
        passage["correction_id"] = hit["correction_id"]
    return passage


def answer_record(answer_id: str, question: str, document: str | None, pipeline: str, result: dict, body: dict,
                  now: float | None = None) -> dict:
    """What is kept of one answer: enough for a reviewer to see exactly what the visitor saw and why."""
    now = time.time() if now is None else now
    passages = result.get("passages") or []
    return {
        "answer_id": answer_id, "created_at": int(now), "expires_at": days(config.ANSWER_KEEP_DAYS, now),
        "question": question, "document": document, "pipeline": pipeline,
        "answer": result.get("answer"), "not_contained": bool(result.get("not_contained")),
        "sources": body.get("sources") or [],
        "passages": [saved_passage(hit) for hit in passages],
        "notes": [hit["correction_id"] for hit in passages if hit.get("correction_id")],
        "model": config.GENERATE_MODEL_ID, "prompt_version": config.PROMPT_VERSION, "setup": body.get("setup"),
    }
