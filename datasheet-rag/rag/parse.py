"""PDF -> pages, text, tables. Saves runs/parsed/<doc>.json so later layers do not re-parse.

Run: python -m rag.parse            (all PDFs in data/pdfs)
     python -m rag.parse lm35.pdf   (one file)
"""
import json
import re
import sys
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

import pdfplumber

from rag import config

# pdfplumber's default x_tolerance of 3 glues words together in the ESP32 and LM35 files.
X_TOLERANCE = 1.5

# A char this much smaller than the char before it is treated as a subscript or superscript.
SCRIPT_SIZE_RATIO = 0.85

# Header and footer detection: a line in the top or bottom band of the page that
# repeats (digits ignored) at the same height on at least this many pages.
TOP_BAND = 0.09
BOTTOM_BAND = 0.88
MIN_REPEAT_PAGES = 3
SAME_HEIGHT = 0.02

# A word must reach this many points past a cell border on both sides to count as cut by it.
SPLIT_WORD_MARGIN = 2

# Above this share of image-only pages the file is treated as scanned.
SCANNED_SHARE = 0.2

CAPTION_RE = re.compile(r"^(table|figure)\s+\d+", re.IGNORECASE)
# A caption as it appears above a table: 'Table 12.', 'Table 5-3.', 'TABLE 1:'. The number must
# end in '.' or ':' so a sentence such as 'Table 10 gives ...' is not taken for a caption.
# Tolerates a stray leading char and 'T able'.
TABLE_CAPTION_RE = re.compile(r"^.{0,3}?\b((?:T ?able|Figure) \d+(?:-\d+)?[.:].*)$", re.IGNORECASE)
HEADING_RE = re.compile(r"^\d+(\.\d+)*\.?\s+[A-Z]")
# A figure caption inside a cell ('Figure 1. Thermal ...') marks a grid of graphs, not a table.
# The '.' keeps real tables that only mention a figure ('Figure 21: updated', 'see Figure 14').
FIGURE_RE = re.compile(r"^figure\s+\d+\.\s", re.IGNORECASE | re.MULTILINE)
CAPTION_REACH = 90   # points above a table searched for a 'Table n' line or a heading
FALLBACK_REACH = 45  # points above a table searched for any short line

REPLACEMENTS = {
    "μ": "µ",  # Greek mu -> micro sign, so "µA" is one spelling everywhere
    "Ω": "Ω",  # ohm sign -> Greek omega
    "−": "-",       # minus sign
    " ": " ",
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    # Symbol font (STM32 file): pdfplumber returns U+F000 plus the Symbol byte.
    "": " ", "": "(", "": ")", "": "+", "": "-",
    "": "<", "": "=", "": ">", "": "[", "": "]",
    "": "Δ", "": "Θ", "": "Σ", "": "Ω",
    "": "α", "": "β", "": "µ", "": "θ",
    "": "σ", "": "τ",
    "": "≤", "": "/", "": "∞", "": "°",
    "": "±", "": "≥", "": "×", "": "•",
    "": "≠", "": "≈", "": "⋅",
    # Pieces of a tall brace (ESP32 file): drawing only, no text.
    "": " ", "": " ", "": " ", "": " ",
}
_REPLACE_RE = re.compile("|".join(map(re.escape, REPLACEMENTS)))
_PRIVATE_USE_RE = re.compile(r"[-�]")


class ScannedPdfError(Exception):
    """The PDF has pages with images and no selectable text. Decide on OCR first."""


def clean_text(text: str) -> str:
    return _REPLACE_RE.sub(lambda m: REPLACEMENTS[m.group()], text)


def clean_cell(cell: str | None) -> str | None:
    """None marks a cell covered by a merged neighbour; '' is an empty cell."""
    if cell is None:
        return None
    return " ".join(clean_text(cell).split())


def line_template(text: str) -> str:
    """The line with digits ignored, so 'Rev 18 70/181' and 'Rev 18 71/181' match."""
    return " ".join(re.sub(r"\d+", "#", text).lower().split())


def snap_scripts(page) -> int:
    """Move subscripts and superscripts onto the line of the char they follow.

    Without this 'VDD' comes out as 'V' on one line and 'DD' on the next. The char
    dicts are changed in place, so it must run before any text or table extraction.
    """
    base = last = None
    moved = 0
    for c in page.chars:
        if not c.get("upright", True):
            base = last = None
            continue
        if base is not None and c["size"] <= SCRIPT_SIZE_RATIO * base["size"]:
            overlap = min(c["bottom"], base["bottom"]) - max(c["top"], base["top"])
            gap = c["x0"] - last["x1"]
            if overlap > 0 and -1 <= gap <= base["size"]:
                if abs(c["bottom"] - base["bottom"]) > 0.01:
                    moved += 1
                c["doctop"] += base["top"] - c["top"]
                c["top"], c["bottom"] = base["top"], base["bottom"]
                last = c
                continue
        base = last = c
    return moved


