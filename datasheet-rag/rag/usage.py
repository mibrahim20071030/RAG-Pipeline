"""Daily counters in DynamoDB (Cycle 2 step 7b): how many questions the demo has answered today.

The cap used to live in Aurora, so a request over it still woke Aurora to find that out. Here it is one DynamoDB item
per kind and UTC day: {"pk": "asks#2026-10-23", "n": 12, "expires_at": <epoch>}. `expires_at` is a few days later, so
DynamoDB's TTL can delete old days. `kind` is "asks" (our pipeline) or "kb_asks" (the Knowledge Base, step 8);
later steps can count other things the same way.
"""
import functools
from datetime import UTC, date, datetime, timedelta

from botocore.exceptions import ClientError

from rag import config


@functools.cache
def _client():
    import boto3

    return boto3.client("dynamodb", region_name=config.AWS_REGION)


def today() -> date:
    """Today in UTC, the day a counter counts against."""
    return datetime.now(UTC).date()


def _table() -> str:
    if not config.USAGE_TABLE:
        raise RuntimeError("USAGE_TABLE is not set: put the DynamoDB table's name (datasheet-rag-usage) in .env, "
                           "or in the Lambda's environment.")
    return config.USAGE_TABLE


def _key(kind: str, day: date) -> dict:
    return {"pk": {"S": f"{kind}#{day.isoformat()}"}}


def _expires(day: date) -> str:
    end = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=config.USAGE_KEEP_DAYS)
    return str(int(end.timestamp()))


def _condition_failed(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def take(kind: str, limit: int, day: date | None = None, client=None) -> int | None:
    """Count one against the cap of `day` (default today, UTC): the new count, or None when `limit` is already
    reached. One conditional update, so two requests at the same moment cannot both take the last place."""
    day, client = day or today(), client or _client()
    try:
        response = client.update_item(
            TableName=_table(), Key=_key(kind, day),
            UpdateExpression="ADD n :one SET expires_at = :expires",
            ConditionExpression="attribute_not_exists(n) OR n < :limit",
            ExpressionAttributeValues={":one": {"N": "1"}, ":limit": {"N": str(limit)},
                                       ":expires": {"N": _expires(day)}},
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as error:
        if _condition_failed(error):
            return None
        raise
    return int(response["Attributes"]["n"]["N"])


def release(kind: str, day: date | None = None, client=None) -> None:
    """Give back one place of the cap of `day` (default today, UTC), for a request refused before an answer was
    paid for. Never goes below 0."""
    day, client = day or today(), client or _client()
    try:
        client.update_item(
            TableName=_table(), Key=_key(kind, day),
            UpdateExpression="ADD n :minus_one",
            ConditionExpression="n > :zero",
            ExpressionAttributeValues={":minus_one": {"N": "-1"}, ":zero": {"N": "0"}},
        )
    except ClientError as error:
        if not _condition_failed(error):
            raise


def count(kind: str, day: date | None = None, client=None) -> int:
    """How many the cap of `day` (default today, UTC) has counted."""
    day, client = day or today(), client or _client()
    item = client.get_item(TableName=_table(), Key=_key(kind, day), ConsistentRead=True).get("Item")
    return int(item["n"]["N"]) if item and "n" in item else 0
