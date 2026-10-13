"""Print the chunks of one page for one strategy, to read beside the PDF.

Run from datasheet-rag:  python scripts\\show_chunks.py lm35 6                  (structure_aware)
                         python scripts\\show_chunks.py lm35 6 fixed_256
                         python scripts\\show_chunks.py lm35 6 fixed_256 --tables   (table chunks only)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import chunk, parse
from rag.chunk_base import approx_tokens

DEFAULT_STRATEGY = "structure_aware"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2 or not args[1].isdigit():
        sys.exit("Usage: python scripts\\show_chunks.py <document> <page> [strategy] [--tables]\n"
                 f"Strategies: {', '.join(chunk.STRATEGIES)}")
    strategy = args[2] if len(args) > 2 else DEFAULT_STRATEGY
    chunks = [c for c in chunk.chunk_document(parse.load_parsed(args[0]), strategy) if c["page"] == int(args[1])]
    if "--tables" in sys.argv:
        chunks = [c for c in chunks if c["kind"] == "table"]
    print(f"{args[0]} page {args[1]}, strategy {strategy}: {len(chunks)} chunks")
    for n, c in enumerate(chunks, 1):
        print(f"\n--- {n}: {c['kind']}, {approx_tokens(c['text'])} tokens, section: {c['section']}")
        print(c["text"])


if __name__ == "__main__":
    main()
