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
EMBED_WORKERS = 4  # calls in flight at once; the model takes one text per request
EMBED_BATCH = 100  # texts per batch: one cost log row, and vectors are saved after each batch

# The datasheets, by parsed name. `revision` is what the PDF prints on its first page.
DOCUMENTS = {
    "DS_CD00237391": {"part": "STM32F205/207", "revision": "DS6329 Rev 18"},
    "TCTH022BE_datasheet_en_20230425": {"part": "TCTH0xxxE", "revision": "Rev. 2.0.A"},
    "esp32_datasheet_en": {"part": "ESP32", "revision": "Version 5.3"},
    "lm35": {"part": "LM35", "revision": "SNIS159H"},
}

# Chunking. Size is counted in approximate tokens: characters divided by CHARS_PER_TOKEN, rounded up.
CHARS_PER_TOKEN = 4
# name -> size limit in tokens (None: no limit), overlap in tokens, and rerank_limited: True when a chunk on its
# own can be longer than the reranker reads. A 400-token chunk can still pass that limit by the length of the
# question, and the token measure is approximate, so Layer 5 truncates for reranking whatever the flag says.
CHUNK_STRATEGIES = {
    "page": {"size": None, "overlap": 0, "rerank_limited": True},
    "fixed_256": {"size": 256, "overlap": 0, "rerank_limited": False},
    "fixed_256_overlap": {"size": 256, "overlap": 50, "rerank_limited": False},
    "fixed_400": {"size": 400, "overlap": 0, "rerank_limited": False},
    "fixed_400_overlap": {"size": 400, "overlap": 80, "rerank_limited": False},
    "structure_aware": {"size": 400, "overlap": 0, "rerank_limited": False},
    "fixed_1024": {"size": 1024, "overlap": 0, "rerank_limited": True},
}
RERANK_MAX_TOKENS = 400  # a chunk sent to the reranker should stay at or below this, question included
CHUNK_COUNTS = RUNS_DIR / "chunk_counts.csv"

# Retrieval
N_CANDIDATES = 30
TOP_K = 5
DEFAULT_STRATEGY = "fixed_400"  # the chunking strategy searched when none is named; Layer 8 picks the final one
KEYWORD_CONFIGS = ("english", "simple")  # the two keyword columns: tsv_english (stemmed) and tsv_simple (as written)
KEYWORD_CONFIG = "english"
RRF_C = 60
# Experiment, off in the baseline: limit a search to the datasheet the question names.
DETECT_PART = False
# document name -> lower-case prefixes; a question word that starts with one names that datasheet.
PART_ALIASES = {
    "DS_CD00237391": ("stm32f20",),
    "TCTH022BE_datasheet_en_20230425": ("tcth0",),
    "esp32_datasheet_en": ("esp32",),
    "lm35": ("lm35",),
}

# Reranking. API, model ARN and price checked against the Bedrock docs and pricing page on 2026-10-13.
RERANK_MODEL_ID = "cohere.rerank-v3-5:0"
RERANK_ENABLED = True
RERANK_MAX_DOCS = 100  # documents per call: one billed query covers up to 100
RERANK_MIN_INTERVAL_S = 20  # seconds between calls: the account's quota is 3 requests a minute
RERANK_USD_PER_QUERY = 0.002  # $2.00 per 1,000 queries

# Local database (Cycle 1). The password comes from .env or the environment only.
PG_HOST = os.environ.get("PGHOST", "localhost")
PG_PORT = int(os.environ.get("PGPORT", "5432"))
PG_USER = os.environ.get("PGUSER", "postgres")
PG_DATABASE = os.environ.get("PGDATABASE", "postgres")
PG_PASSWORD = os.environ.get("PGPASSWORD", "")
