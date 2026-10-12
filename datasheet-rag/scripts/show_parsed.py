"""Print the parsed tables of one page, to read beside the PDF during the table review.

Run from datasheet-rag:  python scripts\\show_parsed.py lm35 5
                         python scripts\\show_parsed.py lm35 --list     (pages that have tables)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import parse


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    doc = parse.load_parsed(sys.argv[1])
    if sys.argv[2] == "--list":
        for page in doc["pages"]:
            for table in page["tables"]:
                print(f"page {page['page']:>3}  {len(table['rows']):>2} rows  {table['caption']}")
        return
    page = doc["pages"][int(sys.argv[2]) - 1]
    print(f"{doc['file']}  page {page['page']}  (printed label: {page['page_label']})")
    for table in page["tables"]:
        print(f"\nTable {table['index']}: {table['caption']}")
        for row in table["rows"]:
            print("  | " + " | ".join("^" if cell is None else cell for cell in row))
    print("\nText outside tables:\n" + page["text_outside_tables"])


if __name__ == "__main__":
    main()
