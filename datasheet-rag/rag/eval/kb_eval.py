"""Run the frozen questions through the Knowledge Base pipeline and write the results (Cycle 2 step 8c).

Run from datasheet-rag:  python -m rag.eval.kb_eval --dry-run   (prints calls, cost, minutes; calls nothing)
                         python -m rag.eval.kb_eval             (prints the same, asks, then runs)
                         python -m rag.eval.kb_eval --yes       (does not ask)

Each question gets one Retrieve (the Managed KB's top 10, already reranked by AWS) and one Nova Lite answer from
`generate.generate`, the same answer step as our pipeline. Rows and summary use the columns of rag.eval.run_eval, so
the experiment `kb_managed` sits in runs/results.csv next to ours (`gen_prefix_k10`, the frozen setup). It needs no
database. There is no cache: a re-run pays again (about $0.06 for 49 questions).
"""
import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from rag import config, costlog, generate, kb
from rag.eval import metrics, questions, run_eval

NAME = "kb_managed"
# The experiment as run_eval's summary columns describe it. rerank True: the KB's order is already reranked.
EXPERIMENT = {"name": NAME, "group": "kb", "strategy": "kb_default_chunking", "mode": "kb_managed_hybrid",
              "ts_config": None, "n": config.TOP_K, "rerank": True, "k": config.TOP_K, "detect_part": False,
              "hnsw": False, "generate": True}
PASSAGE_TOKENS = 400  # assumed size of a KB passage for the cost estimate; measured tokens go in the results
RETRIEVE_SECONDS = 2.0  # assumed time of one Retrieve for the time estimate (the first one took 5.8 s)


def plan(count: int) -> dict:
    """Calls, cost and minutes for `count` questions. Retrieve is exact; the answer tokens are estimated."""
    input_tokens = count * (config.TOP_K * PASSAGE_TOKENS + run_eval.PROMPT_TOKENS)
    cost = {
        "retrieve": count * config.KB_USD_PER_RETRIEVE,
        "generate": (input_tokens / 1000 * config.GENERATE_USD_PER_1K_INPUT
                     + count * run_eval.ANSWER_TOKENS / 1000 * config.GENERATE_USD_PER_1K_OUTPUT),
    }
    cost["total"] = cost["retrieve"] + cost["generate"]
    return {"questions": count, "retrieve_calls": count, "generate_calls": count, "est_input_tokens": input_tokens,
            "est_cost_usd": cost, "est_minutes": count * RETRIEVE_SECONDS / 60 + count / config.GENERATE_PER_MIN}


def print_plan(p: dict) -> None:
    cost = p["est_cost_usd"]
    print(f"Questions:                {p['questions']:>6,}")
    print(f"Retrieve calls (KB):      {p['retrieve_calls']:>6,}  ${cost['retrieve']:.4f}"
          f"  at ${config.KB_USD_PER_RETRIEVE:.3f} per call")
    print(f"Generate calls (Nova):    {p['generate_calls']:>6,}  ${cost['generate']:.4f}"
          f"  ({p['est_input_tokens']:,} input tokens, estimated)")
    print(f"Estimated cost:           ${cost['total']:.4f}")
    print(f"Estimated time:           {p['est_minutes']:.1f} minutes")


def _elapsed_ms(clock, start: float) -> float:
    return (clock() - start) * 1000


