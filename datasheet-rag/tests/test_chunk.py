"""Layer 2 tests. The real-data tests need the parsed datasheets in runs/parsed (not in git)."""
import json
from functools import cache
from itertools import pairwise
from pathlib import Path

import pytest

from rag import chunk, chunk_tables, chunk_text, config
from rag.chunk_base import approx_tokens

PARSED = sorted(config.PARSED_DIR.glob("*.json"))
SIZED = {name: s["size"] for name, s in config.CHUNK_STRATEGIES.items() if s["size"] is not None}
CHUNK_KEYS = {"document", "page", "page_label", "section", "kind", "text"}

H_APPLICATION = "8 Application and Implementation"
H_ELECTRICAL = "9. Electrical characteristics"
H_LDO = "3.2 Internal LDO (VDD_SDIO) Voltage Control"
H_REGULATOR = "3.16.1 Regulator ON"

EIGHT_WORDS = "alpha beta gamma delta epsilon zeta eta theta"


# --- fixture builders --------------------------------------------------------

def make_page(number: int, outside: str = "", tables: list[dict] | None = None, text: str | None = None,
              label: str | None = None) -> dict:
    return {
        "page": number,
        "page_label": label,
        "blank": not outside and not tables,
        "text": outside if text is None else text,
        "text_outside_tables": outside,
        "tables": tables or [],
    }


def make_table(rows: list[list], caption: str | None = None, page: int = 1, index: int = 0) -> dict:
    return {"page": page, "index": index, "caption": caption, "bbox": [50, 100, 500, 300], "rows": rows}


def make_doc(*pages: dict, name: str = "demo") -> dict:
    return {"document": name, "file": f"{name}.pdf", "page_count": len(pages), "pages": list(pages)}


def squash(text: str) -> str:
    """The text with all whitespace removed, to compare content whatever the line breaks and hard cuts."""
    return "".join(text.split())


def texts(chunks: list[dict]) -> list[str]:
    return [c["text"] for c in chunks]


def overlap_run(previous_words: list[str], overlap: int) -> list[str]:
    """The longest run of the last words whose joined text is at most `overlap` tokens (the contract's rule)."""
    run: list[str] = []
    for word in reversed(previous_words):
        if approx_tokens(" ".join([word, *run])) > overlap:
            break
        run.insert(0, word)
    return run


SUPPLY_ROWS = [
    ["Parameter", "Min", "Typ", "Max", "Unit"],
    ["VDD", "3.0", "", "3.6", "V"],
    ["IDD", "", "5", "", "mA"],
]
SUPPLY_CAPTION = "Table 1. Supply"
SUPPLY_LINES = ["Parameter: VDD, Min: 3.0, Max: 3.6, Unit: V", "Parameter: IDD, Typ: 5, Unit: mA"]


# --- headings and sections (test list 11) ------------------------------------

@pytest.mark.parametrize("heading", [
    H_LDO,
    "6.6 Electrical Characteristics: LM35A, LM35CA",
    H_APPLICATION,
    H_ELECTRICAL,
    H_REGULATOR,
])
def test_numbered_heading_is_found(heading):
    assert chunk_text.find_headings(heading) == [(0, heading)]


@pytest.mark.parametrize("not_heading", [
    "0.01 PF BYPASS",
    "1 PF",
    "1.2 V",
    "4.2.2 RTC Clock 29",
    "4.3.1 Power Management Unit (PMU) 30",
    "5 V supply is required for the sensor to work correctly in this mode.",
    "The device draws only 60 µA from the supply.",
    "see the note below for details",
    "",
])
def test_value_contents_line_and_prose_are_not_headings(not_heading):
    assert chunk_text.find_headings(not_heading) == []


def test_headings_come_with_their_line_index():
    text = f"Some opening words here\n{H_LDO}\nbody text of the section\n1.2 V\n{H_REGULATOR}"
    assert chunk_text.find_headings(text) == [(1, H_LDO), (4, H_REGULATOR)]


def test_sections_by_page_carries_a_section_across_pages():
    parsed = make_doc(
        make_page(1, "Opening words only"),
        make_page(2, f"{H_APPLICATION}\nBody text follows here"),
        make_page(3, "More body text with no heading"),
        make_page(4, f"{H_ELECTRICAL}\nvalues follow\n{H_REGULATOR}\nmore values"),
        make_page(5, "Last page"),
    )
    assert chunk_text.sections_by_page(parsed) == {
        1: None,
        2: None,                # the heading on page 2 itself does not count at the top of page 2
        3: H_APPLICATION,
        4: H_APPLICATION,
        5: H_REGULATOR,         # the last heading of page 4
    }


def test_sections_by_page_skips_nothing_on_a_blank_page():
    parsed = make_doc(make_page(1, H_APPLICATION), make_page(2), make_page(3, "text"))
    assert chunk_text.sections_by_page(parsed) == {1: None, 2: H_APPLICATION, 3: H_APPLICATION}


def test_text_chunkers_give_nothing_for_a_document_with_no_pages():
    parsed = make_doc()
    assert chunk_text.sections_by_page(parsed) == {}
    assert chunk_text.page_chunks(parsed) == []
    assert chunk_text.fixed_chunks(parsed, 256) == []
    assert chunk_text.structure_chunks(parsed, 400) == []


# --- page chunks (test list 1, 5) --------------------------------------------

def test_page_chunks_one_per_page_with_text():
    parsed = make_doc(
        make_page(1, "Front page words", label="i"),
        make_page(2),
        make_page(3, "   \n  "),
        make_page(4, "words outside", text="  words outside\nParameter Min Max\nVDD 3.0 3.6\n", label="4"),
        name="lm35",
    )
    chunks = chunk_text.page_chunks(parsed)
    assert [c["page"] for c in chunks] == [1, 4]
    assert texts(chunks) == ["Front page words", "words outside\nParameter Min Max\nVDD 3.0 3.6"]
    assert [c["page_label"] for c in chunks] == ["i", "4"]
    for c in chunks:
        assert set(c) >= CHUNK_KEYS
        assert c["document"] == "lm35" and c["kind"] == "text"


def test_page_chunk_section_is_its_own_first_heading_or_the_one_carried_over():
    parsed = make_doc(
        make_page(1, "Opening words only"),
        make_page(2, f"{H_APPLICATION}\nBody text follows here"),
        make_page(3, f"Body continues here\n{H_ELECTRICAL}\nvalues"),
        make_page(4, "The end"),
    )
    assert [c["section"] for c in chunk_text.page_chunks(parsed)] == [None, H_APPLICATION, H_APPLICATION, H_ELECTRICAL]


def test_page_chunks_have_no_size_limit():
    long_text = " ".join(["word"] * 3000)
    chunks = chunk_text.page_chunks(make_doc(make_page(1, long_text)))
    assert texts(chunks) == [long_text]


# --- fixed chunks (test list 2, 3, 13) ---------------------------------------

