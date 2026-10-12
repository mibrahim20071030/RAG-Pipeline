"""Layer 1 tests. The datasheet tests need the PDFs in data/pdfs (not in git)."""
import re
from pathlib import Path

import pdfplumber
import pytest

from rag import config, parse

PDFS = sorted(config.PDF_DIR.glob("*.pdf"))
PARSE_SOURCE = Path(parse.__file__)


@pytest.fixture(scope="session")
def parsed_docs() -> dict[str, dict]:
    """Parsed output per PDF. Re-parses when the JSON is missing or older than its inputs."""
    docs = {}
    for pdf in PDFS:
        out = parse.parsed_path(pdf.stem)
        newest_input = max(pdf.stat().st_mtime, PARSE_SOURCE.stat().st_mtime)
        if not out.exists() or out.stat().st_mtime < newest_input:
            parse.save_parsed(parse.parse_pdf(pdf))
        docs[pdf.name] = parse.load_parsed(pdf.stem)
    return docs


def line(text: str, y0: float, height: float = 0.01) -> dict:
    return {"text": text, "y0": y0, "y1": y0 + height}


# --- real datasheets ---------------------------------------------------------

needs_pdfs = pytest.mark.skipif(not PDFS, reason="no PDFs in data/pdfs")


@needs_pdfs
@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: p.name)
def test_page_count_matches_pdf(pdf, parsed_docs):
    doc = parsed_docs[pdf.name]
    with pdfplumber.open(pdf) as f:
        assert doc["page_count"] == len(f.pages)
    assert len(doc["pages"]) == doc["page_count"]


@needs_pdfs
@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: p.name)
def test_every_item_has_its_page_number(pdf, parsed_docs):
    doc = parsed_docs[pdf.name]
    assert [p["page"] for p in doc["pages"]] == list(range(1, doc["page_count"] + 1))
    for page in doc["pages"]:
        for table in page["tables"]:
            assert table["page"] == page["page"]
            assert table["rows"], f"empty table on page {page['page']}"


@needs_pdfs
@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: p.name)
def test_no_page_is_empty_unless_blank_in_pdf(pdf, parsed_docs):
    doc = parsed_docs[pdf.name]
    empty = [p["page"] for p in doc["pages"] if not p["text"].strip() and not p["tables"]]
    with pdfplumber.open(pdf) as f:
        not_blank = [n for n in empty if f.pages[n - 1].chars]
    assert not not_blank, f"pages with text in the PDF but nothing parsed: {not_blank}"


@needs_pdfs
@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: p.name)
def test_not_scanned_and_no_private_use_glyphs(pdf, parsed_docs):
    stats = parsed_docs[pdf.name]["stats"]
    assert stats["image_only_pages"] == []
    assert stats["private_use_chars"] == 0


@needs_pdfs
@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: p.name)
def test_table_rows_have_one_width(pdf, parsed_docs):
    for page in parsed_docs[pdf.name]["pages"]:
        for table in page["tables"]:
            assert len({len(row) for row in table["rows"]}) == 1


@needs_pdfs
@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: p.name)
def test_table_text_is_also_in_page_text(pdf, parsed_docs):
    """`text` holds the whole page; `text_outside_tables` leaves the tables out."""
    for page in parsed_docs[pdf.name]["pages"]:
        if page["tables"]:
            assert len(page["text_outside_tables"]) < len(page["text"])
        else:
            assert page["text_outside_tables"] == page["text"]


# --- helpers, no PDF needed --------------------------------------------------

def test_repeated_header_and_footer_are_flagged():
    pages = [
        [line("Electrical characteristics STM32F20xxx", 0.07), line("Body text", 0.4), line(f"DS6329 Rev 18 {n}/181", 0.89)]
        for n in range(1, 5)
    ]
    assert parse.repeated_margin_lines(pages) == [[True, False, True]] * 4


def test_line_repeated_on_two_pages_is_kept():
    pages = [[line("Device marking", 0.07)], [line("Device marking", 0.07)], [line("Other", 0.07)]]
    assert parse.repeated_margin_lines(pages) == [[False], [False], [False]]


