"""Parsed table -> table chunks: one 'Label: value' line per row, under the caption.

The rule throughout: a value is never put under the wrong label and never invented.
Where the table does not say, the cell stays empty.

The parser writes every merged cell as None, whether it is merged with the cell above or with
the cell to its left. Only two kinds are safe to read as "same as above": a None before the
first filled cell of its row (nothing to its left to be merged with) and a None in a unit
column. Any other None is taken as part of the cell to its left, which then spans those columns.
"""
import re

from rag import config
from rag.chunk_base import make_chunk

# What a value looks like: an optional sign or comparison, then a digit ('120', '±0.5', '–65 to +150').
# Used to tell values from wording: a number is never copied into another row.
NUMBER_LIKE_RE = re.compile(r"^[±+\-–−<>≤≥~]?\s?\d")

# Footnote markers, removed only at the end of a word: 'Accuracy(1)', '1.8(1)', 'GPIO16[2]', 'VDD [1, 2]'.
# Kept: a cell that is just '(1)'; a bus index or a power with more after it, 'TXD[0](O)', '2[15]-1';
# a value in brackets, the first '(2)' of '3/(2)(2)'. A footnote is never numbered 0.
MARKER_END = r"(?=$|[\s,;])"
PAREN_MARKER_RE = re.compile(r"(?<=[A-Za-z0-9)%°])(?:\([1-9]\d?\))+" + MARKER_END)
BRACKET_MARKER_RE = re.compile(r" ?\[[1-9]\d?(?:,\s*[1-9]\d?)*\]" + MARKER_END)
LINE_BREAK_RE = re.compile(r"\s*[\r\n]+\s*")

# A caption may take at most this share of a chunk; the rest is for rows.
CAPTION_SHARE = 0.25
# When a row is split, its first 'Label: value' pair is repeated in every part, cut to this share of the room.
LEAD_SHARE = 0.5

PAIR_SEP = ", "
# A group header longer than this is not a header but text caught in the table's first row
# (ESP32 page 11: a figure's labels above Table 1-1); the sub-column names are used alone.
MAX_GROUP_CHARS = 60
# Joins the labels of the columns a spanning cell covers: 'LM35 MIN / LM35 TYP / LM35 MAX: ±0.4'.
SPAN_SEP = " / "


def is_figure_table(table: dict) -> bool:
    """True for a figure that the parser kept as a table (caption 'Figure n ...')."""
    caption = table.get("caption")
    return bool(caption) and caption.strip().lower().startswith("figure")


def is_number_like(value: str) -> bool:
    return bool(NUMBER_LIKE_RE.match(value.strip()))


def clean_cell(cell: str | None) -> str | None:
    """Remove footnote markers and line breaks. None (a merged cell) stays None."""
    if cell is None:
        return None
    cell = LINE_BREAK_RE.sub(" ", cell)
    cell = BRACKET_MARKER_RE.sub("", cell)
    cell = PAREN_MARKER_RE.sub("", cell)
    return cell.strip()


def starts_span(top: list[str | None], i: int) -> bool:
    """True if the row-0 cell is a group header that covers the next column too ('Pins', None, None ...)."""
    return bool(top[i]) and i + 1 < len(top) and top[i + 1] is None


def header_row_count(rows: list[list[str | None]]) -> int:
    """1 or 2. Row 1 is a header row when it only names sub-columns of row 0."""
    if len(rows) < 2 or not rows[1]:
        return 1
    top, second = rows[0], rows[1]
    filled = [i for i, cell in enumerate(second) if cell]
    if not filled or any(is_number_like(second[i]) for i in filled):
        return 1
    if not second[0]:
        # Sub-columns under blank row-0 cells: 'LM35A' printed over MIN TYP MAX.
        return 2 if any(not top[i] for i in filled) else 1
    # Sub-columns from the first column on: every name sits under a group header or the cells it covers,
    # as in 'Pins' over LQFP64, LQFP100, LQFP144.
    under_group = all(top[i] is None or starts_span(top, i) for i in filled)
    return 2 if under_group and starts_span(top, 0) else 1


def single_header_labels(top: list[str | None]) -> list[str]:
    """One label per column. A None cell is covered by the header to its left."""
    labels: list[str] = []
    for cell in top:
        if cell is None:
            labels.append(labels[-1] if labels else "")
        else:
            labels.append(cell)
    return labels