def test_fixed_chunks_fill_up_to_the_size():
    # size 5 = 20 characters. "alpha beta gamma" is 16; adding " delta" makes 22.
    chunks = chunk_text.fixed_chunks(make_doc(make_page(1, EIGHT_WORDS)), 5)
    assert texts(chunks) == ["alpha beta gamma", "delta epsilon zeta", "eta theta"]
    assert all(c["kind"] == "text" and c["page"] == 1 and c["document"] == "demo" for c in chunks)


def test_fixed_chunk_exactly_at_the_limit_stays_whole():
    text = "aaaa bbbb cccc ddddd"   # 20 characters = 5 tokens
    assert texts(chunk_text.fixed_chunks(make_doc(make_page(1, text)), 5)) == [text]
    assert texts(chunk_text.fixed_chunks(make_doc(make_page(1, text + "e")), 5)) == ["aaaa bbbb cccc", "ddddde"]


def test_fixed_chunk_text_is_words_joined_by_single_spaces():
    chunks = chunk_text.fixed_chunks(make_doc(make_page(1, "alpha  beta\ngamma\n  delta ")), 256)
    assert texts(chunks) == ["alpha beta gamma delta"]


def test_fixed_chunks_single_word_page():
    chunks = chunk_text.fixed_chunks(make_doc(make_page(7, "LM35", label="7")), 256, 50)
    assert texts(chunks) == ["LM35"]
    assert chunks[0]["page"] == 7 and chunks[0]["page_label"] == "7"


@pytest.mark.parametrize("size", [3, 5, 8, 20, 256])
def test_fixed_chunks_without_overlap_reproduce_the_words_exactly(size):
    lines = [" ".join(f"w{i}" for i in range(start, start + 9)) for start in range(0, 180, 9)]
    parsed = make_doc(make_page(1, "\n".join(lines)), make_page(2, "second page words go here"))
    chunks = chunk_text.fixed_chunks(parsed, size)
    for page in parsed["pages"]:
        words = [w for c in chunks if c["page"] == page["page"] for w in c["text"].split()]
        assert words == page["text_outside_tables"].split()
    assert all(approx_tokens(c["text"]) <= size for c in chunks)
    assert all(c["text"] == " ".join(c["text"].split()) for c in chunks)


def test_fixed_chunk_never_crosses_a_page():
    parsed = make_doc(make_page(1, "one two three"), make_page(2), make_page(3, "four five six"))
    for overlap in (0, 50):
        chunks = chunk_text.fixed_chunks(parsed, 256, overlap)
        assert [(c["page"], c["text"]) for c in chunks] == [(1, "one two three"), (3, "four five six")]


def test_fixed_chunks_come_out_in_page_order():
    parsed = make_doc(*[make_page(n, EIGHT_WORDS) for n in (1, 2, 3)])
    pages = [c["page"] for c in chunk_text.fixed_chunks(parsed, 5)]
    assert pages == sorted(pages) and set(pages) == {1, 2, 3}


@pytest.mark.parametrize("overlap", [0, 2])
def test_fixed_over_long_word_is_hard_cut(overlap):
    text = "start " + "x" * 50 + " end"
    chunks = chunk_text.fixed_chunks(make_doc(make_page(1, text)), 5, overlap)
    assert len(chunks) >= 3
    assert all(c["text"].strip() and approx_tokens(c["text"]) <= 5 for c in chunks)
    if overlap == 0:
        assert squash("".join(texts(chunks))) == squash(text)
    else:
        assert sum(c["text"].count("x") for c in chunks) >= 50


def test_fixed_overlap_chunks_share_the_expected_text():
    # size 5 = 20 characters, overlap 2 = 8 characters.
    # After "alpha beta gamma": "gamma" (5) fits the overlap, "beta gamma" (10) does not.
    # After "epsilon zeta eta": "zeta eta" (8) fits, "epsilon zeta eta" (16) does not.
    chunks = chunk_text.fixed_chunks(make_doc(make_page(1, EIGHT_WORDS)), 5, 2)
    assert texts(chunks) == ["alpha beta gamma", "gamma delta epsilon", "epsilon zeta eta", "zeta eta theta"]


@pytest.mark.parametrize("size, overlap", [(20, 5), (256, 50), (400, 80)])
def test_fixed_overlap_is_the_tail_of_the_previous_chunk(size, overlap):
    page_words = {1: [f"w{i}" for i in range(900)], 2: [f"v{i}" for i in range(900)]}
    parsed = make_doc(*[make_page(n, " ".join(words)) for n, words in page_words.items()])
    chunks = chunk_text.fixed_chunks(parsed, size, overlap)
    assert all(approx_tokens(c["text"]) <= size for c in chunks)
    for number, words in page_words.items():
        on_page = [c["text"].split() for c in chunks if c["page"] == number]
        assert len(on_page) > 1
        assert on_page[0][0] == words[0], "overlap must not cross a page"
        rebuilt = list(on_page[0])
        for previous, current in pairwise(on_page):
            shared = overlap_run(previous, overlap)
            assert shared, "fixture error: the overlap should hold at least one word"
            assert current[:len(shared)] == shared
            assert len(current) > len(shared), "a chunk needs at least one new word after the overlap"
            rebuilt += current[len(shared):]
        assert rebuilt == words


def test_fixed_chunk_section_is_the_heading_in_effect_at_its_first_word():
    parsed = make_doc(
        make_page(1, "Opening words only"),
        make_page(2, f"{H_APPLICATION}\nalpha beta gamma delta\n{H_ELECTRICAL}\nepsilon zeta"),
        make_page(3, "eta theta"),
    )
    chunks = chunk_text.fixed_chunks(parsed, 256)
    assert [(c["page"], c["section"]) for c in chunks] == [(1, None), (2, H_APPLICATION), (3, H_ELECTRICAL)]


def test_fixed_chunk_section_changes_inside_a_page():
    # size 4 = 16 characters: "intro words here" fills the first chunk, so the second starts on the heading line.
    parsed = make_doc(make_page(1, f"intro words here\n{H_APPLICATION}\nbody"))
    chunks = chunk_text.fixed_chunks(parsed, 4)
    assert chunks[0]["text"] == "intro words here" and chunks[0]["section"] is None
    assert len(chunks) > 1
    assert all(c["section"] == H_APPLICATION for c in chunks[1:])


# --- structure-aware chunks (test list 2, 14) --------------------------------

def test_structure_heading_starts_a_new_chunk_and_is_its_first_line():
    lines = ["Preamble line one.", H_APPLICATION, "The LM35 is easy to apply.", "It can be glued to a surface.",
             H_ELECTRICAL, "Values are listed below."]
    text = "\n".join(lines)
    chunks = chunk_text.structure_chunks(make_doc(make_page(3, text, label="3"), name="lm35"), 400)
    assert texts(chunks) == [
        "Preamble line one.",
        f"{H_APPLICATION}\nThe LM35 is easy to apply.\nIt can be glued to a surface.",
        f"{H_ELECTRICAL}\nValues are listed below.",
    ]
    assert [c["section"] for c in chunks] == [None, H_APPLICATION, H_ELECTRICAL]
    for c in chunks:
        assert c["document"] == "lm35" and c["page"] == 3 and c["page_label"] == "3" and c["kind"] == "text"


