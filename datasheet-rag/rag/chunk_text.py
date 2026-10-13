"""Text chunking: headings, sections and the page, fixed-size and structure-aware strategies.

Every strategy returns a list of chunks made with chunk_base.make_chunk, in page order.
A chunk never crosses a page. Sizes are in approximate tokens (chunk_base.approx_tokens).
Tables are chunked separately in rag/chunk_tables.py.
"""
import re

from rag import config
from rag.chunk_base import make_chunk

# A numbered section heading: '3.2 Internal LDO', '9. Electrical characteristics', '3.16.1 Regulator ON'.
# The number has no leading zero, so a figure label such as '0 Times' is not a heading.
HEADING_RE = re.compile(r"^[1-9]\d?(?:\.\d{1,2}){0,3}\.?\s+([A-Z].*)$")
MAX_HEADING_CHARS = 80
# A title has at least one run of this many lowercase letters; '0.01 PF BYPASS' and '1.2 V' have none.
LOWERCASE_RUN_RE = re.compile(r"[a-z]{3,}")
# A line that ends with a page number is a contents entry: '4.2.2 RTC Clock 29'.
PAGE_NUMBER_END_RE = re.compile(r"\s\d{1,3}$")
# A heading does not end like a sentence, a list or a footnote that introduces a list ('1. Function names:').
BAD_HEADING_ENDS = (".", ",", ":")
# Two-column pages glue a heading to body text: '2 Applications especially easy. The device is ...'.
SENTENCE_BREAK_RE = re.compile(r"[a-z]\.\s")
# A number followed by a unit is a value or a figure label, not a heading: '1 Mbyte', '2.4 GHz Wi-Fi + ...'.
UNIT_WORDS = {"Mbyte", "Kbyte", "Mbit", "Kbit", "Mbps", "Kbps", "GHz", "MHz", "Hz"}

# A contents entry: a section number, a title, then dot leaders or spaces and a page number.
CONTENTS_LINE_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+[A-Za-z].*\s\d{1,3}$")
# A page with at least this many contents entries is a contents page and has no headings.
CONTENTS_MIN_LINES = 5

# A line that starts a new paragraph-like unit: a bullet or a marker such as '(1)' or '(a)'.
BULLET_RE = re.compile(r"^(?:[•\-–*]|\(\w{1,2}\))")
# A line that ends a unit.
UNIT_ENDS = (".", ":", "?")


def is_heading(line: str) -> bool:
    """True if the stripped line is a numbered section heading."""
    match = HEADING_RE.match(line)
    if not match or len(line) > MAX_HEADING_CHARS:
        return False
    title = match.group(1)
    if PAGE_NUMBER_END_RE.search(line) or line.endswith(BAD_HEADING_ENDS):
        return False
    if SENTENCE_BREAK_RE.search(title) or title.split()[0] in UNIT_WORDS:
        return False
    return bool(LOWERCASE_RUN_RE.search(title))


def is_contents_page(lines: list[str]) -> bool:
    """True if the page lists sections with their page numbers."""
    return sum(1 for line in lines if CONTENTS_LINE_RE.match(line.strip())) >= CONTENTS_MIN_LINES


def find_headings(page_text: str) -> list[tuple[int, str]]:
    """Numbered section headings in a page's text, as (line index, heading)."""
    lines = page_text.split("\n")
    if is_contents_page(lines):
        return []
    return [(i, line.strip()) for i, line in enumerate(lines) if is_heading(line.strip())]


def sections_by_page(parsed: dict) -> dict[int, str | None]:
    """For every page number, the section in effect at the top of that page."""
    sections: dict[int, str | None] = {}
    current = None
    for page in parsed["pages"]:
        sections[page["page"]] = current
        headings = find_headings(page["text_outside_tables"])
        if headings:
            current = headings[-1][1]
    return sections


def line_sections(page_text: str, carried: str | None) -> list[str | None]:
    """The section in effect at each line of the page; `carried` applies above the first heading."""
    headings = dict(find_headings(page_text))
    sections = []
    for i in range(len(page_text.split("\n"))):
        carried = headings.get(i, carried)
        sections.append(carried)
    return sections


def text_chunk(parsed: dict, page: dict, section: str | None, text: str) -> dict:
    return make_chunk(parsed["document"], page["page"], page["page_label"], section, "text", text)


def page_chunks(parsed: dict) -> list[dict]:
    """One chunk per page that has text: the whole page, tables included as raw text. No size limit."""
    carried = sections_by_page(parsed)
    chunks = []
    for page in parsed["pages"]:
        text = page["text"].strip()
        if not text:
            continue
        outside = page["text_outside_tables"]
        # The page's own first line wins when it is a heading.
        section = carried[page["page"]]
        first = next((i for i, line in enumerate(outside.split("\n")) if line.strip()), None)
        section = dict(find_headings(outside)).get(first, section)
        chunks.append(text_chunk(parsed, page, section, text))
    return chunks


def hard_cut(word: str, max_chars: int) -> list[str]:
    """The word itself, or pieces of at most max_chars when it is too long for one chunk."""
    return [word[i:i + max_chars] for i in range(0, len(word), max_chars)]


def page_words(page_text: str, max_chars: int) -> list[tuple[str, int]]:
    """The page's words in order as (word, line index). An over-long word is hard-cut."""
    words = []
    for i, line in enumerate(page_text.split("\n")):
        for word in line.split():
            words.extend((piece, i) for piece in hard_cut(word, max_chars))
    return words


