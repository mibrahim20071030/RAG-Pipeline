"""Titan Text Embeddings V2 calls."""
import hashlib
import json
import sys
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor

import boto3
from botocore.config import Config

from rag import config
from rag.costlog import log_cost

_client = None


def _bedrock():
    global _client
    if _client is None:
        # Adaptive retries slow the calls down when Bedrock throttles, and try again.
        _client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION,
                               config=Config(retries={"max_attempts": 10, "mode": "adaptive"}))
    return _client


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_key() -> str:
    """Names the cache entries of one model setup: a change of model or normalisation starts a new set.

    A change of EMBED_DIMENSIONS also needs new tables: the vector columns are created with a fixed size.
    """
    return f"{config.EMBED_MODEL_ID}|{config.EMBED_DIMENSIONS}|normalize={config.EMBED_NORMALIZE}"


def _invoke(text: str) -> tuple[list[float], int]:
    """One paid call: the vector and the input token count. The model takes one text per request."""
    body = json.dumps({
        "inputText": text,
        "dimensions": config.EMBED_DIMENSIONS,
        "normalize": config.EMBED_NORMALIZE,
    })
    response = _bedrock().invoke_model(
        modelId=config.EMBED_MODEL_ID,
        body=body,
        accept="application/json",
        contentType="application/json",
    )
    result = json.loads(response["body"].read())
    return result["embedding"], result["inputTextTokenCount"]


def _log(operation: str, tokens: int) -> None:
    log_cost(operation, config.EMBED_MODEL_ID, "input_tokens", tokens,
             tokens / 1000 * config.EMBED_USD_PER_1K_TOKENS)


def embed_text(text: str) -> list[float]:
    """Embed one string. Logs the input token count to the cost log."""
    vector, tokens = _invoke(text)
    _log("embed", tokens)
    return vector


def embed_texts(texts: list[str], label: str = "", workers: int | None = None) -> list[list[float]]:
    """Embed many strings, a few calls at a time. Vectors come back in the order of `texts`.

    The batch writes one cost log row with its token total (`label` names the batch). If a call fails, the
    calls not yet started are dropped, the tokens of the ones that finished are still logged, and the error
    is raised.
    """
    if not texts:
        return []
    operation = f"embed_batch:{label}" if label else "embed_batch"
    _bedrock()  # made here, once: creating a boto3 client from several threads at the same time is not safe
    failed = threading.Event()

    def call(text: str) -> tuple[list[float], int]:
        # A worker can pick up its next text before the failure is noticed, so each call checks first.
        if failed.is_set():
            raise CancelledError
        try:
            return _invoke(text)
        except BaseException:
            failed.set()
            raise

    with ThreadPoolExecutor(max_workers=workers or config.EMBED_WORKERS) as pool:
        futures = [pool.submit(call, text) for text in texts]
        try:
            return [future.result()[0] for future in futures]
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            finished = [f for f in futures if f.done() and not f.cancelled() and f.exception() is None]
            if finished:
                tokens = sum(f.result()[1] for f in finished)
                try:
                    _log(operation, tokens)
                except OSError as error:
                    # The vectors are paid for; a cost log that cannot be written must not throw them away.
                    print(f"WARNING: cost log not written ({error}). Add by hand: {operation}, {tokens} input tokens.",
                          file=sys.stderr)