def test_structure_small_size_keeps_headings_first_and_sections_right():
    # size 12 = 48 characters. The first heading (43) plus "line a one." would be 55.
    lines = ["Opening words before any heading.", H_LDO, "line a one.", "line b two.", "line c three.",
             H_REGULATOR, "line d four."]
    chunks = chunk_text.structure_chunks(make_doc(make_page(1, "\n".join(lines))), 12)
    assert texts(chunks) == [
        "Opening words before any heading.",
        H_LDO,
        "line a one.\nline b two.\nline c three.",
        f"{H_REGULATOR}\nline d four.",
    ]
    assert [c["section"] for c in chunks] == [None, H_LDO, H_LDO, H_REGULATOR]


@pytest.mark.parametrize("size", [6, 12, 20, 400])
def test_structure_chunks_stay_under_the_size_and_lose_no_text(size):
    body = [f"Sentence number {i} says something short." if i % 3 else f"• bullet {i} without an end" for i in range(40)]
    lines = ["Words before the first heading", H_LDO, *body[:20], H_REGULATOR, *body[20:], "y" * 130]
    parsed = make_doc(make_page(1, "\n".join(lines)), make_page(2, "a second page"))
    chunks = chunk_text.structure_chunks(parsed, size)
    assert all(c["text"].strip() and approx_tokens(c["text"]) <= size for c in chunks)
    for page in parsed["pages"]:
        on_page = [c["text"] for c in chunks if c["page"] == page["page"]]
        assert squash("".join(on_page)) == squash(page["text_outside_tables"])
    if size >= 12:   # both headings fit on one line
        for heading in (H_LDO, H_REGULATOR):
            assert sum(c["text"].split("\n")[0] == heading for c in chunks) == 1
            assert not any(heading in c["text"].split("\n")[1:] for c in chunks)
    pages = [c["page"] for c in chunks]
    assert pages == sorted(pages)


def test_structure_packs_whole_units_not_single_lines():
    # size 13 = 52 characters. Line by line, the first two lines (52) would fit together;
    # as units, the two-line second sentence (50) does not fit after the first (25).
    text = "First sentence ends here.\nSecond sentence starts and\ncontinues on this line."
    chunks = chunk_text.structure_chunks(make_doc(make_page(1, text)), 13)
    assert texts(chunks) == ["First sentence ends here.", "Second sentence starts and\ncontinues on this line."]


def test_structure_bullet_starts_a_new_unit():
    # size 11 = 44 characters. The first two lines are 43 together, but the bullet unit is two lines (33).
    text = "Intro text that is here\n• Bullet one starts\nand continues"
    chunks = chunk_text.structure_chunks(make_doc(make_page(1, text)), 11)
    assert texts(chunks) == ["Intro text that is here", "• Bullet one starts\nand continues"]


def test_structure_units_that_fit_share_a_chunk():
    text = "What is it?\nIt is a sensor.\n(1) First note\n- a dash item\nNote:\nlast line"
    chunks = chunk_text.structure_chunks(make_doc(make_page(1, text)), 400)
    assert texts(chunks) == [text]


def test_structure_over_long_unit_is_split_at_line_ends():
    # One unit of three 20-character lines; size 6 = 24 characters holds one line.
    lines = ["alpha beta gamma one", "alpha beta gamma two", "alpha beta gamma six"]
    chunks = chunk_text.structure_chunks(make_doc(make_page(1, "\n".join(lines))), 6)
    assert texts(chunks) == lines


def test_structure_over_long_line_is_split_at_words_then_hard_cut():
    text = "one two three four five six seven eight nine ten\n" + "x" * 90
    chunks = chunk_text.structure_chunks(make_doc(make_page(1, text)), 5)
    assert all(c["text"].strip() and approx_tokens(c["text"]) <= 5 for c in chunks)
    assert squash("".join(texts(chunks))) == squash(text)
    words = " ".join(texts(chunks)).split()
    assert words[:10] == text.split()[:10], "words must stay whole where they fit"


def test_structure_section_is_carried_over_from_an_earlier_page():
    parsed = make_doc(
        make_page(1, f"{H_APPLICATION}\nBody text."),
        make_page(2),
        make_page(3, "Text on a later page."),
    )
    chunks = chunk_text.structure_chunks(parsed, 400)
    assert [(c["page"], c["section"]) for c in chunks] == [(1, H_APPLICATION), (3, H_APPLICATION)]


def test_structure_never_joins_text_across_pages():
    parsed = make_doc(make_page(1, "no full stop at the end"), make_page(2, "of this sentence."))
    assert texts(chunk_text.structure_chunks(parsed, 400)) == ["no full stop at the end", "of this sentence."]


# --- tables: figure tables (test list 10) ------------------------------------

@pytest.mark.parametrize("caption, expected", [
    ("Figure 1. Thermal Resistance Junction To Air", True),
    ("FIGURE 5-2: TIMING", True),
    ("figure 3", True),
    ("Table 4. Figure of merit", False),
    ("6.6 Electrical Characteristics: LM35A, LM35CA", False),
    ("", False),
    (None, False),
])
def test_is_figure_table(caption, expected):
    assert chunk_tables.is_figure_table(make_table([["400", "300"], ["200", "100"]], caption)) is expected


def test_table_chunks_skip_a_figure_table():
    figure = make_table(SUPPLY_ROWS, "Figure 2. Supply current")
    assert chunk_tables.table_chunks("demo", 1, "1", figure, 400) == []


# --- tables: normalise_table (test list 7, 8, 9) -----------------------------

def test_single_header_row_gives_its_cells_as_labels():
    labels, data = chunk_tables.normalise_table(SUPPLY_ROWS)
    assert labels == ["Parameter", "Min", "Typ", "Max", "Unit"]
    assert data == [["VDD", "3.0", "", "3.6", "V"], ["IDD", "", "5", "", "mA"]]


def test_header_cell_that_spans_columns_labels_each_of_them():
    labels, data = chunk_tables.normalise_table([
        ["Symbol", "Ratings", None, "Max", ""],
        ["IVDD", "Total current", "into VDD", "120", "mA"],
    ])
    assert labels == ["Symbol", "Ratings", "Ratings", "Max", ""]
    assert data == [["IVDD", "Total current", "into VDD", "120", "mA"]]


def test_header_only_table_has_no_data_rows():
    assert chunk_tables.normalise_table([["Symbol", "Max"]]) == (["Symbol", "Max"], [])


