"""Chunking strategies: one entry point over the text chunker and the table chunker.

Run from datasheet-rag:  python -m rag.chunk   (prints chunk counts, writes runs/chunk_counts.csv)
"""
import csv
import sys
from collections import defaultdict

from rag import chunk_tables, chunk_text, config, parse
from rag.chunk_base import approx_tokens

STRATEGIES = config.CHUNK_STRATEGIES
COUNT_COLUMNS = ["document", "strategy", "text_chunks", "table_chunks", "total", "figure_tables_skipped",
                 "max_tokens", "mean_tokens"]


def text_chunks(parsed: dict, strategy: str) -> list[dict]:
    """The text chunks of one strategy. For "page" that is the whole page, tables included as raw text."""
    spec = STRATEGIES[strategy]
    if strategy == "page":
        return chunk_text.page_chunks(parsed)
    if strategy == "structure_aware":
        return chunk_text.structure_chunks(parsed, spec["size"])
    return chunk_text.fixed_chunks(parsed, spec["size"], spec["overlap"])


def chunk_document(parsed: dict, strategy: str) -> list[dict]:
    """All chunks of one document for one strategy, in page order: a page's text chunks, then its tables."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown chunking strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
    chunks = text_chunks(parsed, strategy) if strategy == "page" else _text_and_tables(parsed, strategy)
    return [{**chunk, "strategy": strategy} for chunk in chunks]


def _text_and_tables(parsed: dict, strategy: str) -> list[dict]:
    size = STRATEGIES[strategy]["size"]
    text_by_page = defaultdict(list)
    for chunk in text_chunks(parsed, strategy):
        text_by_page[chunk["page"]].append(chunk)
    top_sections = chunk_text.sections_by_page(parsed)
    chunks = []
    for page in parsed["pages"]:
        on_page = text_by_page[page["page"]]
        chunks.extend(on_page)
        # Best effort: a table takes the section of the last text on its page, else the one the page opens in.
        section = on_page[-1]["section"] if on_page else top_sections.get(page["page"])
        for table in page["tables"]:
            for chunk in chunk_tables.table_chunks(parsed["document"], page["page"], page["page_label"], table, size):
                chunks.append({**chunk, "section": section})
    return chunks


def count_row(parsed: dict, strategy: str) -> dict:
    """One line of the counts file: how many chunks a strategy makes for a document, and how big they are."""
    chunks = chunk_document(parsed, strategy)
    tokens = [approx_tokens(chunk["text"]) for chunk in chunks]
    tables = sum(chunk["kind"] == "table" for chunk in chunks)
    skipped = 0
    if strategy != "page":
        skipped = sum(chunk_tables.is_figure_table(table) for page in parsed["pages"] for table in page["tables"])
    return {
        "document": parsed["document"],
        "strategy": strategy,
        "text_chunks": len(chunks) - tables,
        "table_chunks": tables,
        "total": len(chunks),
        "figure_tables_skipped": skipped,
        "max_tokens": max(tokens, default=0),
        "mean_tokens": round(sum(tokens) / len(tokens)) if tokens else 0,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    stems = sorted(path.stem for path in config.PARSED_DIR.glob("*.json"))
    if not stems:
        sys.exit(f"No parsed datasheets in {config.PARSED_DIR}. Run: python -m rag.parse")
    rows = [count_row(parse.load_parsed(stem), strategy) for stem in stems for strategy in STRATEGIES]
    with config.CHUNK_COUNTS.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COUNT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Token measure: characters / {config.CHARS_PER_TOKEN}, rounded up.")
    print(f"{'document':34} {'strategy':18} {'text':>6} {'table':>6} {'total':>6} {'skipped':>8} {'max':>6} {'mean':>6}")
    for row in rows:
        print(f"{row['document']:34} {row['strategy']:18} {row['text_chunks']:>6} {row['table_chunks']:>6} "
              f"{row['total']:>6} {row['figure_tables_skipped']:>8} {row['max_tokens']:>6} {row['mean_tokens']:>6}")
    print(f"Saved {config.CHUNK_COUNTS}")


if __name__ == "__main__":
    main()
