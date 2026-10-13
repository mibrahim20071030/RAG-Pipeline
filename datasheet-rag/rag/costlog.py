"""Append one row per paid Bedrock call to runs/cost_log.csv."""
import csv
from datetime import UTC, datetime

from rag import config

FIELDS = ["timestamp", "operation", "model_id", "unit", "quantity", "est_cost_usd"]


def read_rows() -> list[dict]:
    """Every row of the cost log, oldest first. Empty when the file does not exist yet."""
    if not config.COST_LOG.exists():
        return []
    with config.COST_LOG.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def log_cost(operation: str, model_id: str, unit: str, quantity: int, est_cost_usd: float) -> None:
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