TWO_ROW_HEADERS = [
    (
        ["PARAMETER", "TEST CONDITIONS", None, "", "LM35A", "", "", "LM35CA", "", "UNIT"],
        [None, None, None, "MIN", "TYP", "MAX", "TYP", "TYP", "MAX", None],
        ["PARAMETER", "TEST CONDITIONS", "TEST CONDITIONS", "LM35A MIN", "LM35A TYP", "LM35A MAX",
         "LM35CA TYP", "LM35CA TYP", "LM35CA MAX", "UNIT"],
    ),
    (
        ["PARAMETER", "TEST CONDITIONS", None, "", "LM35", "", "LM35C, LM35D", None, None, "UNIT"],
        [None, None, None, "MIN", "TYP", "MAX", "MIN", "TYP", "MAX", None],
        ["PARAMETER", "TEST CONDITIONS", "TEST CONDITIONS", "LM35 MIN", "LM35 TYP", "LM35 MAX",
         "LM35C, LM35D MIN", "LM35C, LM35D TYP", "LM35C, LM35D MAX", "UNIT"],
    ),
    (
        ["PARAMETER", "TEST CONDITIONS", "", "LM35A", "", "", "LM35CA", "", "UNIT"],
        [None, None, "TYP", "TESTED LIMIT(1)", "DESIGN LIMIT(2)", "TYP", "TESTED LIMIT(1)", "DESIGN LIMIT(2)", None],
        ["PARAMETER", "TEST CONDITIONS", "LM35A TYP", "LM35A TESTED LIMIT", "LM35A DESIGN LIMIT",
         "LM35CA TYP", "LM35CA TESTED LIMIT", "LM35CA DESIGN LIMIT", "UNIT"],
    ),
]


@pytest.mark.parametrize("row0, row1, expected", TWO_ROW_HEADERS, ids=["lm35a-min-typ-max", "lm35-spanned", "lm35a-limits"])
def test_two_row_header_gives_combined_labels(row0, row1, expected):
    data_row = ["Accuracy", "TA = 25°C"] + [""] * (len(row0) - 3) + ["°C"]
    labels, data = chunk_tables.normalise_table([row0, row1, data_row])
    assert labels == expected
    assert data == [data_row], "the second header row must not come back as data"


def test_two_row_header_only_has_no_data_rows():
    row0, row1, expected = TWO_ROW_HEADERS[0]
    assert chunk_tables.normalise_table([row0, row1]) == (expected, [])


def test_sub_label_with_no_group_label_stands_alone():
    labels, data = chunk_tables.normalise_table([
        ["Parameter", "", "", "Unit"],
        [None, "Min", "Max", None],
        ["VDD", "3.0", "3.6", "V"],
    ])
    assert labels == ["Parameter", "Min", "Max", "Unit"]
    assert data == [["VDD", "3.0", "3.6", "V"]]


def test_second_row_with_numbers_is_data_not_a_header():
    labels, data = chunk_tables.normalise_table([["Parameter", "", "Max"], ["", "1.8", "3.6"]])
    assert labels == ["Parameter", "", "Max"]
    assert data == [["", "1.8", "3.6"]]


def test_second_row_under_filled_headers_is_data_not_a_header():
    labels, data = chunk_tables.normalise_table([["Parameter", "Conditions", "Unit"], ["", "TA = 25°C", "mA"]])
    assert labels == ["Parameter", "Conditions", "Unit"]
    assert data == [["", "TA = 25°C", "mA"]]


def test_unit_is_filled_down():
    _, data = chunk_tables.normalise_table([
        ["Symbol", "Ratings", "Max", "Unit"],
        ["IVDD", "Total current into VDD", "120", "mA"],
        ["IVSS", "Total current out of VSS", "120", None],
        ["IIO", "Output current sunk by any pin", "25", None],
    ])
    assert data == [
        ["IVDD", "Total current into VDD", "120", "mA"],
        ["IVSS", "Total current out of VSS", "120", "mA"],
        ["IIO", "Output current sunk by any pin", "25", "mA"],   # filled from the row above, itself filled
    ]


def test_number_is_never_filled_down():
    _, data = chunk_tables.normalise_table([
        ["PARAMETER", "CONDITIONS", "LIMIT", "MIN", "TYP", "MAX"],
        ["Accuracy", "TA = 25°C", "Tested Limit", "", "", "±0.5"],
        [None, None, "Design Limit", "", None, None],
    ])
    assert data == [
        ["Accuracy", "TA = 25°C", "Tested Limit", "", "", "±0.5"],
        ["Accuracy", "TA = 25°C", "Design Limit", "", "", ""],
    ]


@pytest.mark.parametrize("number", ["120", "±0.5", "–65 to +150", "3.3 V", "-40", "+85", "−5", "<1", ">2", "≤3.6", "≥1.8", "~10"])
def test_number_like_values_are_not_copied_into_the_next_row(number):
    rows = [["Parameter", "Value", "Note"], ["First", number, "a"], ["Second", None, "b"]]
    _, data = chunk_tables.normalise_table(rows)
    assert data == [["First", number, "a"], ["Second", "", "b"]]


@pytest.mark.parametrize("number", ["≤ 3.6", "± 0.5", "- 40"])
def test_number_with_a_space_after_its_sign_is_number_like(number):
    rows = [["Value", "Note"], [number, "a"], [None, "b"]]
    _, data = chunk_tables.normalise_table(rows)
    assert data == [[number, "a"], ["", "b"]]


# The rules below were changed by the lead at integration, after the table builder found real tables
# that the first contract got wrong (ESP32 pages 14 and 68, STM32 pages 14 and 83, TCTH page 6, LM35 page 9).

@pytest.mark.parametrize("value", ["mA", "°C", "TA = 25°C", "Accuracy"])
def test_text_is_filled_down_before_the_first_filled_cell_of_a_row(value):
    rows = [["Parameter", "Conditions", "Value"], [value, "a", "1"], [None, "b", "2"]]
    _, data = chunk_tables.normalise_table(rows)
    assert data == [[value, "a", "1"], [value, "b", "2"]]


@pytest.mark.parametrize("value", ["mA", "°C", "TA = 25°C", "Accuracy"])
def test_text_is_not_filled_down_after_a_filled_cell(value):
    # The None may be merged with the cell to its left, so the text above it is not copied.
    rows = [["Parameter", "Value", "Note"], ["First", value, "a"], ["Second", None, "b"]]
    _, data = chunk_tables.normalise_table(rows)
    assert data == [["First", value, "a"], ["Second", "", "b"]]


def test_another_rows_description_is_not_copied_into_a_spanning_row():
    # ESP32 Table 2-1: each pin's description spans the columns to its right.
    _, data = chunk_tables.normalise_table([
        ["Name", "No.", "Function", ""],
        ["VDET_2", "11", "GPIO35,", "ADC1_CH7, RTC_GPIO5"],
        ["32K_XP", "12", "GPIO32, ADC1_CH4", None],
    ])
    assert data[1] == ["32K_XP", "12", "GPIO32, ADC1_CH4", ""]


