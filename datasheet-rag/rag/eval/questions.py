"""The hand-checked question set: one JSON object per line of `data/questions.jsonl`.

A question has `id`, `question`, `expected_answer`, `sources` (a list of `[document, page]` pairs, `page` being the
1-based PDF page position), `type` and an optional `notes`. A `no_answer` question, and only that type, has no sources.
`load_questions` checks every line and raises ValueError naming the line when something is wrong.
"""
import json
from pathlib import Path

from rag import config, parse

TYPES = ("single_value", "table_lookup", "comparison", "rephrased", "no_answer")
REQUIRED = ("id", "question", "expected_answer", "sources", "type")


def load_questions(path: Path | str | None = None) -> list[dict]:
    """The questions in file order, each `sources` as a list of (document, page) tuples. Blank lines are skipped."""
    path = Path(config.QUESTIONS if path is None else path)
    questions, seen, page_counts = [], set(), {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        question = _parse_line(line, number)
        if question["id"] in seen:
            raise ValueError(f"line {number}: id {question['id']!r} is used by an earlier question")
        seen.add(question["id"])
        question["sources"] = [_check_source(source, number, page_counts) for source in question["sources"]]
        _check_type(question, number)
        questions.append(question)
    return questions


def _parse_line(line: str, number: int) -> dict:
    """One line as a dict with every required field, or ValueError."""
    try:
        question = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"line {number}: not valid JSON ({error.msg})") from None
    if type(question) is not dict:
        raise ValueError(f"line {number}: a question must be a JSON object")
    missing = [field for field in REQUIRED if field not in question]
    if missing:
        raise ValueError(f"line {number}: missing field(s) {', '.join(missing)}")
    if type(question["id"]) is not str or not question["id"]:
        raise ValueError(f"line {number}: id must be a non-empty string")
    if type(question["sources"]) is not list:
        raise ValueError(f"line {number}: sources must be a list of [document, page] pairs")
    return question


def _check_type(question: dict, number: int) -> None:
    """The type is known, and only a `no_answer` question has no sources."""
    kind = question["type"]
    if kind not in TYPES:
        raise ValueError(f"line {number}: unknown type {kind!r}; known types are {', '.join(TYPES)}")
    if kind == "no_answer" and question["sources"]:
        raise ValueError(f"line {number}: a no_answer question must have no sources")
    if kind != "no_answer" and not question["sources"]:
        raise ValueError(f"line {number}: a {kind} question needs at least one source")


def _check_source(source, number: int, page_counts: dict) -> tuple[str, int]:
    """One source as a (document, page) tuple, or ValueError naming what is wrong with it."""
    if not isinstance(source, (list, tuple)) or len(source) != 2:
        raise ValueError(f"line {number}: source {source!r} is not a [document, page] pair")
    document, page = source
    if type(document) is not str or document not in config.DOCUMENTS:
        raise ValueError(f"line {number}: unknown document {document!r}; known are {', '.join(config.DOCUMENTS)}")
    if type(page) is not int:  # a bool is not a page, and the contract asks for ValueError, not TypeError
        raise ValueError(f"line {number}: page {page!r} of {document} is not a whole number")
    if page < 1:
        raise ValueError(f"line {number}: page {page} of {document} is below 1 (pages count from 1)")
    count = _page_count(document, page_counts)
    if count is not None and page > count:
        raise ValueError(f"line {number}: page {page} is past the end of {document}, which has {count} pages")
    return document, page


def _page_count(document: str, page_counts: dict) -> int | None:
    """Pages in the parsed document, read once per load; None when it has not been parsed."""
    if document not in page_counts:
        exists = parse.parsed_path(document).exists()
        page_counts[document] = len(parse.load_parsed(document)["pages"]) if exists else None
    return page_counts[document]
