"""Settings for the pipeline: region, model IDs, retrieval sizes, paths."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdfs"


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

# Outputs and the cost log. Lambda can write only under /tmp, so it sets RAG_RUNS_DIR=/tmp/runs.
RUNS_DIR = Path(os.environ.get("RAG_RUNS_DIR") or ROOT / "runs")
COST_LOG = RUNS_DIR / "cost_log.csv"
PARSED_DIR = RUNS_DIR / "parsed"

AWS_REGION = "us-east-1"
# Tries per Bedrock call, throttling retries included. Lambda sets 2, so a throttle fails fast instead of passing
# API Gateway's 30 s limit.
BEDROCK_MAX_ATTEMPTS = int(os.environ.get("BEDROCK_MAX_ATTEMPTS") or "10")
# Seconds to open a connection to Bedrock and to wait for its answer (botocore's defaults are 60). Lambda sets lower
# values, so one slow call cannot hold a request past API Gateway's limit.
BEDROCK_CONNECT_TIMEOUT_S = float(os.environ.get("BEDROCK_CONNECT_TIMEOUT_S") or "60")
BEDROCK_READ_TIMEOUT_S = float(os.environ.get("BEDROCK_READ_TIMEOUT_S") or "60")
# True inside Lambda, which sets AWS_LAMBDA_FUNCTION_NAME itself: error messages then give no local-machine hints,
# and each cost log row is also printed to the log, because the cost log in /tmp goes with the container.
DEPLOYED = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

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
# Header-prefix experiment (Layer 7): "<strategy>_prefix" stores the same chunks, embedded with
# "<part> (<document>). <section>" on a line before the text.
HEADER_PREFIX = "_prefix"
RERANK_MAX_TOKENS = 400  # a chunk sent to the reranker should stay at or below this, question included
CHUNK_COUNTS = RUNS_DIR / "chunk_counts.csv"

# Retrieval. The values marked "frozen" are the setup chosen at Layer 8 on 2026-10-14 from the Layer 7 results
# (runs/cycle1_summary.md): header-prefix chunks, hybrid search, reranker on, 10 passages to the answer model.
N_CANDIDATES = 30  # frozen: candidates from search, all sent to the reranker
TOP_K = 10  # frozen: passages kept by the reranker and given to the answer model
DEFAULT_STRATEGY = "fixed_400_prefix"  # frozen: the chunking strategy searched when none is named
SEARCH_MODE = "hybrid"  # frozen: the search mode of rag.pipeline
KEYWORD_CONFIGS = ("english", "simple")  # the two keyword columns: tsv_english (stemmed) and tsv_simple (as written)
KEYWORD_CONFIG = "english"  # frozen
RRF_C = 60
# Experiment, off (frozen): limit a search to the datasheet the question names. Not tested with the header prefix.
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
RERANK_ENABLED = True  # frozen
RERANK_MAX_DOCS = 100  # documents per call: one billed query covers up to 100
# Seconds between calls in one process: the account's quota is 3 requests a minute. Lambda sets 0, because a 20 s wait
# inside a request would pass API Gateway's limit; a throttle then comes back as a 429.
RERANK_MIN_INTERVAL_S = float(os.environ.get("RERANK_MIN_INTERVAL_S") or "20")
RERANK_USD_PER_QUERY = 0.002  # $2.00 per 1,000 queries

# Generation. Converse API fields checked against the Bedrock user guide on 2026-10-14. Price verified by the human
# on the Bedrock pricing page on 2026-10-14: $0.06 / $0.24 per 1M input / output tokens (Standard tier, on-demand).
GENERATE_MODEL_ID = "amazon.nova-lite-v1:0"
GENERATE_MAX_TOKENS = 512  # longest answer, in tokens
GENERATE_TEMPERATURE = 0.0
GENERATE_USD_PER_1K_INPUT = 0.00006
GENERATE_USD_PER_1K_OUTPUT = 0.00024
# The model is told to reply with exactly this sentence when the passages do not hold the answer.
NOT_CONTAINED = "The provided datasheets do not contain this information."

# Evaluation (Layer 7). Questions are hand-checked against the PDFs; a source is (document, PDF page).
QUESTIONS = ROOT / "data" / "questions.jsonl"
# Frozen at Layer 8: SHA-256 of the question file with line endings as "\n" (Git on Windows checks it out with "\r\n").
QUESTIONS_SHA256 = "61e509596b8cba8451d1e6837d49917d137c2687ecfe8c64486e7641518aa702"
EVAL_DIR = RUNS_DIR / "eval"  # per-question rows, one CSV per experiment
RESULTS = RUNS_DIR / "results.csv"  # one summary row per experiment and reranker state
RESULTS_BY_TYPE = RUNS_DIR / "results_by_type.csv"
EVAL_KS = (1, 3, 5, 10)  # recall@k is reported for each
EVAL_RERANK_K = 10  # the reranker keeps this many in eval, so every k above is read from one list
# Request quotas of this account (Service Quotas, 2026-10-13), for the dry run's time estimate.
EMBED_PER_MIN = 60
GENERATE_PER_MIN = 100

# RAGAS judge (Layer 7b), run in .venv-ragas (Python 3.13). Price verified by the human on the Bedrock pricing page
# on 2026-10-14: $0.80 / $3.20 per 1M input / output tokens (on-demand).
JUDGE_MODEL_ID = "amazon.nova-pro-v1:0"
JUDGE_USD_PER_1K_INPUT = 0.0008
JUDGE_USD_PER_1K_OUTPUT = 0.0032
JUDGE_PER_MIN = 13  # the account's Nova Pro quota (Service Quotas, 2026-10-13)
JUDGE_MIN_INTERVAL_S = 60 / (JUDGE_PER_MIN - 1)  # seconds between judge calls: one a minute under the quota
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 2048  # longest judge reply, in tokens: a statement list or verdict list can be long
RAGAS_DIR = RUNS_DIR / "ragas"  # per-answer judge scores, one CSV per experiment
RAGAS_RESULTS = RUNS_DIR / "results_ragas.csv"  # one summary row per experiment
RAGAS_SHORTLIST = ("gen_prefix_k10", "gen_prefix_k5", "gen_detect_part", "gen_baseline")

# Local web UI (Layer 8b): `python -m rag.server` serves web/index.html and the JSON API on this machine only.
WEB_DIR = ROOT / "web"
SERVER_HOST = "127.0.0.1"  # never 0.0.0.0: anyone who can reach the server can spend on the AWS account
SERVER_PORT = 8000
API_MAX_QUESTION_CHARS = 500
API_MAX_BODY_BYTES = 10_000
# Questions answered per UTC day before /api/ask says "try tomorrow" (Cycle 2 step 5). 0 is off; Lambda sets 300.
API_DAILY_ASK_LIMIT = int(os.environ.get("API_DAILY_ASK_LIMIT") or "0")

# Local database (Cycle 1). The password comes from .env or the environment only.
PG_HOST = os.environ.get("PGHOST", "localhost")
PG_PORT = int(os.environ.get("PGPORT", "5432"))
PG_USER = os.environ.get("PGUSER", "postgres")
PG_DATABASE = os.environ.get("PGDATABASE", "postgres")
PG_PASSWORD = os.environ.get("PGPASSWORD", "")

# Which database `db.connect()` reaches: "local" (the Docker Postgres above) or "aurora" (Cycle 2).
DB_TARGET = os.environ.get("DB_TARGET", "local")

# Aurora (Cycle 2). Identifiers from .env, not secrets: express clusters log in with IAM tokens.
AURORA_HOST = os.environ.get("AURORA_HOST", "")
AURORA_PORT = int(os.environ.get("AURORA_PORT") or "5432")
AURORA_DATABASE = os.environ.get("AURORA_DATABASE", "postgres")
AURORA_USER = os.environ.get("AURORA_USER", "rag_app")
# A resume after auto-pause takes about 15 s, 30 s or more after a day (AWS docs, checked 2026-10-16).
AURORA_CONNECT_TIMEOUT_S = int(os.environ.get("AURORA_CONNECT_TIMEOUT_S") or "60")
AURORA_CONNECT_ATTEMPTS = int(os.environ.get("AURORA_CONNECT_ATTEMPTS") or "3")
# A refused connection while the cluster wakes fails at once; wait before the next try.
AURORA_RETRY_PAUSE_S = float(os.environ.get("AURORA_RETRY_PAUSE_S") or "5")
# Worst case here is 3 x 60 s, well past API Gateway's 30 s, so Lambda sets one attempt with a shorter timeout.
# CA bundle the server certificate is checked against. Empty means the one that ships with botocore: libpq's
# "system" store is empty on Windows, and botocore is in the Lambda runtime too.
AURORA_CA_BUNDLE = os.environ.get("AURORA_CA_BUNDLE", "")