def test_a_cell_that_spans_columns_carries_all_their_labels():
    # LM35 page 9: one value printed across MIN, TYP and MAX. The unit column is not part of the span.
    table = {"caption": None, "rows": [
        ["PARAMETER", "MIN", "TYP", "MAX", "UNIT"],
        ["Accuracy", "±0.4", None, None, "°C"],
        ["Gain", "9.8", "10", "10.2", None],
    ]}
    chunks = chunk_tables.table_chunks("doc", 1, "1", table, 400)
    assert chunks[0]["text"].split("\n") == [
        "PARAMETER: Accuracy, MIN / TYP / MAX: ±0.4, UNIT: °C",
        "PARAMETER: Gain, MIN: 9.8, TYP: 10, MAX: 10.2, UNIT: °C",
    ]


def test_group_label_covers_the_none_cells_to_its_right():
    # STM32 Table 23: 'Max' spans three columns. Sharing the run out evenly would call the second column 'Typ'.
    labels, _ = chunk_tables.normalise_table([
        ["Symbol", "Typ", "Max", None, None, "Unit"],
        [None, "TA = 25 °C", "TA = 25 °C", "TA = 85 °C", "TA = 105 °C", None],
        ["IDD", "0.55", "1.2", "11.00", "20.00", "mA"],
    ])
    assert labels == ["Symbol", "Typ TA = 25 °C", "Max TA = 25 °C", "Max TA = 85 °C", "Max TA = 105 °C", "Unit"]


@pytest.mark.parametrize("cell", ["EMAC_TXD[0](O)", "2[15]-1", "3/(2)", "GPIO[0]"])
def test_brackets_that_are_not_footnote_markers_are_kept(cell):
    _, data = chunk_tables.normalise_table([["Signal", "Note"], [cell, "x"]])
    assert data == [[cell, "x"]]


# Added after the code review.

def test_group_header_over_the_first_columns_gives_two_header_rows():
    # STM32 Table 8: 'Pins' covers one column per package, starting at the first column.
    labels, data = chunk_tables.normalise_table([
        ["Pins", None, None, "Pin name", "Type"],
        ["LQFP64", "LQFP100", "LQFP144", None, None],
        ["-", "1", "1", "PE2", "I/O"],
    ])
    assert labels == ["Pins LQFP64", "Pins LQFP100", "Pins LQFP144", "Pin name", "Type"]
    assert data == [["-", "1", "1", "PE2", "I/O"]]


def test_text_row_under_ordinary_headers_is_data():
    labels, data = chunk_tables.normalise_table([["Name", "No.", "Function"], ["Analog", None, None], ["VDDA", "1", "Supply"]])
    assert labels == ["Name", "No.", "Function"]
    assert data == [["Analog", "", ""], ["VDDA", "1", "Supply"]]


def test_heading_row_inside_a_table_is_not_filled_down():
    _, data = chunk_tables.normalise_table([
        ["Symbol", "Parameter", "Unit"],
        ["DC characteristics", None, None],
        [None, "Output high", "V"],
    ])
    assert data[1] == ["", "Output high", "V"]


def test_long_cell_is_cut_between_words():
    table = {"caption": None, "rows": [["Pin", "Functions"], ["PE2", " ".join(f"SIGNAL_{n:02d}," for n in range(40))]]}
    chunks = chunk_tables.table_chunks("doc", 1, "1", table, 20)
    assert len(chunks) > 1
    words = [word for c in chunks for word in c["text"].replace("Pin: PE2, ", "").replace("Functions: ", "").split()]
    assert [w.rstrip(",") for w in words if w.startswith("SIGNAL")] == [f"SIGNAL_{n:02d}" for n in range(40)]


def test_heading_with_no_text_joins_the_block_below_it():
    parsed = {"document": "doc", "pages": [{
        "page": 1, "page_label": "1", "blank": False, "tables": [],
        "text": "2 Pins\n2.1 Pin Layout\nThe pins are laid out below.",
        "text_outside_tables": "2 Pins\n2.1 Pin Layout\nThe pins are laid out below.",
    }]}
    chunks = chunk_text.structure_chunks(parsed, 400)
    assert [c["text"] for c in chunks] == ["2 Pins\n2.1 Pin Layout\nThe pins are laid out below."]
    assert chunks[0]["section"] == "2.1 Pin Layout"


def test_text_chunks_handles_every_strategy():
    parsed = {"document": "doc", "pages": [{
        "page": 1, "page_label": "1", "blank": False, "tables": [],
        "text": "Some text here.", "text_outside_tables": "Some text here.",
    }]}
    for strategy in config.CHUNK_STRATEGIES:
        assert [c["text"] for c in chunk.text_chunks(parsed, strategy)] == ["Some text here."]


def test_chained_markers_are_stripped_and_a_bracketed_value_is_kept():
    _, data = chunk_tables.normalise_table([["A", "B", "C"], ["I2C(1)(2)", "reset)(1)", "3/(2)(2)"]])
    assert data == [["I2C", "reset)", "3/(2)"]]


def test_full_width_row_fills_nothing():
    _, data = chunk_tables.normalise_table([
        ["Pin", "Type", "Function"],
        ["GPIO1", "I/O", "General purpose"],
        ["Analog", None, None],
        ["VDET_1", "I", "Voltage detect"],
    ])
    assert data == [
        ["GPIO1", "I/O", "General purpose"],
        ["Analog", "", ""],
        ["VDET_1", "I", "Voltage detect"],
    ]


def test_footnote_markers_are_stripped_from_labels_and_cells():
    labels, data = chunk_tables.normalise_table([
        ["Pin(1)", "Power [2]", "Note"],
        ["GPIO16[2]", "VDD [1]", "(1)"],
        ["Accuracy(1)", "1.8(1)", "power domain [1, 2]"],
        ["Value(12)", "VDD_SDIO[1]", "plain"],
    ])
    assert labels == ["Pin", "Power", "Note"]
    assert data == [
        ["GPIO16", "VDD", "(1)"],                 # a cell that is only a marker is kept
        ["Accuracy", "1.8", "power domain"],
        ["Value", "VDD_SDIO", "plain"],
    ]


def test_line_breaks_inside_a_cell_become_single_spaces():
    labels, data = chunk_tables.normalise_table([["TESTED\nLIMIT", "Unit"], ["Total current\ninto VDD", "mA"]])
    assert labels == ["TESTED LIMIT", "Unit"]
    assert data == [["Total current into VDD", "mA"]]


def test_empty_data_rows_are_dropped():
    _, data = chunk_tables.normalise_table([["Parameter", "Value"], ["VDD", "3.3"], ["", ""], ["", None], ["IDD", "5"]])
    assert data == [["VDD", "3.3"], ["IDD", "5"]]


@pytest.mark.parametrize("rows", [
    SUPPLY_ROWS,
    [*TWO_ROW_HEADERS[1][:2], ["Accuracy(1)", "TA = 25°C", None, "", "±0.4", "", "", "±0.4", "", "°C"],
     [None, "TA = –10°C", None, "", "±0.5", "", "", "±0.5", "", None]],
    [["Pin", "Type", "Function"], ["Analog", None, None], ["VDET_1", "I", None], [None, None, None]],
], ids=["plain", "two-row-header", "merged-cells"])
def test_normalised_rows_are_strings_as_long_as_the_labels(rows):
    labels, data = chunk_tables.normalise_table(rows)
    assert len(labels) == len(rows[0]) and all(isinstance(label, str) for label in labels)
    for row in data:
        assert len(row) == len(labels)
        assert all(isinstance(cell, str) for cell in row)
        assert any(cell for cell in row)


