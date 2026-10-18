"""Append one row per paid Bedrock call to runs/cost_log.csv."""
import csv
import json
from datetime import UTC, datetime

from rag import config

FIELDS = ["timestamp", "operation", "model_id", "unit", "quantity", "est_cost_usd"]


def read_rows() -> list[dict]:
    """Every row of the cost log, oldest first. Empty when the file does not exist yet."""
    if not config.COST_LOG.exists():
        return []
    with config.COST_LOG.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def row_count() -> int | None:
    """The number of rows in the cost log; None when it cannot be read (for example, open in Excel)."""
    try:
        return len(read_rows())
    except (OSError, ValueError):
        return None


def spent_since(logged_before: int | None) -> float | None:
    """Cost of the rows added since the log had `logged_before` rows; None when either count could not be read.
    Rows another process writes meanwhile (an eval run in a second terminal) are counted too."""
    if logged_before is None:
        return None
    try:
        return sum((float(row["est_cost_usd"]) for row in read_rows()[logged_before:]), 0.0)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def log_cost(operation: str, model_id: str, unit: str, quantity: int, est_cost_usd: float) -> None:
    if config.DEPLOYED:
        # Lambda's cost log is in /tmp and goes with the container; CloudWatch keeps this line.
        print(json.dumps({"cost": {"operation": operation, "model_id": model_id, "unit": unit, "quantity": quantity,
                                   "est_cost_usd": round(est_cost_usd, 8)}}), flush=True)
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not config.COST_LOG.exists()
    with config.COST_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "operation": operation,
            "model_id": model_id,
            "unit": unit,
            "quantity": quantity,
            "est_cost_usd": f"{est_cost_usd:.8f}",
        })