def text_kwargs(page) -> dict:
    kwargs = {"x_tolerance": X_TOLERANCE}
    # Vertical labels on an unrotated page read bottom to top in all four files.
    if page.rotation in (0, 180):
        kwargs["char_dir_rotated"] = "btt"
    return kwargs


def extract_lines(page, kwargs: dict) -> list[dict]:
    """Text lines with their position as a fraction of the page height."""
    lines = []
    for line in page.extract_text_lines(return_chars=False, **kwargs):
        text = clean_text(line["text"]).strip()
        if text:
            lines.append({
                "text": text,
                "x0": line["x0"],
                "x1": line["x1"],
                "top": line["top"],
                "bottom": line["bottom"],
                "y0": line["top"] / page.height,
                "y1": line["bottom"] / page.height,
            })
    return lines


def is_data_table(rows: list[list[str | None]]) -> bool:
    """Reject boxes, figure grids and other ruled areas that are not tables."""
    if len(rows) < 2 or max(len(r) for r in rows) < 2:
        return False
    filled_rows = sum(1 for r in rows if sum(1 for c in r if c) >= 2)
    return filled_rows >= 2


def rejoin_split_cells(page, found, raw: list[list[str | None]], kwargs: dict) -> int:
    """Put back together a cell that was cut in two through the middle of a word.

    A header that spans two columns ('TEST CONDITIONS') can be detected as two cells, which
    cuts the word where the column line would be ('TEST CONDI' | 'TIONS'). When a word
    crosses the border between two neighbouring cells, the two are read as one cell and
    the right one becomes a merged-cell marker (None). Returns how many were rejoined.
    """
    words = [w for w in page.extract_words(**kwargs) if w["upright"]]

    def word_crosses(border: float, top: float, bottom: float) -> bool:
        return any(
            w["x0"] < border - SPLIT_WORD_MARGIN and w["x1"] > border + SPLIT_WORD_MARGIN
            and top <= (w["top"] + w["bottom"]) / 2 <= bottom
            for w in words
        )

    rejoined = 0
    for r, row in enumerate(found.rows):
        cells = row.cells
        i = 0
        while i < len(cells) - 1:
            if cells[i] is None:
                i += 1
                continue
            x0, top, x1, bottom = cells[i]
            j = i + 1
            # Keep taking the next cell while a word runs across the border into it.
            while j < len(cells) and cells[j] is not None and word_crosses(x1, top, bottom):
                x1 = cells[j][2]
                raw[r][j] = None
                j += 1
            if j > i + 1:
                box = [x0, top, x1, bottom]
                joined = page.filter(lambda obj, box=box: obj["object_type"] == "char" and _inside(obj, [box]))
                raw[r][i] = joined.extract_text(**kwargs)
                rejoined += 1
            i = j
    return rejoined


def extract_tables(page, kwargs: dict) -> list[dict]:
    tables = []
    for found in page.find_tables():
        raw = found.extract(**kwargs)
        rejoin_split_cells(page, found, raw, kwargs)
        if any(c and FIGURE_RE.search(c) for r in raw for c in r):
            continue
        rows = [[clean_cell(c) for c in r] for r in raw]
        rows = [r for r in rows if any(r)]
        if not is_data_table(rows):
            continue
        tables.append({"bbox": [round(v, 1) for v in found.bbox], "rows": rows})
    # A table drawn inside another one is part of a figure (for example a ballout grid).
    return [t for t in tables if not any(_contains(o["bbox"], t["bbox"]) for o in tables if o is not t)]


def _contains(outer: list[float], inner: list[float]) -> bool:
    return (outer != inner and outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def _inside(obj: dict, boxes: list[list[float]]) -> bool:
    x = (obj["x0"] + obj["x1"]) / 2
    y = (obj["top"] + obj["bottom"]) / 2
    return any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in boxes)


def outside_tables(page, tables: list[dict]):
    """The page without the chars that sit inside a kept table."""
    boxes = [t["bbox"] for t in tables]
    return page.filter(lambda obj: obj["object_type"] != "char" or not _inside(obj, boxes))