# --- tables: row_sentence (test list 12) -------------------------------------

def test_row_sentence_leaves_out_empty_cells():
    sentence = chunk_tables.row_sentence(["Parameter", "Min", "Typ", "Max", "Unit"], ["VDD", "3.0", "", "3.6", "V"])
    assert sentence == "Parameter: VDD, Min: 3.0, Max: 3.6, Unit: V"


def test_row_sentence_writes_a_cell_with_no_label_as_the_value_alone():
    assert chunk_tables.row_sentence(["", "Min", ""], ["VDD", "3.0", "note"]) == "VDD, Min: 3.0, note"


def test_row_sentence_single_cell_and_empty_row():
    assert chunk_tables.row_sentence(["Unit"], ["V"]) == "Unit: V"
    assert chunk_tables.row_sentence(["Parameter", "Unit"], ["", "V"]) == "Unit: V"
    assert chunk_tables.row_sentence(["Parameter", "Unit"], ["", ""]) == ""


# --- tables: table_chunks (test list 4, 6, 10) -------------------------------

def test_table_that_fits_is_one_chunk_with_caption_and_row_sentences():
    chunks = chunk_tables.table_chunks("lm35", 6, "vi", make_table(SUPPLY_ROWS, SUPPLY_CAPTION, page=6), 400)
    assert len(chunks) == 1
    assert chunks[0] == {**chunks[0], "document": "lm35", "page": 6, "page_label": "vi", "section": None, "kind": "table"}
    assert set(chunks[0]) >= CHUNK_KEYS
    assert chunks[0]["text"] == "\n".join([SUPPLY_CAPTION, *SUPPLY_LINES])


def test_table_without_a_caption_has_no_caption_line():
    chunks = chunk_tables.table_chunks("demo", 1, None, make_table(SUPPLY_ROWS), 400)
    assert texts(chunks) == ["\n".join(SUPPLY_LINES)]
    assert chunks[0]["page_label"] is None


@pytest.mark.parametrize("rows", [
    [["Parameter", "Min", "Max"]],
    [["Parameter", "Min", "Max"], ["", "", ""]],
    [TWO_ROW_HEADERS[0][0], TWO_ROW_HEADERS[0][1]],
], ids=["header-only", "empty-data-row", "two-header-rows-only"])
def test_table_with_no_data_rows_gives_no_chunk(rows):
    assert chunk_tables.table_chunks("demo", 1, "1", make_table(rows, "Table 3. Nothing"), 400) == []


def test_table_exactly_at_the_limit_stays_whole_and_one_token_less_splits_it():
    table = make_table(SUPPLY_ROWS, SUPPLY_CAPTION)
    whole = "\n".join([SUPPLY_CAPTION, *SUPPLY_LINES])
    limit = approx_tokens(whole)   # 92 characters = 23 tokens; the caption (4 tokens) is under a quarter of that
    assert texts(chunk_tables.table_chunks("demo", 1, "1", table, limit)) == [whole]
    pieces = chunk_tables.table_chunks("demo", 1, "1", table, limit - 1)
    assert texts(pieces) == [f"{SUPPLY_CAPTION}\n{SUPPLY_LINES[0]}", f"{SUPPLY_CAPTION}\n{SUPPLY_LINES[1]}"]


def test_split_table_repeats_the_caption_and_the_labels_in_every_piece():
    caption = "Table 2. Currents"
    rows = [["Parameter", "Min", "Max", "Unit"]] + [[f"P{i}", str(i), str(i + 1), "mA"] for i in range(12)]
    expected_lines = [f"Parameter: P{i}, Min: {i}, Max: {i + 1}, Unit: mA" for i in range(12)]
    pieces = chunk_tables.table_chunks("demo", 4, "4", make_table(rows, caption, page=4), 40)
    assert len(pieces) > 1
    row_lines = []
    for piece in pieces:
        assert piece["kind"] == "table" and piece["page"] == 4 and piece["document"] == "demo"
        assert approx_tokens(piece["text"]) <= 40
        first, *rest = piece["text"].split("\n")
        assert first == caption
        assert rest, "a piece needs at least one row"
        for label in ("Parameter: ", "Min: ", "Max: ", "Unit: "):
            assert all(label in row_line for row_line in rest)
        row_lines += rest
    assert row_lines == expected_lines, "whole rows, in order, each exactly once"


def test_split_table_without_a_caption_still_carries_the_labels():
    rows = [["Parameter", "Min", "Max", "Unit"]] + [[f"P{i}", str(i), str(i + 1), "mA"] for i in range(12)]
    pieces = chunk_tables.table_chunks("demo", 1, "1", make_table(rows), 30)
    assert len(pieces) > 1
    lines = [row_line for piece in pieces for row_line in piece["text"].split("\n")]
    assert lines == [f"Parameter: P{i}, Min: {i}, Max: {i + 1}, Unit: mA" for i in range(12)]
    assert all(approx_tokens(piece["text"]) <= 30 for piece in pieces)


def test_table_chunk_carries_two_row_header_labels_and_no_footnote_markers():
    row0, row1, _ = TWO_ROW_HEADERS[2]
    rows = [row0, row1,
            ["Accuracy(3)", "TA = 25°C", "±0.2", "±0.5", "", "±0.2", "±0.5", "", "°C"],
            [None, "TA = –10°C", "±0.3", "", "", "±0.3", "", "±1", None]]
    caption = "6.5 Electrical Characteristics: LM35A, LM35CA Limits"
    chunks = chunk_tables.table_chunks("lm35", 5, "5", make_table(rows, caption, page=5), 400)
    first = ("PARAMETER: Accuracy, TEST CONDITIONS: TA = 25°C, LM35A TYP: ±0.2, LM35A TESTED LIMIT: ±0.5, "
             "LM35CA TYP: ±0.2, LM35CA TESTED LIMIT: ±0.5, UNIT: °C")
    second = ("PARAMETER: Accuracy, TEST CONDITIONS: TA = –10°C, LM35A TYP: ±0.3, LM35CA TYP: ±0.3, "
              "LM35CA DESIGN LIMIT: ±1, UNIT: °C")
    expected = [caption, first, second]
    assert texts(chunks) == ["\n".join(expected)]


LONG_DESCRIPTION = "Strapping pin that is sampled at reset to choose the boot voltage"
LONG_NOTES = "Leave the pin floating when the internal regulator is not used"


