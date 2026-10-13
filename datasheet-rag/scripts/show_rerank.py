"""Layer 5 checks that call the real reranker: the two-chunk test, then five questions before and after reranking.

Run from datasheet-rag:  python scripts\\show_rerank.py           (prints the paid calls, asks, then reranks)
                         python scripts\\show_rerank.py --yes     (does not ask)
                         python scripts\\show_rerank.py --strategy page

A (question, chunk) pair is scored once and its score is then kept in `rerank_cache`, so a second run is free.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, db, rerank, search
from scripts.show_search import QUESTIONS

# Two rows of Table 12 (current characteristics) of the STM32F205 datasheet, page 70, as the chunker writes them.
PIN_QUESTION = "What is the maximum current per GPIO pin?"
PER_PIN = "Symbol: IIO, Ratings: Output current source by any I/Os and control pin, Max: 25, Unit: mA"
TOTAL = "Symbol: IVDD, Ratings: Total current into VDD power lines (source), Max: 120, Unit: mA"
PIN_CHUNKS = [{"id": 1, "text": TOTAL, "score": 0.0}, {"id": 2, "text": PER_PIN, "score": 0.0}]

REFUSED = """
Bedrock refused the rerank call: {error}

What it means: the call reached AWS and AWS said no. The scores made so far are saved.
Likely reasons, most likely first:
  1. "not authorized to perform the required AWS Marketplace actions" (seen on 2026-10-13, on the second call;
     the first one was answered): Cohere Rerank is a third-party model, and its first use needs the permissions
     aws-marketplace:Subscribe and aws-marketplace:ViewSubscriptions, which the dev user does not have.
     Fix: add them to the dev user in the IAM console (or make one call from an admin identity), wait 5 minutes,
     run this again, then remove them.
  2. "Your account is currently being verified": wait some minutes and run this again.
  3. ThrottlingException: more than 3 calls in a minute. Wait a minute and run this again.
"""


def show(title: str, hits: list[dict], places: dict[int, int] | None = None) -> None:
    print(f"  {title}")
    for rank, hit in enumerate(hits, start=1):
        was = f" was {places[hit['id']]:>2}" if places else ""
        text = " ".join(hit["text"].split())[:80]
        print(f"    {rank}. {hit['document'][:14]:14} p{hit['page']:<3} {hit['kind']:5} {hit['score']:.4f}{was} | {text}")


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run the two-chunk rerank test and show five questions reranked.")
    parser.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    parser.add_argument("--strategy", choices=list(config.CHUNK_STRATEGIES), default=config.DEFAULT_STRATEGY)
    args = parser.parse_args(argv)

    try:
        conn = db.connect()
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    with conn:
        db.create_schema(conn)  # adds the rerank_cache table when it is not there yet
        to_embed = search.uncached_questions(conn, QUESTIONS)
        if to_embed:
            sys.exit(f"{len(to_embed)} question vectors are not cached. Run scripts\\show_search.py first.")
        candidates = {q: search.search(conn, q, "hybrid", strategy=args.strategy) for q in QUESTIONS}
        calls = rerank.calls_needed(conn, PIN_QUESTION, PIN_CHUNKS)
        calls += sum(rerank.calls_needed(conn, q, hits) for q, hits in candidates.items())
        print(f"Paid rerank calls (not cached): {calls}, about ${calls * config.RERANK_USD_PER_QUERY:.3f}, "
              f"about {max(calls - 1, 0) * config.RERANK_MIN_INTERVAL_S} seconds of waiting between calls.")
        if calls and not args.yes and input("Make these paid calls? [y/N] ").strip().lower() != "y":
            print("Stopped. Nothing was reranked.")
            return

        try:
            pair = rerank.rerank(conn, PIN_QUESTION, PIN_CHUNKS, k=2, enabled=True)
            print(f"\n== Two-chunk test: {PIN_QUESTION}")
            for hit in pair:
                print(f"    {hit['score']:.4f} | {hit['text']}")
            if pair[0]["text"] != PER_PIN:
                sys.exit("FAILED: the total-current row scored higher than the per-pin row.")
            print("  OK: the per-pin row scored higher.")

            print(f"\nStrategy: {args.strategy}. Hybrid search gives {config.N_CANDIDATES} candidates; "
                  f"the top {config.TOP_K} are shown.")
            for question, hits in candidates.items():
                places = {hit["id"]: place for place, hit in enumerate(hits, start=1)}
                print(f"\n== {question}")
                show("hybrid, before reranking", hits[:config.TOP_K])
                show("after reranking (was = place before)", rerank.rerank(conn, question, hits, enabled=True), places)
        except (ClientError, BotoCoreError) as error:
            sys.exit(REFUSED.format(error=error))


if __name__ == "__main__":
    main(sys.argv[1:])
