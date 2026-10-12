"""Titan Text Embeddings V2 calls."""
import json

import boto3

from rag import config
from rag.costlog import log_cost

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
    return _client


def embed_text(text: str) -> list[float]:
    """Embed one string. Logs the input token count to the cost log."""
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
    tokens = result["inputTextTokenCount"]
    log_cost("embed", config.EMBED_MODEL_ID, "input_tokens", tokens,
             tokens / 1000 * config.EMBED_USD_PER_1K_TOKENS)
    return result["embedding"]
