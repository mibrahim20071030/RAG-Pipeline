"""Layer 6 smoke test, paid: three answerable and three unanswerable questions, end to end.

Run from datasheet-rag:  python scripts\\show_generate.py           (prints the paid calls, asks, then answers)
                         python scripts\\show_generate.py --yes     (does not ask)
                         python scripts\\show_generate.py --strategy page

Each question goes through hybrid search, the reranker (top k) and Nova Lite. Question vectors and rerank scores
are cached, so a second run pays only for the six answers.
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, db, generate, load, rerank, search

# question -> (document, pages that hold the answer), or None when no datasheet answers it.
# Answers checked in the parsed datasheets on 2026-10-14.
QUESTIONS = {
    # Table 12, Current characteristics: IIO, output current sourced by any I/O and control pin, max 25 mA.
    "What is the maximum output current that a single I/O pin of the STM32F205 can source?": ("DS_CD00237391", [70]),
    # Features and electrical characteristics: linear + 10 mV/°C scale factor.
    "What is the output scale factor of the LM35?": ("lm35", [1, 13, 15, 16]),
    # Section 2.5 notes on power supply (2.3 V to 3.6 V) and Table 5-2.
    "What supply voltage range does the ESP32 operate from?": ("esp32_datasheet_en", [19, 51]),
    "What is the unit price of the ESP32 chip?": None,
    "What is the Bluetooth range of the LM35?": None,
    "How much flash memory does the STM32H743 have?": None,
}
GENERATE_TOKENS = (4500, 300)  # rough input and output tokens per answer at k 10 (about 4,300 in Layer 7), for the estimate only

REFUSED = """
Bedrock refused a call: {error}

What it means: the call reached AWS and AWS said no. Question vectors and rerank scores made so far are saved.
Likely reasons, most likely first:
  1. "Your account is currently being verified" (seen on the first Titan call): wait some minutes, run this again.
  2. AccessDeniedException on Converse: the dev user needs bedrock:InvokeModel on amazon.nova-lite-v1:0.
  3. ThrottlingException: too many calls in a minute. Wait a minute and run this again.
"""


def check(result: dict, expected) -> tuple[bool, str]:
    """PASS/FAIL and why. Answerable: an answer citing a page that holds it. Unanswerable: the fixed sentence."""
    if result["invalid"]:
        return False, f"invalid labels {result['invalid']}"
    if result["cut_off"]:
        return False, f"cut off at {config.GENERATE_MAX_TOKENS} tokens"
    if expected is None:
        return result["not_contained"], "said not contained" if result["not_contained"] else "answered anyway"
    if result["not_contained"]:
        return False, "said not contained"
    document, pages = expected
    cited = [(s["document"], s["page"]) for s in result["sources"]]
    if not any(doc == document for doc, _ in cited):
        return False, "no source from the right datasheet"
    if not any(doc == document and page in pages for doc, page in cited):
        return False, f"right datasheet, but none of pages {pages}"
    return True, "cites an answer page"


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Answer six questions end to end and check the behaviour.")
    parser.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    parser.add_argument("--strategy", choices=load.strategy_names(), default=config.DEFAULT_STRATEGY)
    args = parser.parse_args(argv)

    try:
        conn = db.connect()
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    with conn:
        to_embed = search.uncached_questions(conn, list(QUESTIONS))
        # A question with a stored vector can be searched for free, so its rerank calls are counted exactly.
        # One without is counted as one rerank call: the 30 candidates fit in one.
        reranks = len(to_embed) * math.ceil(config.N_CANDIDATES / config.RERANK_MAX_DOCS)
        for question in QUESTIONS:
            if question not in to_embed:
                hits = search.search(conn, question, "hybrid", strategy=args.strategy)
                reranks += rerank.calls_needed(conn, question, hits)
        answers = len(QUESTIONS)
        tokens_in, tokens_out = GENERATE_TOKENS
        cost = (reranks * config.RERANK_USD_PER_QUERY
                + answers * (tokens_in / 1000 * config.GENERATE_USD_PER_1K_INPUT
                             + tokens_out / 1000 * config.GENERATE_USD_PER_1K_OUTPUT))
        print(f"Paid calls: {len(to_embed)} question embeddings, {reranks} rerank calls (at most), {answers} answers.")
        print(f"Estimated cost about ${cost:.4f} (embeddings are a fraction of a cent); "
              f"about {max(reranks - 1, 0) * config.RERANK_MIN_INTERVAL_S} seconds of waiting between rerank calls.")
        if not args.yes and input("Make these paid calls? [y/N] ").strip().lower() != "y":
            print("Stopped. Nothing was called.")
            return

        print(f"\nStrategy: {args.strategy}. Hybrid search {config.N_CANDIDATES}, rerank to {config.TOP_K}, "
              f"{config.GENERATE_MODEL_ID}.")
        passed = 0
        try:
            for question, expected in QUESTIONS.items():
                hits = search.search(conn, question, "hybrid", strategy=args.strategy)
                top = rerank.rerank(conn, question, hits)
                print(f"\n== {question}")
                try:
                    result = generate.generate(question, top)
                except RuntimeError as error:
                    # A blocked answer (for example content_filtered): logged and paid for; the run carries on.
                    print(f"  FAIL: {error}")
                    continue
                ok, why = check(result, expected)
                passed += ok
                print(f"  expected: {'not contained' if expected is None else f'{expected[0]} p{expected[1]}'}")
                for n, hit in enumerate(top, start=1):
                    print(f"  [{n}] {generate.passage_label(hit)}")
                print("  answer: " + "\n          ".join(result["answer"].splitlines()))
                for source in result["sources"]:
                    print(f"  source [{source['label']}] {source['document']} p{source['page']}")
                print(f"  tokens {result['input_tokens']} in / {result['output_tokens']} out, "
                      f"{result['latency_ms']} ms, stop {result['stop_reason']}")
                print(f"  {'PASS' if ok else 'FAIL'}: {why}")
        except (ClientError, BotoCoreError) as error:
            sys.exit(REFUSED.format(error=error))
        print(f"\n{passed} of {len(QUESTIONS)} passed.")


if __name__ == "__main__":
    main(sys.argv[1:])
