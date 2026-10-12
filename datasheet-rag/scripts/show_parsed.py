"""Print the parsed tables of one page as a grid, to read beside the PDF during the table review.

Run from datasheet-rag:  python scripts\\show_parsed.py lm35 5
                         python scripts\\show_parsed.py lm35 5 --full   (long cells not shortened)
                         python scripts\\show_parsed.py lm35 --list     (pages that have tables)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import parse

MAX_WIDTH = 26  # longer cells are shortened with "..." unless --full is given
MERGED = "^"       # shown for a cell that is merged with the one above it or to its left
EMPTY = "(blank)"  # shown for a cell that is empty in the PDF; a printed "-" is a real dash


def show(cell: str | None, full: bool) -> str:
    if cell is None:
        return MERGED
    if cell == "":
        return EMPTY
    return cell if full or len(cell) <= MAX_WIDTH else cell[: MAX_WIDTH - 3] + "..."


def print_table(rows: list[list[str | None]], full: bool) -> None:
    shown = [[show(cell, full) for cell in row] for row in rows]
    widths = [max(len(row[i]) for row in shown) for i in range(len(shown[0]))]
    rule = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    print(rule)
    for n, row in enumerate(shown):
        print("| " + " | ".join(cell.ljust(w) for cell, w in zip(row, widths)) + " |")
        if n == 0:
            print(rule)
    print(rule)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    doc = parse.load_parsed(args[0])
    if "--list" in sys.argv:
        for page in doc["pages"]:
            for table in page["tables"]:
                print(f"page {page['page']:>3}  {len(table['rows']):>2} rows  {table['caption']}")
        return
    page = doc["pages"][int(args[1]) - 1]
    print(f"{doc['file']}  page {page['page']}  (printed label: {page['page_label']})")
    for table in page["tables"]:
        print(f"\nTable {table['index'] + 1} of {len(page['tables'])}: {table['caption']}")
        print_table(table["rows"], "--full" in sys.argv)
    print(f"\nKey:  {MERGED} = merged with the cell above or to the left     {EMPTY} = empty in the PDF")
    if "--full" in sys.argv:
        print("\nText outside tables:\n" + page["text_outside_tables"])


if __name__ == "__main__":
    main()