def find_caption(table: dict, lines: list[dict]) -> str | None:
    """Best-effort caption from the lines above the table.

    Order: the nearest 'Table n' line, else the nearest numbered section heading,
    else the line directly above if it is short and is not a sentence.
    """
    table_top = table["bbox"][1]
    above = [l for l in lines if l["bottom"] <= table_top + 2 and l["top"] >= table_top - CAPTION_REACH]
    for line in reversed(above):
        match = TABLE_CAPTION_RE.match(line["text"])
        if match:
            return re.sub(r"^T able", "Table", match.group(1))
    for line in reversed(above):
        if HEADING_RE.match(line["text"]):
            return line["text"]
    if above and above[-1]["top"] >= table_top - FALLBACK_REACH:
        text = above[-1]["text"]
        if len(text) <= 80 and not text.endswith(".") and sum(c.isalpha() for c in text) >= 5:
            return text
    return None


def repeated_margin_lines(pages_lines: list[list[dict]]) -> list[list[bool]]:
    """Flag header and footer lines: for each page, one bool per line.

    A line is flagged when it sits in the top or bottom band and the same template
    appears at the same height on at least MIN_REPEAT_PAGES pages. Table and figure
    captions are never flagged.
    """
    seen = defaultdict(list)  # template -> [(page index, height)]
    for p, lines in enumerate(pages_lines):
        for line in lines:
            if _in_margin(line):
                seen[line_template(line["text"])].append((p, (line["y0"] + line["y1"]) / 2))

    flags = []
    for lines in pages_lines:
        page_flags = []
        for line in lines:
            flagged = False
            if _in_margin(line):
                y = (line["y0"] + line["y1"]) / 2
                pages = {p for p, other in seen[line_template(line["text"])] if abs(other - y) <= SAME_HEIGHT}
                flagged = len(pages) >= MIN_REPEAT_PAGES
            page_flags.append(flagged)
        flags.append(page_flags)
    return flags


def _line_key(line: dict) -> tuple[str, int]:
    return line["text"], round(line["top"])


def _in_margin(line: dict) -> bool:
    # A table that starts at the top of a continuation page keeps its header row.
    if line.get("in_table") or CAPTION_RE.match(line["text"]):
        return False
    return line["y0"] < TOP_BAND or line["y1"] > BOTTOM_BAND


def page_labels(removed: list[list[str]]) -> list[str | None]:
    """Printed page label per page, read from the removed header and footer lines.

    In a repeated line such as 'DS6329 Rev 18 71/181' the label is the number that
    changes from page to page.
    """
    by_template = defaultdict(list)  # template -> [(page index, numbers in the line)]
    for p, texts in enumerate(removed):
        for text in texts:
            by_template[line_template(text)].append((p, re.findall(r"\d+", text)))

    labels: list[str | None] = [None] * len(removed)
    for entries in by_template.values():
        if len(entries) < MIN_REPEAT_PAGES or not entries[0][1]:
            continue
        for pos in range(len(entries[0][1])):
            values = [nums[pos] for _, nums in entries if len(nums) > pos]
            if len(set(values)) >= 0.8 * len(entries):
                for p, nums in entries:
                    if labels[p] is None and len(nums) > pos:
                        labels[p] = nums[pos]
                break

    # A run of unlabelled pages (landscape pages) between two numbered pages is filled
    # in when the numbers on either side leave room for exactly that many pages.
    p = 1
    while p < len(labels):
        if labels[p] is not None or not (labels[p - 1] or "").isdigit():
            p += 1
            continue
        end = p
        while end < len(labels) and labels[end] is None:
            end += 1
        before = int(labels[p - 1])
        if end < len(labels) and labels[end].isdigit() and int(labels[end]) - before == end - p + 1:
            for i in range(p, end):
                labels[i] = str(before + i - p + 1)
        p = end + 1
    return labels


def inherit_captions(pages: list[dict], at_page_top: list[bool]) -> None:
    """Give a table that continues from the previous page that table's caption.

    Applies only to a table with no caption that is the first thing on its page and
    has as many columns as the last table of the page before.
    """
    for (prev, page), at_top in zip(pairwise(pages), at_page_top[1:]):
        if not at_top or not prev["tables"] or not page["tables"]:
            continue
        last, first = prev["tables"][-1], page["tables"][0]
        same_width = len(first["rows"][0]) == len(last["rows"][0])
        if first["caption"] is None and last["caption"] and same_width:
            base = last["caption"].removesuffix(" (continued)")
            first["caption"] = f"{base} (continued)"