def joined_len(words: list[str]) -> int:
    """Length of the words joined by single spaces."""
    return sum(map(len, words)) + max(len(words) - 1, 0)


def overlap_tail(words: list[str], overlap_chars: int, room: int) -> list[str]:
    """The longest run of last words that fits in overlap_chars and leaves `room` chars free for a new word."""
    tail: list[str] = []
    for word in reversed(words):
        longer = [word, *tail]
        if joined_len(longer) > min(overlap_chars, room):
            break
        tail = longer
    return tail


def fixed_word_groups(words: list[tuple[str, int]], max_chars: int, overlap_chars: int) -> list[tuple[list[str], int]]:
    """Cut one page's words into chunks, as (chunk words, line index of the first new word)."""
    groups = []
    current: list[str] = []
    start_line = 0
    for word, line in words:
        if current and joined_len([*current, word]) > max_chars:
            groups.append((current, start_line))
            # The tail must leave room for this word and the space before it.
            current = overlap_tail(current, overlap_chars, max_chars - len(word) - 1) if overlap_chars else []
            start_line = line
        elif not current:
            start_line = line
        current = [*current, word]
    if current:
        groups.append((current, start_line))
    return groups


def fixed_chunks(parsed: dict, size: int, overlap: int = 0) -> list[dict]:
    """Chunks of at most `size` tokens, cut at words. `overlap` tokens of each chunk repeat in the next."""
    max_chars = size * config.CHARS_PER_TOKEN
    overlap_chars = overlap * config.CHARS_PER_TOKEN
    carried = sections_by_page(parsed)
    chunks = []
    for page in parsed["pages"]:
        outside = page["text_outside_tables"]
        sections = line_sections(outside, carried[page["page"]])
        for words, line in fixed_word_groups(page_words(outside, max_chars), max_chars, overlap_chars):
            chunks.append(text_chunk(parsed, page, sections[line], " ".join(words)))
    return chunks


def section_blocks(page_text: str, carried: str | None) -> list[tuple[str | None, list[str]]]:
    """The page's lines grouped as (section, lines). A heading line starts a new block."""
    headings = dict(find_headings(page_text))
    blocks: list[tuple[str | None, list[str]]] = []
    for i, line in enumerate(page_text.split("\n")):
        if i in headings:
            carried = headings[i]
            blocks.append((carried, []))
        if not line.strip():
            continue
        if not blocks:
            blocks.append((carried, []))
        blocks[-1][1].append(line.strip())
    return merge_bare_headings([block for block in blocks if block[1]], set(headings.values()))


def merge_bare_headings(blocks: list[tuple[str | None, list[str]]], headings: set[str]) -> list[tuple[str | None, list[str]]]:
    """Join a heading that has no text of its own ('2 Pins' right above '2.1 Pin Layout') to the block below it.

    On its own it would be a chunk that holds a title and no content.
    """
    merged: list[tuple[str | None, list[str]]] = []
    carry: list[str] = []
    for section, lines in blocks:
        if len(lines) == 1 and lines[0] in headings:
            carry.append(lines[0])
            continue
        merged.append((section, [*carry, *lines]))
        carry = []
    if carry:
        # Headings at the foot of the page, with nothing below them to join.
        merged.append((carry[-1], carry))
    return merged


def split_units(lines: list[str]) -> list[list[str]]:
    """Group lines into paragraph-like units: a new one starts at a bullet and after a line that ends a sentence."""
    units: list[list[str]] = []
    closed = True
    for line in lines:
        if closed or BULLET_RE.match(line):
            units.append([])
        units[-1].append(line)
        closed = line.endswith(UNIT_ENDS)
    return units


def split_long_line(line: str, max_chars: int) -> list[str]:
    """The line itself, or pieces cut at words when it is too long for one chunk."""
    if len(line) <= max_chars:
        return [line]
    words = [(piece, 0) for word in line.split() for piece in hard_cut(word, max_chars)]
    return [" ".join(group) for group, _ in fixed_word_groups(words, max_chars, 0)]


def fit_units(units: list[list[str]], max_chars: int) -> list[list[str]]:
    """Break a unit that is too large for one chunk into one-line units, cutting over-long lines at words."""
    fitted = []
    for unit in units:
        if joined_len(unit) <= max_chars:
            fitted.append(unit)
        else:
            fitted.extend([piece] for line in unit for piece in split_long_line(line, max_chars))
    return fitted


def pack_units(units: list[list[str]], max_chars: int) -> list[list[str]]:
    """Pack whole units into chunks of lines, each at most max_chars when joined."""
    packed: list[list[str]] = []
    for unit in units:
        if packed and joined_len([*packed[-1], *unit]) <= max_chars:
            packed[-1] = [*packed[-1], *unit]
        else:
            packed.append(unit)
    return packed


def structure_chunks(parsed: dict, size: int) -> list[dict]:
    """Chunks that follow the document: cut at headings, then packed from whole paragraphs up to `size` tokens."""
    max_chars = size * config.CHARS_PER_TOKEN
    carried = sections_by_page(parsed)
    chunks = []
    for page in parsed["pages"]:
        for section, lines in section_blocks(page["text_outside_tables"], carried[page["page"]]):
            for chunk_lines in pack_units(fit_units(split_units(lines), max_chars), max_chars):
                chunks.append(text_chunk(parsed, page, section, "\n".join(chunk_lines)))
    return chunks
