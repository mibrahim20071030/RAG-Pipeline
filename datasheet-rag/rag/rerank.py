"""Reranking: Cohere Rerank 3.5 scores each (question, chunk) pair, and the best k hits are kept.

Every score that was paid for is kept in `rerank_cache`, keyed by the question, the text that was sent and the
model setup, so a pair is sent once. The account allows 3 rerank calls a minute, so the calls are spaced out.

An argument left as None takes its value from `rag/config.py` when the function is called.
"""
import math
import sys
import time

import boto3
from botocore.config import Config

from rag import config, db, embed, search
from rag.chunk_base import approx_tokens
from rag.costlog import log_cost

_client = None
_last_call: float | None = None  # clock reading of the last request, for the spacing


def _bedrock():
    global _client
    if _client is None:
        # The Rerank API is on the agent runtime endpoint. Adaptive retries try again when Bedrock throttles.
        _client = boto3.client("bedrock-agent-runtime", region_name=config.AWS_REGION,
                               config=Config(retries={"max_attempts": config.BEDROCK_MAX_ATTEMPTS, "mode": "adaptive"},
                                             connect_timeout=config.BEDROCK_CONNECT_TIMEOUT_S,
                                             read_timeout=config.BEDROCK_READ_TIMEOUT_S))
    return _client


def model_arn() -> str:
    return f"arn:aws:bedrock:{config.AWS_REGION}::foundation-model/{config.RERANK_MODEL_ID}"


def rerank_key() -> str:
    """Names the cache entries of one model. The truncation needs no place here: the sent text is what is hashed."""
    return config.RERANK_MODEL_ID


def rerank_text(question: str, text: str) -> str:
    """The chunk text as it is sent: cut so that question and text together stay within RERANK_MAX_TOKENS."""
    # A question that fills the limit on its own still leaves room for one token of the chunk.
    tokens = max(config.RERANK_MAX_TOKENS - approx_tokens(question), 1)
    return text[:tokens * config.CHARS_PER_TOKEN]


def _lookup(conn, question: str, candidates: list[dict]) -> tuple[list[str], dict[str, str], dict[str, float]]:
    """Per candidate the hash of its sent text, then hash -> sent text (each once) and hash -> cached score."""
    sent = [rerank_text(question, candidate["text"]) for candidate in candidates]
    hashes = [embed.text_hash(text) for text in sent]
    texts = dict(zip(hashes, sent, strict=True))
    scores = db.cached_rerank_scores(conn, embed.text_hash(question), list(texts), rerank_key())
    return hashes, texts, scores


def uncached_count(conn, question: str, candidates: list[dict]) -> int:
    """The number of documents `rerank` would have to send: the different texts with no stored score yet."""
    if not candidates or not search.query_terms(question):
        return 0
    _, texts, scores = _lookup(conn, question, candidates)
    return sum(1 for key in texts if key not in scores)


def calls_needed(conn, question: str, candidates: list[dict]) -> int:
    """The number of paid calls `rerank` would make. Each one is one billed query."""
    return math.ceil(uncached_count(conn, question, candidates) / config.RERANK_MAX_DOCS)


def _wait(sleep, clock) -> None:
    """Hold the request back until RERANK_MIN_INTERVAL_S has passed since the last one."""
    if _last_call is not None:
        remaining = config.RERANK_MIN_INTERVAL_S - (clock() - _last_call)
        if remaining > 0:
            sleep(remaining)


def _scores(client, question: str, texts: list[str], sleep, clock) -> list[float]:
    """One paid query: the relevance score of each text, in the order of `texts`."""
    request = {
        "queries": [{"type": "TEXT", "textQuery": {"text": question}}],
        "sources": [
            {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": text}}}
            for text in texts
        ],
        "rerankingConfiguration": {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": model_arn()},
                "numberOfResults": len(texts),  # every score comes back, not only the best few
            },
        },
    }
    global _last_call
    scores: dict[int, float] = {}
    logged = False
    while True:
        _wait(sleep, clock)
        try:
            response = client.rerank(**request)
        finally:
            # Stamped when the request ends, so time spent in retries is not counted as waiting.
            _last_call = clock()
        if not logged:
            # Logged as soon as an answer is in: the query is paid for even if what follows fails.
            _log()
            logged = True
        for result in response["results"]:
            scores[result["index"]] = float(result["relevanceScore"])
        if not response.get("nextToken"):
            break
        request["nextToken"] = response["nextToken"]
    if sorted(scores) != list(range(len(texts))):
        raise RuntimeError(f"the reranker scored {len(scores)} of {len(texts)} documents")
    return [scores[index] for index in range(len(texts))]


def _log() -> None:
    try:
        log_cost("rerank", config.RERANK_MODEL_ID, "queries", 1, config.RERANK_USD_PER_QUERY)
    except OSError as error:
        # The scores are paid for; a cost log that cannot be written must not throw them away.
        print(f"WARNING: cost log not written ({error}). Add by hand: rerank, 1 query.", file=sys.stderr)


def rerank(conn, question: str, candidates: list[dict], k: int | None = None, enabled: bool | None = None,
           client=None, sleep=time.sleep, clock=time.monotonic) -> list[dict]:
    """The best k of the candidate hits by rerank score, best first. Hits with the same score keep their order.

    A returned hit is a copy: `score` is the relevance score and `search_score` is the score it came in with.
    With the reranker switched off, the first k candidates are returned with their search score in both places,
    and nothing is called.
    """
    k = config.TOP_K if k is None else k
    if not (config.RERANK_ENABLED if enabled is None else enabled):
        return [{**hit, "search_score": hit.get("score")} for hit in candidates[:k]]
    if not candidates or not search.query_terms(question):
        return []
    hashes, texts, scores = _lookup(conn, question, candidates)
    missing = [key for key in texts if key not in scores]
    for start in range(0, len(missing), config.RERANK_MAX_DOCS):
        group = missing[start:start + config.RERANK_MAX_DOCS]
        new = dict(zip(group, _scores(client or _bedrock(), question, [texts[key] for key in group], sleep, clock),
                       strict=True))
        # Saved after each call, so a run that stops keeps what it paid for.
        db.store_rerank_scores(conn, embed.text_hash(question), rerank_key(), new)
        scores.update(new)
    order = sorted(range(len(candidates)), key=lambda index: -scores[hashes[index]])
    return [
        {**candidates[index], "score": scores[hashes[index]], "search_score": candidates[index].get("score")}
        for index in order[:k]
    ]
