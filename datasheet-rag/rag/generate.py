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

# Added only when a reviewer's note is among the passages (step 9b), so every other prompt stays as it was.
_NOTE_RULE = """6. A passage labelled "Verified note" is a reviewer's correction, checked against the datasheet page it names. \
If it answers the question, answer from it and cite it; where it disagrees with another passage, follow the note.
7."""

# [3], [1, 3] or [1-3]. A label glued to a word may be a footnote marker copied from a passage (GPIO16[2]).
_LABELS = re.compile(r"\[\s*(\d+(?:\s*(?:,|-|–)\s*\d+)*)\s*\]")
_GLUED_WORD = re.compile(r"\w+$")
_OK_STOPS = ("end_turn", "max_tokens")


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION,
                               config=Config(retries={"max_attempts": config.BEDROCK_MAX_ATTEMPTS, "mode": "adaptive"},
                                             connect_timeout=config.BEDROCK_CONNECT_TIMEOUT_S,
                                             read_timeout=config.BEDROCK_READ_TIMEOUT_S))
    return _client


def is_note(hit: dict) -> bool:
    """A reviewer's note (step 9b): a passage of kind "note", or a source that cites one."""
    return hit.get("kind") == "note" or bool(hit.get("note"))


def passage_label(hit: dict) -> str:
    """Which datasheet and PDF page a passage is from, with the printed page number when it differs. A reviewer's
    note says so first: "Verified note: LM35 (lm35), page 4"."""
    part = config.DOCUMENTS.get(hit["document"], {}).get("part")
    document = hit["document"] or "unknown datasheet"  # a Knowledge Base passage may come back without one
    if document.startswith(config.UPLOAD_PREFIX):
        part = "Uploaded PDF"  # a visitor's upload (step 9a); its file name is the visitor's, so it is not shown here
    label = f"{part} ({document})" if part else document
    if is_note(hit):
        label = "Verified note: " + label
    label += f", page {hit['page'] if hit['page'] is not None else 'unknown'}"
    if hit.get("page_label") and str(hit["page_label"]) != str(hit["page"]):
        label += f", printed page {hit['page_label']}"
    return label


def build_context(chunks: list[dict]) -> str:
    """The passages, numbered from 1: a label line, then the chunk's full text, a blank line between passages."""
    return "\n\n".join(f"[{n}] {passage_label(hit)}\n{hit['text']}" for n, hit in enumerate(chunks, start=1))


def system_prompt(notes: bool = False) -> str:
    """The rules, with the 'not contained' sentence as config gives it now. With `notes`, the rule on reviewers'
    notes comes before the last rule; without, the prompt is the same as before step 9b."""
    rules = _RULES.replace("\n6. ", "\n" + _NOTE_RULE + " ", 1) if notes else _RULES
    return rules + config.NOT_CONTAINED


def build_prompt(question: str, chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """The Converse `system` and `messages`: the rules, then one user message with the passages and the question."""
    user = f"Passages:\n\n{build_context(chunks)}\n\nQuestion: {question}"
    system = system_prompt(notes=any(is_note(hit) for hit in chunks))
    return [{"text": system}], [{"role": "user", "content": [{"text": user}]}]


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
                source = {"label": label, "id": hit.get("id"), "document": hit["document"], "page": hit["page"],
                          "page_label": hit.get("page_label")}
                if is_note(hit):
                    source["note"] = True
                sources.append(source)
    return {"sources": sources, "invalid": sorted(invalid)}


def citation_spans(answer: str, chunks: list[dict]) -> list[tuple[int, int, list[int]]]:
    """Where the answer cites passages, by the rules of `resolve_citations`: (start, end, labels) for each bracket
    that names at least one existing passage. Copied footnote markers (GPIO16[2]) and invalid labels are left out,
    so a page that links these spans shows exactly the citations counted here."""
    spans = []
    for match in _LABELS.finditer(answer):
        if _copied_footnote(answer, match, chunks):
            continue
        labels = [label for label in dict.fromkeys(_labels(match.group(1), len(chunks))) if 1 <= label <= len(chunks)]
        if labels:
            spans.append((match.start(), match.end(), labels))
    return spans


def _words(text: str) -> str:
    """Lower case, single spaces, and a curly apostrophe as a straight one (models write both: "don’t")."""
    return " ".join(text.lower().replace("’", "'").split())


# A refusal in the model's own words (seen from Nova Lite in Layer 7: "Sorry, I cannot provide information about the
# unit price ... as it is not mentioned in the provided datasheets."). Both must match: a phrase saying something is
# missing, and a word naming what was read. Either alone is too loose: "does not require calibration" is a fact, and
# "the datasheet gives 35 V" is an answer. The two share no word, so one phrase can never satisfy both.
_MISSING = re.compile(
    r"\b(?:cannot|can't|can not|unable to) (?:be )?(?:provide|find|answer|determine|found|answered|determined)"
    r"|\b(?:do|does|did)(?: not|n't) (?:contain|mention|specify|include|list|give|provide|state)"
    r"|(?:\bnot|n't) (?:mentioned|specified|provided|included|listed|stated|given|found|available)\b"
    r"|\bno (?:information|mention|data|details)\b"
)
# "context" only as the model's name for the passages ("in the given context", 9c u4 on the KB pipeline), not "in
# this context", which ordinary answers use.
_SOURCE_MATERIAL = re.compile(r"\b(?:datasheets?|passages?|documents?)\b|\b(?:the|given|provided) context\b")


def is_not_contained(answer: str, sources: list[dict] | None = None) -> bool:
    """True when the answer is a refusal:

    - it is the fixed 'not contained' sentence with only labels after it ("... information. [1] [2]", seen from
      Nova Lite in Layer 7); or
    - it cites nothing and gives the fixed sentence anywhere; or
    - it cites nothing and refuses in other words: it says something is missing (`_MISSING`) and refers to the
      datasheets or passages (`_SOURCE_MATERIAL`).

    Case, spacing and the final full stop do not matter. An answer that cites a passage is an answer, whatever else
    it says ("the datasheet does not specify a maximum; typical is 0.5 °C [1]")."""
    sentence = _words(config.NOT_CONTAINED).rstrip(".")
    if _words(_LABELS.sub(" ", answer)).rstrip(" .") == sentence:
        return True
    if sources:
        return False
    text = _words(answer)
    return sentence in text or bool(_MISSING.search(text) and _SOURCE_MATERIAL.search(text))


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
