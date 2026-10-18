"""Cycle 2 step 6 tests for web/index.html: the datasheet list is written into the page (so loading it never wakes
the paused database) and must stay equal to config.DOCUMENTS. No network and no database."""
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
