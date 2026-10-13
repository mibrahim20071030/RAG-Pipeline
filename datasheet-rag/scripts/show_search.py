"""Layer 4 review by eye: vector-only, keyword-only and hybrid top 5 for five hand-picked questions.

Run from datasheet-rag:  python scripts\\show_search.py           (prints the paid calls, asks, then searches)
                         python scripts\\show_search.py --yes     (does not ask)
                         python scripts\\show_search.py --strategy page

A question is embedded once (one paid Titan call) and its vector is then kept in `embedding_cache`.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, db, search
from rag.chunk_base import approx_tokens

QUESTIONS = [
    "What is the operating voltage range of the ESP32?",
    "What is the accuracy of the LM35CA at 25 °C?",
    "How much current does the LM35 draw when it is running?",  # worded differently from the datasheet
    "What is the maximum output current sourced by any I/O pin on the STM32F205?",
    "TCTH022BE supply voltage",  # an exact part number
]
TOP = 5


def show(title: str, hits: list[dict]) -> None:
    print(f"  {title}")
    if not hits:
        print("      (nothing)")
    for rank, hit in enumerate(hits[:TOP], start=1):
        ranks = ""
        if "vector_rank" in hit:
            ranks = f" v{hit['vector_rank'] or '-'}/k{hit['keyword_rank'] or '-'}"
        text = " ".join(hit["text"].split())[:80]
        print(f"    {rank}. {hit['document'][:14]:14} p{hit['page']:<3} {hit['kind']:5} {hit['score']:.4f}{ranks} | {text}")


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Print the top results of each search mode for five questions.")
    parser.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    parser.add_argument("--strategy", choices=list(config.CHUNK_STRATEGIES), default=config.DEFAULT_STRATEGY)
    args = parser.parse_args(argv)
    n = config.N_CANDIDATES

    try:
        conn = db.connect()
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    with conn:
        to_embed = search.uncached_questions(conn, QUESTIONS)
        tokens = sum(approx_tokens(q) for q in to_embed)
        print(f"Questions: {len(QUESTIONS)}. Paid Titan calls (not cached): {len(to_embed)}, about {tokens} tokens, "
              f"about ${tokens / 1000 * config.EMBED_USD_PER_1K_TOKENS:.6f}.")
        if to_embed and not args.yes and input("Make these paid calls? [y/N] ").strip().lower() != "y":
            print("Stopped. Nothing was embedded.")
            return

        print(f"\nStrategy: {args.strategy}. Each search takes {n} candidates; the top {TOP} are shown.")
        for question in QUESTIONS:
            try:
                vector = search.question_vector(conn, question)
            except (ClientError, BotoCoreError) as error:
                sys.exit(f"\nBedrock refused the embedding call: {error}\nThe vectors made so far are saved. "
                         "Run the same command again to carry on.")
            print(f"\n== {question}")
            show("vector", search.vector_search(conn, vector, n, args.strategy))
            for ts_config in config.KEYWORD_CONFIGS:
                show(f"keyword ({ts_config})", search.keyword_search(conn, question, n, args.strategy, ts_config))
            show(f"hybrid ({config.KEYWORD_CONFIG}; v = vector rank, k = keyword rank)",
                 search.hybrid_search(conn, question, vector, n, args.strategy))


if __name__ == "__main__":
    main(sys.argv[1:])