def sub_column_runs(second: list[str | None]) -> list[list[int]]:
    """The unbroken runs of columns that have a row-1 label."""
    runs: list[list[int]] = []
    for i, cell in enumerate(second):
        if not cell:
            continue
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def group_for_run(run: list[int], top: list[str | None]) -> dict[int, str]:
    """Column -> group label, for one run of sub-columns."""
    groups = [i for i in run if top[i]]
    if not groups:
        return {}
    if len(run) % len(groups) == 0:
        # 'LM35A' printed over the middle one of MIN TYP MAX: share the run out evenly.
        share = len(run) // len(groups)
        shared = {col: top[groups[n // share]] for n, col in enumerate(run)}
    else:
        shared = {col: top[min(groups, key=lambda g: (abs(g - col), g))] for col in run}
    # What the header itself says wins over the sharing: a filled cell names its own column,
    # and a None cell is covered by the group to its left ('Max', None, None over three columns).
    group: dict[int, str] = {}
    for col in run:
        if top[col]:
            group[col] = top[col]
        elif top[col] is None and col - 1 in group:
            group[col] = group[col - 1]
        else:
            group[col] = shared[col]
    return group


def double_header_labels(top: list[str | None], second: list[str | None]) -> list[str]:
    """Labels from two header rows: '<group> <sub>' for a sub-column, the row-0 label otherwise."""
    group: dict[int, str] = {}
    for run in sub_column_runs(second):
        group.update(group_for_run(run, top))
    labels: list[str] = []
    for i, cell in enumerate(top):
        if second[i]:
            named = i in group and len(group[i]) <= MAX_GROUP_CHARS
            labels.append(f"{group[i]} {second[i]}" if named else second[i])
        elif cell is None:
            labels.append(labels[-1] if labels else "")
        else:
            labels.append(cell)
    return labels


def is_full_width(row: list[str | None]) -> bool:
    """A heading row inside the table: only the first cell is filled and the rest are merged into it."""
    return bool(row[0]) and all(cell is None for cell in row[1:])


def is_unit_column(label: str) -> bool:
    return "unit" in label.lower()


def fill_down(rows: list[list[str | None]], labels: list[str]) -> tuple[list[list[str]], list[dict[int, list[int]]], int, int]:
    """Resolve the merged (None) cells of the data rows.

    Returns the rows, for each row the columns each spanning cell covers ({column: [columns to its right]}),
    the number of cells filled from above, and the number kept empty because the cell above holds a number.

    Only wording is copied down (a unit, a parameter name), and only where the None can not belong to
    the cell on its left. A number is never copied: a value put in the wrong row is a wrong answer.
    """
    out: list[list[str]] = []
    spans: list[dict[int, list[int]]] = []
    filled = blocked = 0
    above = None
    for row in rows:
        full_width = is_full_width(row)
        new: list[str] = []
        span: dict[int, list[int]] = {}
        seen = False  # a filled cell so far in this row
        owner = None  # the filled cell a None may be merged into
        for i, cell in enumerate(row):
            if cell is not None:
                new.append(cell)
                seen = seen or bool(cell)
                owner = i if cell else None
            elif full_width:
                new.append("")
            elif not seen or is_unit_column(labels[i]):
                owner = None
                if above is None or not above[i]:
                    new.append("")
                elif is_number_like(above[i]):
                    new.append("")
                    blocked += 1
                else:
                    new.append(above[i])
                    filled += 1
            else:
                new.append("")
                if owner is not None:
                    span.setdefault(owner, []).append(i)
        out.append(new)
        spans.append(span)
        # A heading row inside the table is not a source: its wording does not belong to the rows below.
        above = None if full_width else new
    return out, spans, filled, blocked


def prepare(rows: list[list[str | None]]) -> list[list[str | None]]:
    """Clean every cell and pad short rows, so every row has the same number of columns."""
    width = max((len(row) for row in rows), default=0)
    return [[clean_cell(cell) for cell in row] + [""] * (width - len(row)) for row in rows]


def normalise_with_spans(rows: list[list[str | None]]) -> tuple[list[str], list[list[str]], list[dict[int, list[int]]]]:
    """(labels, data_rows, spans): normalise_table plus, per data row, the columns each spanning cell covers."""
    rows = prepare(rows)
    if not rows or not rows[0]:
        return [], [], []
    headers = header_row_count(rows)
    labels = double_header_labels(rows[0], rows[1]) if headers == 2 else single_header_labels(rows[0])
    data, spans, _, _ = fill_down(rows[headers:], labels)
    kept = [(row, span) for row, span in zip(data, spans) if any(row)]
    return labels, [row for row, _ in kept], [span for _, span in kept]


def normalise_table(rows: list[list[str | None]]) -> tuple[list[str], list[list[str]]]:
    """(labels, data_rows): one label per column; every cell a str, every row as long as labels."""
    labels, data, _ = normalise_with_spans(rows)
    return labels, data


def span_label(labels: list[str], columns: list[int]) -> str:
    """The label of a cell that covers several columns: their different labels, joined."""
    return SPAN_SEP.join(dict.fromkeys(labels[i] for i in columns if labels[i]))


def row_pairs(labels: list[str], row: list[str], span: dict[int, list[int]] | None = None) -> list[str]:
    """'Label: value' for each non-empty cell; the value alone where the column has no label."""
    pairs = []
    for i, cell in enumerate(row):
        if not cell:
            continue
        label = span_label(labels, [i, *(span or {}).get(i, [])])
        pairs.append(f"{label}: {cell}" if label else cell)
    return pairs


def row_sentence(labels: list[str], row: list[str]) -> str:
    """'Parameter: VDD, Min: 3.0, Max: 3.6, Unit: V'. Empty cells are left out."""
    return PAIR_SEP.join(row_pairs(labels, row))


def hard_cut(text: str, size: int) -> list[str]:
    size = max(1, size)
    return [text[i:i + size] for i in range(0, len(text), size)]


def wrap(text: str, size: int) -> list[str]:
    """Pieces of at most `size` chars, cut between words; only a single over-long word is cut inside."""
    pieces: list[str] = []
    for word in text.split(" "):
        if pieces and len(pieces[-1]) + 1 + len(word) <= size:
            pieces[-1] += " " + word
        else:
            pieces.extend(hard_cut(word, size) or [""])
    return pieces


def split_row(pairs: list[str], room: int) -> list[str]:
    """Split one long row between cells into lines of at most `room` chars.

    Every line starts with the row's first pair, so each part still says which row it is.
    """
    lead_max = max(1, int(room * LEAD_SHARE))
    lead, rest = pairs[0][:lead_max], pairs[0][lead_max:]
    item_room = room - len(lead) - len(PAIR_SEP)
    items = [piece for pair in ([rest] if rest else []) + pairs[1:] for piece in wrap(pair, item_room)]
    lines: list[str] = []
    line = lead
    for item in items:
        if line != lead and len(line) + len(PAIR_SEP) + len(item) > room:
            lines.append(line)
            line = lead
        line += PAIR_SEP + item
    lines.append(line)
    return lines


def pack_lines(rows: list[list[str]], room: int) -> list[list[str]]:
    """Group the row lines into pieces of at most `room` chars, whole rows where they fit."""
    pieces: list[list[str]] = []
    current: list[str] = []
    used = 0
    for pairs in rows:
        line = PAIR_SEP.join(pairs)
        if len(line) > room:
            # Too long even alone: each part of the row gets a piece of its own.
            if current:
                pieces.append(current)
            pieces.extend([part] for part in split_row(pairs, room))
            current, used = [], 0
            continue
        if current and used + 1 + len(line) > room:
            pieces.append(current)
            current, used = [], 0
        used += len(line) + (1 if current else 0)
        current.append(line)
    if current:
        pieces.append(current)
    return pieces


def caption_line(caption: str | None, max_tokens: int) -> str:
    """The caption as one line, cut to its share of the chunk. '' when there is none."""
    if not caption:
        return ""
    limit = int(max_tokens * CAPTION_SHARE) * config.CHARS_PER_TOKEN
    return " ".join(caption.split())[:limit].rstrip()


def table_chunks(document: str, page: int, page_label: str | None, table: dict, max_tokens: int) -> list[dict]:
    """The table as chunks of at most max_tokens: caption line, then one line per row."""
    if is_figure_table(table):
        return []
    labels, data, spans = normalise_with_spans(table["rows"])
    rows = [row_pairs(labels, row, span) for row, span in zip(data, spans)]
    if not rows:
        return []
    caption = caption_line(table.get("caption"), max_tokens)
    prefix = caption + "\n" if caption else ""
    room = max_tokens * config.CHARS_PER_TOKEN - len(prefix)
    return [
        make_chunk(document, page, page_label, None, "table", prefix + "\n".join(lines))
        for lines in pack_lines(rows, room)
    ]