def test_caption_and_table_header_row_at_page_top_are_kept():
    caption = line("Table 96. Document revision history (continued)", 0.07)
    header_row = {**line("Parameter Description Min Typ Max Unit", 0.07), "in_table": True}
    pages = [[caption, header_row]] * 4
    assert parse.repeated_margin_lines(pages) == [[False, False]] * 4


def test_same_text_at_a_different_height_is_kept():
    pages = [[line("25", 0.95)], [line("25", 0.95)], [line("25", 0.95)], [line("25", 0.90)]]
    assert parse.repeated_margin_lines(pages) == [[True], [True], [True], [False]]


def test_page_label_is_the_number_that_changes():
    removed = [[f"DS6329 Rev 18 {n}/181"] for n in (70, 71, 72, 73)]
    assert parse.page_labels(removed) == ["70", "71", "72", "73"]


def test_page_label_missing_when_no_number_changes():
    assert parse.page_labels([["Rev. 2.0.A"]] * 4) == [None] * 4


def test_page_label_filled_in_across_landscape_pages():
    removed = [["Page 13 of 181"], [], [], ["Page 16 of 181"], ["Page 17 of 181"]]
    assert parse.page_labels(removed) == ["13", "14", "15", "16", "17"]


def test_symbols_are_normalised():
    assert parse.clean_text("10 μA, 2 kΩ, −5 V") == "10 µA, 2 kΩ, -5 V"
    assert parse.clean_text("k 150  3.6") == "kΩ ±150 ≤ 3.6"
    assert not re.search(r"[-]", parse.clean_text(" itemone"))


def test_clean_cell_keeps_merged_cell_marker():
    assert parse.clean_cell(None) is None
    assert parse.clean_cell("TESTED\nLIMIT(1)") == "TESTED LIMIT(1)"


def test_is_data_table_rejects_boxes():
    assert not parse.is_data_table([["", ""], ["", None]])
    assert not parse.is_data_table([["only one row", "x"]])
    assert parse.is_data_table([["Symbol", "Max"], ["IVDD", "120"]])


def test_caption_prefers_table_line_then_heading():
    table = {"bbox": [50, 200, 500, 400]}
    above = [
        {"text": "6.5 Electrical Characteristics: LM35A", "top": 140, "bottom": 150},
        {"text": "These specifications apply in the circuit of Figure 14.", "top": 180, "bottom": 190},
    ]
    assert parse.find_caption(table, above) == "6.5 Electrical Characteristics: LM35A"
    above.append({"text": "T able 24. Typical current", "top": 190, "bottom": 198})
    assert parse.find_caption(table, above) == "Table 24. Typical current"


def test_caption_ignores_a_sentence_that_mentions_a_table():
    table = {"bbox": [50, 200, 500, 400]}
    above = [
        {"text": "TABLE 5-3: DC CHARACTERISTICS", "top": 150, "bottom": 160},
        {"text": "Table 10 gives the list of all the pins.", "top": 185, "bottom": 195},
    ]
    assert parse.find_caption(table, above) == "TABLE 5-3: DC CHARACTERISTICS"


def test_figure_grid_is_recognised_but_a_figure_reference_is_not():
    assert parse.FIGURE_RE.search("400\n300\nFigure 1. Thermal Resistance Junction To Air")
    assert not parse.FIGURE_RE.search("Figure 21: updated the timing diagram")
    assert not parse.FIGURE_RE.search("In circuit of\nFigure 14, IL = 0")


def test_caption_is_inherited_only_by_a_table_at_the_top_of_the_page():
    def page(caption):
        return {"tables": [{"caption": caption, "rows": [["a", "b"]]}]}

    pages = [page("Table 12. Current consumption"), page(None), page(None)]
    parse.inherit_captions(pages, [True, True, False])
    assert pages[1]["tables"][0]["caption"] == "Table 12. Current consumption (continued)"
    assert pages[2]["tables"][0]["caption"] is None
