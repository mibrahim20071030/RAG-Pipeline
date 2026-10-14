"""Layer 7 tests for loading and checking the question file. Written from the layer's contract, not from the code.

Every file here is a small hand-made one in a temporary folder. Only the page-count checks read a parsed datasheet,
and the one that needs it skips itself when `runs/parsed/lm35.json` is missing (as on GitHub).
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

from rag import config, parse
from rag.eval import questions

LM35_PARSED = parse.parsed_path("lm35").exists()


def entry(**fields) -> dict:
    """One valid answerable question; `fields` replace or add keys."""
    base = {"id": "q01", "question": "What is the LM35 supply voltage range?", "expected_answer": "4 V to 30 V",
            "sources": [["lm35", 4]], "type": "single_value"}
    return {**base, **fields}


def no_answer(**fields) -> dict:
    base = {"id": "q99", "question": "What is the price of the LM35?", "expected_answer": "Not in the datasheets.",
            "sources": [], "type": "no_answer"}
    return {**base, **fields}


def write(tmp_path, *lines) -> Path:
    """Write a question file. A dict is written as JSON, a string as it is."""
    path = tmp_path / "questions.jsonl"
    text = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def fails_on_line(tmp_path, bad, line: int = 3):
    """Write two valid lines and then `bad` on line 3; the error must name line 3."""
    path = write(tmp_path, entry(id="a1"), entry(id="a2"), bad)
    with pytest.raises(ValueError) as error:
        questions.load_questions(path)
    assert re.search(rf"\b{line}\b", str(error.value)), f"line {line} not named in: {error.value}"
    return error.value


# ---------------------------------------------------------------- valid files

def test_valid_file_loads_in_file_order(tmp_path):
    lines = [
        entry(id="q01", notes="Table 1, row VS"),
        entry(id="q02", type="table_lookup", sources=[["esp32_datasheet_en", 12], ["esp32_datasheet_en", 13]]),
        no_answer(id="q03"),
        entry(id="q04", type="comparison", sources=[["lm35", 3], ["esp32_datasheet_en", 12]]),
        entry(id="q05", type="rephrased"),
    ]
    loaded = questions.load_questions(write(tmp_path, *lines))
    assert [q["id"] for q in loaded] == ["q01", "q02", "q03", "q04", "q05"]
    assert [q["type"] for q in loaded] == ["single_value", "table_lookup", "no_answer", "comparison", "rephrased"]


def test_sources_become_tuples(tmp_path):
    loaded = questions.load_questions(write(tmp_path, entry(sources=[["lm35", 7], ["esp32_datasheet_en", 12]])))
    assert loaded[0]["sources"] == [("lm35", 7), ("esp32_datasheet_en", 12)]
    assert all(isinstance(source, tuple) for source in loaded[0]["sources"])


def test_fields_are_kept(tmp_path):
    loaded = questions.load_questions(write(tmp_path, entry(notes="checked on page 4")))[0]
    assert loaded["question"] == "What is the LM35 supply voltage range?"
    assert loaded["expected_answer"] == "4 V to 30 V"
    assert loaded["notes"] == "checked on page 4"


def test_notes_are_optional(tmp_path):
    loaded = questions.load_questions(write(tmp_path, entry(), no_answer()))
    assert [q["id"] for q in loaded] == ["q01", "q99"]
    assert loaded[1]["sources"] == []


def test_blank_lines_are_ignored(tmp_path):
    path = write(tmp_path, "", entry(id="q01"), "", "   ", entry(id="q02"), "")
    assert [q["id"] for q in questions.load_questions(path)] == ["q01", "q02"]


def test_a_single_question(tmp_path):
    assert len(questions.load_questions(write(tmp_path, entry()))) == 1


def test_path_none_reads_the_configured_file(tmp_path, monkeypatch):
    path = write(tmp_path, entry(id="from_config"))
    monkeypatch.setattr(config, "QUESTIONS", path)
    assert [q["id"] for q in questions.load_questions()] == ["from_config"]
    assert [q["id"] for q in questions.load_questions(None)] == ["from_config"]


# ---------------------------------------------------------------- errors naming the line

def test_bad_json(tmp_path):
    fails_on_line(tmp_path, '{"id": "q03", "question": ')


@pytest.mark.parametrize("field", ["id", "question", "expected_answer", "sources", "type"])
def test_missing_required_field(tmp_path, field):
    bad = entry(id="q03")
    del bad[field]
    fails_on_line(tmp_path, bad)


def test_unknown_type(tmp_path):
    fails_on_line(tmp_path, entry(id="q03", type="multiple_choice"))


def test_duplicate_id(tmp_path):
    fails_on_line(tmp_path, entry(id="a1"))


def test_unknown_document(tmp_path):
    fails_on_line(tmp_path, entry(id="q03", sources=[["lm358", 7]]))


def test_document_that_is_not_text(tmp_path):
    fails_on_line(tmp_path, entry(id="q03", sources=[[["lm35"], 7]]))


def test_id_that_is_not_text(tmp_path):
    fails_on_line(tmp_path, entry(id=["q03"]))


def test_unknown_document_in_a_second_source(tmp_path):
    fails_on_line(tmp_path, entry(id="q03", sources=[["lm35", 7], ["nosuch", 1]]))


@pytest.mark.parametrize("page", [0, -1])
def test_page_below_one(tmp_path, page):
    fails_on_line(tmp_path, entry(id="q03", sources=[["lm35", page]]))


@pytest.mark.parametrize("page", ["3", 2.5, None])
def test_page_not_an_int(tmp_path, page):
    fails_on_line(tmp_path, entry(id="q03", sources=[["lm35", page]]))


@pytest.mark.skipif(not LM35_PARSED, reason="runs/parsed/lm35.json not found")
def test_page_past_the_end_of_the_document(tmp_path):
    pages = len(parse.load_parsed("lm35")["pages"])
    assert questions.load_questions(write(tmp_path, entry(sources=[["lm35", pages]])))[0]["sources"] == [
        ("lm35", pages)]
    fails_on_line(tmp_path, entry(id="q03", sources=[["lm35", pages + 1]]))


def test_page_count_is_not_checked_when_the_parsed_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PARSED_DIR", tmp_path / "no_parsed_files")
    loaded = questions.load_questions(write(tmp_path, entry(sources=[["lm35", 9999]])))
    assert loaded[0]["sources"] == [("lm35", 9999)]


def test_no_answer_with_sources(tmp_path):
    fails_on_line(tmp_path, no_answer(id="q03", sources=[["lm35", 7]]))


@pytest.mark.parametrize("kind", ["single_value", "table_lookup", "comparison", "rephrased"])
def test_answerable_type_without_sources(tmp_path, kind):
    fails_on_line(tmp_path, entry(id="q03", type=kind, sources=[]))


def test_the_error_names_the_first_bad_line(tmp_path):
    path = write(tmp_path, entry(id="a1"), entry(id="a2", type="bad"), entry(id="a3", type="bad"))
    with pytest.raises(ValueError) as error:
        questions.load_questions(path)
    assert re.search(r"\b2\b", str(error.value))

# ---------------------------------------------------------------- the frozen question set (Layer 8)

def test_question_file_is_frozen():
    # Line endings are compared as "\n": Git on Windows checks the file out with "\r\n".
    text = config.QUESTIONS.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(text).hexdigest() == config.QUESTIONS_SHA256, (
        "data/questions.jsonl changed. The question set was frozen at Layer 8; results are only comparable on it.")