def run_question(question: dict, retrieve_client=None, generate_client=None, clock=time.perf_counter) -> dict:
    """One question through the KB pipeline, in the row shape of `run_eval.run_question`."""
    text, sources = question["question"], question["sources"]
    start = clock()
    hits = kb.retrieve(text, client=retrieve_client)
    retrieve_ms = _elapsed_ms(clock, start)
    start = clock()
    generated = generate.generate(text, hits, client=generate_client)
    generate_ms = _elapsed_ms(clock, start)
    rank = metrics.first_correct_rank(hits, sources)
    names = run_eval._hit_names(hits)
    return {
        "id": question["id"], "type": question["type"], "document_filter": None,
        "search_hits": names, "rerank_hits": names, "search_rank": rank, "rerank_rank": rank,
        "embed_ms": None, "embed_cached": None, "search_ms": retrieve_ms, "rerank_ms": None, "rerank_wait_ms": None,
        "rerank_calls": 0, "generate_ms": generate_ms,
        "answer": generated["answer"],
        "cited": [f"{s['document']}:{s['page']}" for s in generated["sources"]],
        "not_contained": generated["not_contained"], "invalid": generated["invalid"],
        "input_tokens": generated["input_tokens"], "output_tokens": generated["output_tokens"],
        "unpaged": sum(1 for hit in hits if hit["page"] is None or hit["document"] is None),
        "_question": text, "_search": hits, "_rerank": hits, "_exact_ids": None, "_generated": generated,
    }


def cost_per_query(rows: list[dict]) -> float:
    """One Retrieve plus the mean answer tokens."""
    return (config.KB_USD_PER_RETRIEVE
            + (run_eval._mean(row["input_tokens"] for row in rows) or 0) / 1000 * config.GENERATE_USD_PER_1K_INPUT
            + (run_eval._mean(row["output_tokens"] for row in rows) or 0) / 1000 * config.GENERATE_USD_PER_1K_OUTPUT)


def run(questions_: list[dict], retrieve_client=None, generate_client=None, clock=time.perf_counter) -> dict:
    """Every question through the KB pipeline: the rows and one summary row (the KB's reranked order)."""
    logged_before = costlog.row_count()
    rows = [run_question(question, retrieve_client, generate_client, clock) for question in questions_]
    spent = run_eval._spent_since(logged_before)
    truths = [question["sources"] for question in questions_]
    summary = run_eval._summary_row(rows, truths, EXPERIMENT, True, spent,
                                    datetime.now(UTC).isoformat(timespec="seconds"))
    summary["cost_per_query_usd"] = cost_per_query(rows)  # run_eval's formula counts an embed and a Cohere query
    return {"rows": rows, "summary": [summary]}


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run the questions through the Knowledge Base pipeline.")
    parser.add_argument("--dry-run", action="store_true", help="print the calls, cost and time, then stop")
    parser.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    parser.add_argument("--questions", help=f"question file (default: {config.QUESTIONS})")
    args = parser.parse_args(argv)
    try:
        questions_ = questions.load_questions(Path(args.questions) if args.questions else None)
    except (ValueError, OSError) as error:
        sys.exit(f"Cannot start: {error}")
    if not config.KB_ID:
        sys.exit("Set KB_ID in datasheet-rag\\.env first.")

    print_plan(plan(len(questions_)))
    if args.dry_run:
        print("\nNothing was called. Leave out --dry-run to run.")
        return
    if not args.yes and input("\nRun? [y/N] ").strip().lower() != "y":
        print("Stopped. Nothing was called.")
        return

    logged_before = costlog.row_count()
    try:
        result = run(questions_)
    except (ClientError, BotoCoreError, RuntimeError) as error:
        print(f"\nThe run stopped: {error}\nNothing was written. Run the same command again (there is no cache, so "
              "the questions already sent are paid again).")
        raise SystemExit(1) from error
    finally:
        spent = run_eval._spent_since(logged_before)
        if spent is not None:
            print(f"\nThis run: ${spent:.4f} ({config.COST_LOG})")
    run_eval.write_rows(result["rows"], config.EVAL_DIR / f"{NAME}.csv")
    run_eval.write_results(result["summary"])
    run_eval.write_by_type(result)
    run_eval._print_summary(result["summary"])
    print(f"Results: {config.RESULTS}, {config.RESULTS_BY_TYPE}, {config.EVAL_DIR / f'{NAME}.csv'}")
    print("Compare with ours: python scripts\\compare_pipelines.py")


if __name__ == "__main__":
    main(sys.argv[1:])
