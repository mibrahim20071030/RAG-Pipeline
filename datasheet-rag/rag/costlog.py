"""Append one row per paid Bedrock call to runs/cost_log.csv."""
import csv
from datetime import datetime, timezone

from rag import config

FIELDS = ["timestamp", "operation", "model_id", "unit", "quantity", "est_cost_usd"]


def log_cost(operation: str, model_id: str, unit: str, quantity: int, est_cost_usd: float) -> None:
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not config.COST_LOG.exists()
    with config.COST_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operation": operation,
            "model_id": model_id,
            "unit": unit,
            "quantity": quantity,
            "est_cost_usd": f"{est_cost_usd:.8f}",
        })
