"""The whole query path: search, rerank, answer. It only calls the steps in order, with the setup frozen in config.

Run from datasheet-rag:  python -m rag.pipeline "What is the maximum supply voltage of the LM35?"
                         python -m rag.pipeline "What is the supply voltage range?" --document lm35

A new question costs one Titan embed, one rerank query and one Nova Lite answer (about $0.0023, almost all the
reranker); a question asked before costs only the answer, as its vector and rerank scores are cached. It does not
ask first: what it spent is printed after the answer, and every call is in runs/cost_log.csv.
"""
import argparse
import sys
import time

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, costlog, db, generate, rerank, search

REFUSED = """
Bedrock refused a call: {error}

What it means: the call reached AWS and AWS said no. A question vector or rerank scores made so far are saved.
Likely reasons, most likely first:
  1. ThrottlingException: too many calls in a minute (the reranker allows 3). Wait a minute and ask again.
  2. AccessDeniedException: the dev user needs bedrock:InvokeModel and bedrock:Rerank on the project models.
  3. No AWS credentials found: the key belongs in ~\\.aws\\credentials.
"""


def _ms_since(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def answer_question(conn, question: str, document: str | None = None, embed_fn=None, rerank_client=None,
                    generate_client=None) -> dict:
    """Answer one question from the datasheets, with sources as document and page.

    `document` limits the search to one datasheet (None: all). The result is what `generate.generate` returns,
    plus `question`, the `passages` the model was given (in label order) and `timings_ms` per step; the search
    time includes embedding the question when it is not cached.
    """
    start = time.perf_counter()
    hits = search.search(conn, question, config.SEARCH_MODE, document=document, embed_fn=embed_fn)
    search_ms = _ms_since(start)

    start = time.perf_counter()
    passages = rerank.rerank(conn, question, hits, client=rerank_client)
    rerank_ms = _ms_since(start)

    start = time.perf_counter()
    result = generate.generate(question, passages, client=generate_client)
    generate_ms = _ms_since(start)

    return {"question": question, **result, "passages": passages,
            "timings_ms": {"search": search_ms, "rerank": rerank_ms, "generate": generate_ms}}


def warnings(result: dict) -> list[str]:
    """What a reader should know about an answer beyond its text: an empty search, invalid labels, a cut-off."""
    found = []
    if result.get("passages") == []:
        # Not the same as a question the datasheets cannot answer: nothing was searched or nothing is stored.
        found.append(f"The search found no chunks. The question has no searchable word, or strategy "
                     f"{config.DEFAULT_STRATEGY!r} is not loaded (check with: python scripts\\show_db.py).")
    if result["invalid"]:
        found.append(f"The answer cites passages that do not exist: {result['invalid']}")
    if result["cut_off"]:
        found.append(f"The answer was cut off at {config.GENERATE_MAX_TOKENS} tokens.")
    return found


def format_answer(result: dict) -> str:
    """The answer for a reader: the text, then each cited source as datasheet and PDF page, then any warning."""
    lines = [result["answer"].strip(), ""]
    if result["sources"]:
        lines.append("Sources:")
        lines += [f"  [{source['label']}] {generate.passage_label(source)}" for source in result["sources"]]
    else:
        lines.append("Sources: none")
    lines += [f"WARNING: {warning}" for warning in warnings(result)]
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Answer a question from the datasheets, with page citations.")
    parser.add_argument("question", help="the question, in quotes")
    parser.add_argument("--document", choices=list(config.DOCUMENTS), help="search only this datasheet")
    args = parser.parse_args(argv)

    try:
        conn = db.connect()
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    logged_before = costlog.row_count()
    with conn:
        try:
            result = answer_question(conn, args.question, document=args.document)
        except (ClientError, BotoCoreError) as error:
            sys.exit(REFUSED.format(error=error))
        except RuntimeError as error:
            # The reranker raises when Cohere scores fewer documents than sent; generate raises on a stop reason it
            # does not expect (for example content_filtered). Both log what was paid for first.
            sys.exit(f"A step stopped without a usable result: {error}")

    print(format_answer(result))
    timings = result["timings_ms"]
    print(f"\n{config.DEFAULT_STRATEGY}, {config.SEARCH_MODE} search {config.N_CANDIDATES}, rerank to "
          f"{config.TOP_K}, {config.GENERATE_MODEL_ID}. Search {timings['search']:.0f} ms, rerank "
          f"{timings['rerank']:.0f} ms, answer {timings['generate']:.0f} ms.")
    spent = costlog.spent_since(logged_before)
    print(f"Spent: ${spent:.4f} (cost log {config.COST_LOG})" if spent is not None
          else f"Could not read the cost log. Check {config.COST_LOG} by hand.")


if __name__ == "__main__":
    main(sys.argv[1:])