@pytest.mark.parametrize("caption", ["Table 7. Pins", None], ids=["caption", "no-caption"])
def test_row_too_long_for_one_piece_is_split_between_cells(caption):
    rows = [["Pin", "Description", "Notes"], ["GPIO5", LONG_DESCRIPTION, LONG_NOTES], ["GPIO6", "short", "row"]]
    whole_row = chunk_tables.row_sentence(rows[0], rows[1])
    assert approx_tokens(whole_row) > 30, "fixture error: the row must not fit in one piece"
    pieces = chunk_tables.table_chunks("demo", 1, "1", make_table(rows, caption), 30)
    assert len(pieces) >= 3
    assert all(approx_tokens(piece["text"]) <= 30 for piece in pieces)
    row_lines = []
    for piece in pieces:
        lines = piece["text"].split("\n")
        if caption:
            assert lines[0] == caption
            lines = lines[1:]
        assert lines
        row_lines += lines
    assert all(row_line.startswith(("Pin: GPIO5", "Pin: GPIO6")) for row_line in row_lines)
    gpio5 = [row_line for row_line in row_lines if row_line.startswith("Pin: GPIO5")]
    assert len(gpio5) >= 2, "the long row is split into several lines"
    assert sum(f"Description: {LONG_DESCRIPTION}" in row_line for row_line in gpio5) == 1
    assert sum(f"Notes: {LONG_NOTES}" in row_line for row_line in gpio5) == 1
    for row_line in gpio5:   # each part of the long row sits in its own piece
        assert sum(row_line in piece["text"].split("\n") for piece in pieces) == 1
        holder = next(piece for piece in pieces if row_line in piece["text"].split("\n"))
        assert sum(other in holder["text"].split("\n") for other in gpio5) == 1
    assert row_lines[-1] == "Pin: GPIO6, Description: short, Notes: row"


def test_single_cell_too_long_is_hard_cut():
    rows = [["Pin", "Data"], ["GPIO5", "x" * 600]]
    pieces = chunk_tables.table_chunks("demo", 1, "1", make_table(rows, "Table 8. Raw"), 20)
    assert len(pieces) > 1
    assert all(piece["text"].strip() and approx_tokens(piece["text"]) <= 20 for piece in pieces)
    assert all(piece["text"].split("\n")[0] == "Table 8. Raw" for piece in pieces)
    assert sum(piece["text"].count("x") for piece in pieces) == 600


def test_caption_longer_than_a_quarter_of_the_limit_is_cut():
    caption = "Table 9. " + "a very long caption that goes on " * 6
    pieces = chunk_tables.table_chunks("demo", 1, "1", make_table(SUPPLY_ROWS, caption.strip()), 40)
    assert pieces
    row_lines = []
    for piece in pieces:
        first, *rest = piece["text"].split("\n")
        assert first.strip() and caption.startswith(first.rstrip())
        assert approx_tokens(first) <= 10
        assert len(first) >= 30, "the caption is cut to a quarter of the limit, not dropped"
        assert approx_tokens(piece["text"]) <= 40
        row_lines += rest
    assert row_lines == SUPPLY_LINES


def test_caption_exactly_a_quarter_of_the_limit_is_kept_whole():
    caption = "Table 10. Supply voltage and current limits here"   # 48 characters = 12 tokens, limit 48
    assert approx_tokens(caption) == 12 and len(caption) == 48
    pieces = chunk_tables.table_chunks("demo", 1, "1", make_table(SUPPLY_ROWS, caption), 48)
    assert texts(pieces) == ["\n".join([caption, *SUPPLY_LINES])]


# --- chunk_document (test list 1, 2, 5, 10, 15) ------------------------------

def mixed_doc() -> dict:
    """Page 1: text and a table. Page 2: text and a figure table. Page 3: blank. Page 4: text only."""
    supply = make_table(SUPPLY_ROWS, SUPPLY_CAPTION, page=1)
    figure = make_table([["FIGCELL", "400"], ["300", "200"]], "Figure 1. Thermal response", page=2)
    return make_doc(
        make_page(1, f"{H_APPLICATION}\nThe supply limits are listed below.",
                  tables=[supply], label="1",
                  text=f"{H_APPLICATION}\nThe supply limits are listed below.\nParameter Min Typ Max Unit\nVDD 3.0 3.6 V"),
        make_page(2, "The response is shown in the figure.", tables=[figure], label="2",
                  text="The response is shown in the figure.\nFIGCELL 400\n300 200"),
        make_page(3, label="3"),
        make_page(4, "Closing words on the last page.", label="4"),
        name="mixed",
    )


def test_strategies_are_the_configured_ones():
    assert chunk.STRATEGIES == config.CHUNK_STRATEGIES


@pytest.mark.parametrize("strategy", list(config.CHUNK_STRATEGIES))
def test_chunk_document_sets_the_strategy_and_the_chunk_keys(strategy):
    parsed = mixed_doc()
    labels = {p["page"]: p["page_label"] for p in parsed["pages"]}
    chunks = chunk.chunk_document(parsed, strategy)
    assert chunks
    for c in chunks:
        assert set(c) >= CHUNK_KEYS | {"strategy"}
        assert c["strategy"] == strategy
        assert c["document"] == "mixed"
        assert c["page"] in (1, 2, 4), "the blank page gives no chunk"
        assert c["page_label"] == labels[c["page"]]
        assert c["kind"] in ("text", "table")
        assert c["text"].strip()
    assert {c["page"] for c in chunks} == {1, 2, 4}


@pytest.mark.parametrize("name", ["", "fixed", "fixed_300", "PAGE", "structure"])
def test_chunk_document_rejects_an_unknown_strategy(name):
    with pytest.raises(ValueError):
        chunk.chunk_document(mixed_doc(), name)


def test_page_strategy_is_the_page_chunks_with_no_table_chunks():
    parsed = mixed_doc()
    chunks = chunk.chunk_document(parsed, "page")
    assert all(c["kind"] == "text" for c in chunks)
    assert [(c["page"], c["text"]) for c in chunks] == [(c["page"], c["text"]) for c in chunk_text.page_chunks(parsed)]
    assert [c["page"] for c in chunks] == [1, 2, 4]


@pytest.mark.parametrize("strategy", list(SIZED))
def test_sized_strategies_give_text_then_table_chunks_page_by_page(strategy):
    chunks = chunk.chunk_document(mixed_doc(), strategy)
    order = [(c["page"], c["kind"]) for c in chunks]
    assert order == [(1, "text"), (1, "table"), (2, "text"), (4, "text")]
    assert chunks[1]["text"] == "\n".join([SUPPLY_CAPTION, *SUPPLY_LINES])
    assert all(approx_tokens(c["text"]) <= SIZED[strategy] for c in chunks)


@pytest.mark.parametrize("strategy", list(SIZED))
def test_chunk_document_leaves_out_a_figure_table(strategy):
    chunks = chunk.chunk_document(mixed_doc(), strategy)
    assert not any(c["kind"] == "table" and c["page"] == 2 for c in chunks)
    assert not any("FIGCELL" in c["text"] for c in chunks)


