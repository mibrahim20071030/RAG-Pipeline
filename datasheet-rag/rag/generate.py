"""Generation: Nova Lite answers from numbered passages and cites them as [n]; our code maps [n] back to a page.

The model only repeats labels. Which chunk a label means is decided here, from the list the passages were built
from, and every label is checked.

An argument left as None takes its value from `rag/config.py` when the function is called.
"""
import re
import sys

import boto3
from botocore.config import Config

from rag import config
from rag.costlog import log_cost

_client = None

_RULES = """You answer questions about electronic component datasheets.

Rules:
1. Answer only from the numbered passages in the user message. Do not use outside knowledge.
2. After each claim, cite the passage it comes from as [n], with a space before it. Cite several passages as [1] [3].
3. Give values with their units and conditions exactly as the passage writes them.
4. If passages disagree, say so and cite each of them.
5. Read every passage, tables included, before deciding. If a passage gives the value or fact asked for, even in
a table row or in other words than the question, answer from it.
6. Only if no passage answers the question, reply with exactly this sentence, with no citation and nothing else:
"""

# [3], [1, 3] or [1-3]. A label glued to a word may be a footnote marker copied from a passage (GPIO16[2]).
_LABELS = re.compile(r"\[\s*(\d+(?:\s*(?:,|-|–)\s*\d+)*)\s*\]")
_GLUED_WORD = re.compile(r"\w+$")
_OK_STOPS = ("end_turn", "max_tokens")


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION,
                               config=Config(retries={"max_attempts": 10, "mode": "adaptive"}))
    return _client


def passage_label(hit: dict) -> str:
    """Which datasheet and PDF page a passage is from, with the printed page number when it differs."""
    part = config.DOCUMENTS.get(hit["document"], {}).get("part")
    label = f"{part} ({hit['document']})" if part else hit["document"]
    label += f", page {hit['page']}"
    if hit.get("page_label") and str(hit["page_label"]) != str(hit["page"]):
        label += f", printed page {hit['page_label']}"
    return label


def build_context(chunks: list[dict]) -> str:
    """The passages, numbered from 1: a label line, then the chunk's full text, a blank line between passages."""
    return "\n\n".join(f"[{n}] {passage_label(hit)}\n{hit['text']}" for n, hit in enumerate(chunks, start=1))


def system_prompt() -> str:
    """The rules, with the 'not contained' sentence as config gives it now."""
    return _RULES + config.NOT_CONTAINED


def build_prompt(question: str, chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """The Converse `system` and `messages`: the rules, then one user message with the passages and the question."""
    user = f"Passages:\n\n{build_context(chunks)}\n\nQuestion: {question}"
    return [{"text": system_prompt()}], [{"role": "user", "content": [{"text": user}]}]


def _log(input_tokens: int, output_tokens: int) -> None:
    try:
        model = config.GENERATE_MODEL_ID
        log_cost("generate", model, "input_tokens", input_tokens, input_tokens / 1000 * config.GENERATE_USD_PER_1K_INPUT)
        log_cost("generate", model, "output_tokens", output_tokens,
                 output_tokens / 1000 * config.GENERATE_USD_PER_1K_OUTPUT)
    except OSError as error:
        # The answer is paid for; a cost log that cannot be written must not throw it away.
        print(f"WARNING: cost log not written ({error}). Add by hand: generate, {input_tokens} input and "
              f"{output_tokens} output tokens.", file=sys.stderr)


def call_llm(system: list[dict], messages: list[dict], max_tokens: int | None = None,
             temperature: float | None = None, client=None) -> dict:
    """One paid Converse call. Returns the answer text, token counts, stop reason and the model's latency."""
    response = (client or _bedrock()).converse(
        modelId=config.GENERATE_MODEL_ID,
        system=system,
        messages=messages,
        inferenceConfig={
            "maxTokens": config.GENERATE_MAX_TOKENS if max_tokens is None else max_tokens,
            "temperature": config.GENERATE_TEMPERATURE if temperature is None else temperature,
        },
    )
    usage = response.get("usage", {})
    input_tokens, output_tokens = int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))
    _log(input_tokens, output_tokens)  # before any check below, so a paid call is always logged
    stop = response.get("stopReason")
    if stop not in _OK_STOPS:
        raise RuntimeError(f"the model stopped with {stop!r}, not with an answer")
    text = "".join(block.get("text", "") for block in response["output"]["message"]["content"]).strip()
    return {
        "text": text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "stop_reason": stop,
        "latency_ms": response.get("metrics", {}).get("latencyMs"),
    }


