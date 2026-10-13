"""Settings for the pipeline: region, model IDs, retrieval sizes, paths."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdfs"
RUNS_DIR = ROOT / "runs"
COST_LOG = RUNS_DIR / "cost_log.csv"
PARSED_DIR = RUNS_DIR / "parsed"


def _load_env(path: Path) -> None:
    """Read KEY=VALUE lines from .env into os.environ. Existing variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env(ROOT / ".env")

AWS_REGION = "us-east-1"

# Embeddings. Request and response fields checked against the Bedrock user guide on 2026-10-11.
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBED_DIMENSIONS = 1024
EMBED_NORMALIZE = True
# Verified 2026-10-13 on the Bedrock pricing page: $0.02 per 1M input tokens (on-demand).
EMBED_USD_PER_1K_TOKENS = 0.00002

# Retrieval
N_CANDIDATES = 30
TOP_K = 5

# Local database (Cycle 1). The password comes from .env or the environment only.
PG_HOST = os.environ.get("PGHOST", "localhost")
PG_PORT = int(os.environ.get("PGPORT", "5432"))
PG_USER = os.environ.get("PGUSER", "postgres")
PG_DATABASE = os.environ.get("PGDATABASE", "postgres")
PG_PASSWORD = os.environ.get("PGPASSWORD", "")