@pytest.mark.parametrize("strategy", list(SIZED))
def test_chunk_document_keeps_text_before_tables_on_every_page(strategy):
    rows = [["Parameter", "Min", "Max", "Unit"]] + [[f"P{i}", str(i), str(i + 1), "mA"] for i in range(150)]
    body = " ".join(f"word{i}" for i in range(1500))
    pages = [make_page(n, body, tables=[make_table(rows, f"Table {n}. Currents", page=n)], label=str(n)) for n in (1, 2)]
    chunks = chunk.chunk_document(make_doc(*pages), strategy)
    size = SIZED[strategy]
    assert all(approx_tokens(c["text"]) <= size for c in chunks)
    assert [c["page"] for c in chunks] == sorted(c["page"] for c in chunks)
    for number in (1, 2):
        kinds = [c["kind"] for c in chunks if c["page"] == number]
        assert kinds == sorted(kinds, key=["text", "table"].index)
        assert kinds.count("text") > 1 and kinds.count("table") > 1
        for c in chunks:
            if c["page"] == number and c["kind"] == "table":
                assert c["text"].split("\n")[0] == f"Table {number}. Currents"
                assert "Parameter: P" in c["text"] and "Unit: mA" in c["text"]


def test_chunk_document_on_an_empty_document():
    for strategy in config.CHUNK_STRATEGIES:
        assert chunk.chunk_document(make_doc(), strategy) == []


# --- real datasheets (test list 16) ------------------------------------------

needs_parsed = pytest.mark.skipif(not PARSED, reason="no parsed datasheets in runs/parsed")
REAL_CASES = [(path, strategy) for path in PARSED for strategy in config.CHUNK_STRATEGIES]
REAL_IDS = [f"{path.stem}-{strategy}" for path, strategy in REAL_CASES]


@cache
def load_real(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def real_chunks(path: Path, strategy: str) -> list[dict]:
    return chunk.chunk_document(load_real(path), strategy)


def caption_matches(first_line: str, caption: str, size: int) -> bool:
    """True when the line is the caption, or the start of a caption too long for a quarter of the limit."""
    caption = " ".join(caption.split())
    line = " ".join(first_line.split())
    if line == caption:
        return True
    return bool(line) and caption.startswith(line) and approx_tokens(caption) > size // 4


@needs_parsed
@pytest.mark.parametrize("path, strategy", REAL_CASES, ids=REAL_IDS)
def test_real_every_chunk_has_document_page_and_text(path, strategy):
    parsed = load_real(path)
    labels = {p["page"]: p["page_label"] for p in parsed["pages"]}
    chunks = real_chunks(path, strategy)
    assert chunks
    for c in chunks:
        assert c["document"] == parsed["document"]
        assert isinstance(c["page"], int) and c["page"] in labels
        assert c["page_label"] == labels[c["page"]]
        assert c["kind"] in ("text", "table")
        assert "section" in c and c["strategy"] == strategy
        assert isinstance(c["text"], str) and c["text"].strip()
    assert [c["page"] for c in chunks] == sorted(c["page"] for c in chunks)


@needs_parsed
@pytest.mark.parametrize("path, strategy", [case for case in REAL_CASES if case[1] in SIZED],
                         ids=[i for i, case in zip(REAL_IDS, REAL_CASES) if case[1] in SIZED])
def test_real_no_chunk_exceeds_its_size_limit(path, strategy):
    size = SIZED[strategy]
    too_long = [(c["page"], c["kind"], approx_tokens(c["text"])) for c in real_chunks(path, strategy)
                if approx_tokens(c["text"]) > size]
    assert not too_long, f"chunks over {size} tokens (page, kind, tokens): {too_long[:10]}"


@needs_parsed
@pytest.mark.parametrize("path, strategy", REAL_CASES, ids=REAL_IDS)
def test_real_chunk_text_comes_from_its_own_page(path, strategy):
    parsed = load_real(path)
    pages = {p["page"]: p for p in parsed["pages"]}
    chunks = real_chunks(path, strategy)
    text_chunks = [c for c in chunks if c["kind"] == "text"]
    if strategy == "page":
        assert len(chunks) == len(text_chunks)
        assert [(c["page"], c["text"]) for c in chunks] == [
            (p["page"], p["text"].strip()) for p in parsed["pages"] if p["text"].strip()]
        return
    for c in text_chunks:
        assert squash(c["text"]) in squash(pages[c["page"]]["text_outside_tables"]), f"page {c['page']}"
    if strategy.startswith("fixed") and config.CHUNK_STRATEGIES[strategy]["overlap"] == 0:
        for number, page in pages.items():
            joined = "".join(c["text"] for c in text_chunks if c["page"] == number)
            assert squash(joined) == squash(page["text_outside_tables"]), f"page {number}"
    else:
        for number, page in pages.items():
            has_chunk = any(c["page"] == number for c in text_chunks)
            assert has_chunk == bool(page["text_outside_tables"].strip()), f"page {number}"


@needs_parsed
@pytest.mark.parametrize("path, strategy", [case for case in REAL_CASES if case[1] in SIZED],
                         ids=[i for i, case in zip(REAL_IDS, REAL_CASES) if case[1] in SIZED])
def test_real_table_chunks_sit_on_pages_with_a_real_table_and_start_with_its_caption(path, strategy):
    size = SIZED[strategy]
    pages = {p["page"]: p for p in load_real(path)["pages"]}
    table_chunks = [c for c in real_chunks(path, strategy) if c["kind"] == "table"]
    assert table_chunks
    for c in table_chunks:
        tables = [t for t in pages[c["page"]]["tables"] if not chunk_tables.is_figure_table(t)]
        assert tables, f"table chunk on page {c['page']}, which has no table that is not a figure"
        assert ": " in c["text"], f"page {c['page']}: a table chunk holds 'Label: value' rows"
        if all(t["caption"] for t in tables):
            first_line = c["text"].split("\n")[0]
            assert any(caption_matches(first_line, t["caption"], size) for t in tables), (
                f"page {c['page']}: {first_line!r} is not the caption of a table on the page")


@needs_parsed
@pytest.mark.parametrize("path", PARSED, ids=lambda p: p.stem)
@pytest.mark.parametrize("size", sorted(set(SIZED.values())))
def test_real_every_piece_of_a_captioned_table_starts_with_the_caption(path, size):
    parsed = load_real(path)
    seen = 0
    for page in parsed["pages"]:
        for table in page["tables"]:
            pieces = chunk_tables.table_chunks(parsed["document"], page["page"], page["page_label"], table, size)
            if chunk_tables.is_figure_table(table):
                assert pieces == []
                continue
            labels, data = chunk_tables.normalise_table(table["rows"])
            assert all(len(row) == len(labels) for row in data)
            assert bool(pieces) == bool(data), f"page {page['page']} table {table['index']}"
            for piece in pieces:
                seen += 1
                assert piece["kind"] == "table" and piece["page"] == page["page"]
                assert piece["text"].strip() and approx_tokens(piece["text"]) <= size
                if table["caption"]:
                    first_line = piece["text"].split("\n")[0]
                    assert caption_matches(first_line, table["caption"], size), (
                        f"page {page['page']} table {table['index']}: {first_line!r} vs {table['caption']!r}")
    assert seen