def _copied_footnote(answer: str, match: re.Match, chunks: list[dict]) -> bool:
    """True when the brackets are glued to a word and that word with these brackets is in a passage (GPIO16[2]).
    A glued label that no passage has, such as 25 mA[1], is a citation."""
    word = _GLUED_WORD.search(answer, 0, match.start())
    if word is None or word.end() != match.start():
        return False
    glued = word.group(0) + match.group(0)
    return any(glued in hit["text"] for hit in chunks)


def _labels(group: str, count: int) -> list[int]:
    """The numbers of one bracket, ranges written out. A range's numbers past the passages count once, as its end."""
    labels = []
    for part in group.split(","):
        ends = [int(end) for end in re.split(r"-|–", part)]
        if len(ends) == 2 and ends[0] <= ends[1]:
            labels += list(range(ends[0], min(ends[1], count) + 1))
            if ends[1] > count:
                labels.append(ends[1])
        else:
            labels += ends  # a plain number, or a backwards or odd range: each number as written
    return labels


def resolve_citations(answer: str, chunks: list[dict]) -> dict:
    """Map each [n] to chunks[n - 1]. `sources`: each valid label once, in order of first use. `invalid`: the rest."""
    sources, seen, invalid = [], set(), set()
    for match in _LABELS.finditer(answer):
        if _copied_footnote(answer, match, chunks):
            continue
        for label in _labels(match.group(1), len(chunks)):
            if not 1 <= label <= len(chunks):
                invalid.add(label)
            elif label not in seen:
                seen.add(label)
                hit = chunks[label - 1]
                sources.append({"label": label, "id": hit.get("id"), "document": hit["document"],
                                "page": hit["page"], "page_label": hit.get("page_label")})
    return {"sources": sources, "invalid": sorted(invalid)}


def _words(text: str) -> str:
    return " ".join(text.lower().split())


def is_not_contained(answer: str, sources: list[dict] | None = None) -> bool:
    """True when the answer gives the fixed 'not contained' sentence and cites nothing, or is that sentence with
    only labels after it ("... information. [1] [2]", seen from Nova Lite in Layer 7). Case, spacing and the final
    full stop do not matter. An answer that also says something else and cites a passage is an answer."""
    sentence = _words(config.NOT_CONTAINED).rstrip(".")
    if _words(_LABELS.sub(" ", answer)).rstrip(" .") == sentence:
        return True
    if sources:
        return False
    return sentence in _words(answer)


def generate(question: str, chunks: list[dict], client=None) -> dict:
    """Build the prompt, call the model, check the citations. With no passages, nothing is called."""
    if not chunks:
        reply = {"text": config.NOT_CONTAINED, "input_tokens": 0, "output_tokens": 0, "stop_reason": None,
                 "latency_ms": None}
    else:
        system, messages = build_prompt(question, chunks)
        reply = call_llm(system, messages, client=client)
    citations = resolve_citations(reply["text"], chunks)
    not_contained = is_not_contained(reply["text"], citations["sources"])
    if not_contained:
        citations["sources"] = []  # labels after the refusal sentence cite nothing; no page is shown for them
    return {
        "answer": reply["text"],
        **citations,
        "not_contained": not_contained,
        "cut_off": reply["stop_reason"] == "max_tokens",
        "input_tokens": reply["input_tokens"],
        "output_tokens": reply["output_tokens"],
        "stop_reason": reply["stop_reason"],
        "latency_ms": reply["latency_ms"],
    }
