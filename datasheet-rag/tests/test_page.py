"""Cycle 2 step 6 tests for web/index.html: the datasheet list is written into the page (so loading it never wakes
the paused database) and must stay equal to config.DOCUMENTS. No network and no database."""
import re
from html.parser import HTMLParser

from rag import config


class _Options(HTMLParser):
    """The (value, label) pairs of the <select id="document"> options, and whether the page fetches /api/documents."""

    def __init__(self):
        super().__init__()
        self.options, self._in_select, self._value = [], False, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and attrs.get("id") == "document":
            self._in_select = True
        elif tag == "option" and self._in_select:
            self._value = attrs.get("value")
            self.options.append([self._value, ""])

    def handle_endtag(self, tag):
        if tag == "select":
            self._in_select = False

    def handle_data(self, data):
        if self._in_select and self.options and self._value is not None:
            self.options[-1][1] += data


def page_text() -> str:
    return (config.WEB_DIR / "index.html").read_text(encoding="utf-8")


def options() -> list[tuple[str, str]]:
    parser = _Options()
    parser.feed(page_text())
    return [(value, label.strip()) for value, label in parser.options]


def test_first_option_is_all_datasheets():
    assert options()[0] == ("", "All datasheets")


def test_datasheets_in_the_page_match_the_config():
    in_page = dict(options()[1:])
    assert in_page == {name: entry["part"] for name, entry in config.DOCUMENTS.items()}


def test_loading_the_page_does_not_ask_the_api_for_the_list():
    assert "/api/documents" not in page_text()


def test_pipeline_toggle_offers_ours_and_the_knowledge_base():
    # Cycle 2 step 8: the two values must be config.PIPELINES, the names api.check_pipeline accepts.
    found = re.findall(r'data-pipeline="(\w+)"', page_text())
    assert tuple(found) == config.PIPELINES
    assert 'data-pipeline="ours" aria-pressed="true"' in page_text()  # ours is the default


def test_the_page_knows_the_fixed_refusal_sentence():
    # The page shows a refusal in the model's own words under its notice, and needs the fixed sentence to tell them
    # apart.
    assert f'const NOT_CONTAINED = "{config.NOT_CONTAINED}";' in page_text()



def test_the_page_offers_an_upload_within_the_configured_size():
    text = page_text()
    assert 'id="upload-file"' in text and 'accept="application/pdf,.pdf"' in text
    assert f"const MAX_BYTES = {config.UPLOAD_MAX_BYTES // (1024 * 1024)} * 1024 * 1024;" in text
    assert f"up to {config.UPLOAD_MAX_BYTES // (1024 * 1024)} MB and {config.UPLOAD_MAX_PAGES} pages" in text
    assert f"Kept for {config.UPLOAD_KEEP_HOURS} hours" in text


def test_the_page_knows_the_upload_document_prefix():
    # "Open page" finds the visitor's own copy from the source's document name.
    assert f'.slice("{config.UPLOAD_PREFIX}".length)' in page_text()