def parse_pdf(path: Path) -> dict:
    raw_pages = []
    snapped = 0
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            has_text = bool(page.chars)
            kwargs = text_kwargs(page)
            if page.rotation not in (0, 180):
                # On a landscape page the header and footer run down the sides as
                # sideways text; everything else is upright. Checked in all four files.
                page = page.filter(lambda obj: obj["object_type"] != "char" or obj.get("upright", True))
            snapped += snap_scripts(page)
            tables = extract_tables(page, kwargs)
            body_lines = extract_lines(outside_tables(page, tables), kwargs) if tables else None
            lines = extract_lines(page, kwargs)
            boxes = [t["bbox"] for t in tables]
            for line in lines:
                line["in_table"] = _inside(line, boxes)
            raw_pages.append({
                "page": number,
                "has_text": has_text,
                "has_images": bool(page.images),
                "lines": lines,
                "body_lines": lines if body_lines is None else body_lines,
                "tables": tables,
            })

    image_only = [p["page"] for p in raw_pages if not p["has_text"] and p["has_images"]]
    if len(image_only) > SCANNED_SHARE * len(raw_pages):
        raise ScannedPdfError(
            f"{path.name}: {len(image_only)} of {len(raw_pages)} pages have images and no "
            f"selectable text (first: {image_only[:5]}). Decide on OCR before continuing."
        )

    flags = repeated_margin_lines([p["lines"] for p in raw_pages])
    removed = [[l["text"] for l, f in zip(p["lines"], fl) if f] for p, fl in zip(raw_pages, flags)]
    removed_templates = Counter(line_template(t) for texts in removed for t in texts)
    labels = page_labels(removed)

    document = path.stem
    pages = []
    at_page_top = []  # per page: is its first table the first thing on the page?
    for raw, label, page_flags in zip(raw_pages, labels, flags):
        # Only the exact lines that were flagged are dropped, not every line that looks like them.
        dropped = {_line_key(l) for l, flagged in zip(raw["lines"], page_flags) if flagged}
        body_lines = [l for l in raw["body_lines"] if _line_key(l) not in dropped]
        tables = [
            {
                "page": raw["page"],
                "index": i,
                "caption": find_caption(t, body_lines),
                "bbox": t["bbox"],
                "rows": t["rows"],
            }
            for i, t in enumerate(raw["tables"])
        ]
        at_page_top.append(bool(tables) and not any(l["bottom"] <= tables[0]["bbox"][1] for l in body_lines))
        pages.append({
            "page": raw["page"],
            "page_label": label,
            "blank": not raw["has_text"],
            "text": "\n".join(l["text"] for l in raw["lines"] if _line_key(l) not in dropped),
            "text_outside_tables": "\n".join(l["text"] for l in body_lines),
            "tables": tables,
        })
    inherit_captions(pages, at_page_top)

    all_text = "\n".join(p["text"] for p in pages)
    return {
        "document": document,
        "file": path.name,
        "page_count": len(pages),
        "stats": {
            "tables": sum(len(p["tables"]) for p in pages),
            "chars": len(all_text),
            "scripts_moved": snapped,
            "image_only_pages": image_only,
            "unmapped_glyphs": all_text.count("(cid:"),
            "private_use_chars": len(_PRIVATE_USE_RE.findall(all_text)),
        },
        "removed_lines": dict(removed_templates.most_common()),
        "pages": pages,
    }


def parsed_path(document: str) -> Path:
    return config.PARSED_DIR / f"{document}.json"


def save_parsed(parsed: dict) -> Path:
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    path = parsed_path(parsed["document"])
    path.write_text(json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_parsed(document: str) -> dict:
    return json.loads(parsed_path(document).read_text(encoding="utf-8"))


def main(argv: list[str]) -> None:
    paths = [config.PDF_DIR / name for name in argv] or sorted(config.PDF_DIR.glob("*.pdf"))
    for path in paths:
        parsed = parse_pdf(path)
        out = save_parsed(parsed)
        if parsed["stats"]["image_only_pages"]:
            print(
                f"WARNING {parsed['file']}: pages {parsed['stats']['image_only_pages']} have images and no "
                "selectable text. They are stored as blank. Decide whether they need OCR."
            )
        s = parsed["stats"]
        print(
            f"{parsed['file']}: pages={parsed['page_count']} tables={s['tables']} chars={s['chars']} "
            f"scripts_moved={s['scripts_moved']} unmapped_glyphs={s['unmapped_glyphs']} "
            f"private_use_chars={s['private_use_chars']} -> {out.relative_to(config.ROOT)}"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
