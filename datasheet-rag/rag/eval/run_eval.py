"""Run evaluation experiments over the question set and write the results.

Run from datasheet-rag:  python -m rag.eval.run_eval baseline chunk_page --dry-run   (prints calls, cost, minutes)
                         python -m rag.eval.run_eval --group chunking        (prints the same, asks, then runs)
                         python -m rag.eval.run_eval --all --yes             (does not ask)
                         python -m rag.eval.run_eval baseline --questions data/other.jsonl

Each experiment writes its per-question rows to runs/eval/<name>.csv, and its summary rows to runs/results.csv and
runs/results_by_type.csv, as soon as it finishes. Paid calls go through `search.question_vector`, `rerank.rerank`
and `generate.generate`, which keep vectors and rerank scores cached and write the cost log.
"""
import argparse
import csv
import math
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, costlog, db, generate, rerank, search
from rag.chunk_base import approx_tokens
from rag.eval import experiments, metrics, questions

SUMMARY_FIELDS = [
    "experiment", "strategy", "mode", "ts_config", "rerank", "k", "detect_part", "hnsw", "questions", "answerable",
    *(f"recall@{k}" for k in config.EVAL_KS), "mrr@10", "index_recall", "embed_ms", "search_ms", "rerank_ms",
    "rerank_wait_ms", "generate_ms", "citation_hit_rate", "unanswerable_refused", "answerable_refused",
    "invalid_labels", "cost_per_query_usd", "spent_usd", "run_at",
]
BY_TYPE_FIELDS = ["experiment", "rerank", "type", "questions", "recall@5", "mrr@10"]
TITAN_TOKENS_PER_ESTIMATE = 1.3  # real Titan tokens were 1.29 times the characters / 4 estimate (Layer 3)
PROMPT_TOKENS = 700  # rules and question of one generate call, on top of the passages
ANSWER_TOKENS = 100  # output tokens assumed per answer in the plan
HIT_LIST_LENGTH = 10  # hits written per question in the per-question CSV


def _no_paid_call(text: str):
    raise RuntimeError("the plan must not embed; only cached question vectors are used")


def _hit_names(hits: list[dict]) -> list[str]:
    return [f"{hit['document']}:{hit['page']}" for hit in hits[:HIT_LIST_LENGTH]]


def _search(conn, text: str, exp: dict, vector, document: str | None) -> list[dict]:
    """The exact search of one experiment, with the question vector already made (None in keyword mode)."""
    if exp["mode"] == "keyword":
        return search.keyword_search(conn, text, exp["n"], exp["strategy"], exp["ts_config"], document)
    if exp["mode"] == "vector":
        return search.vector_search(conn, vector, exp["n"], exp["strategy"], document)
    return search.hybrid_search(conn, text, vector, exp["n"], exp["strategy"], exp["ts_config"], document)


def _document_filter(text: str, exp: dict) -> str | None:
    return search.detect_part(text) if exp["detect_part"] else None


def _mean_chunk_tokens(conn, strategy: str) -> float:
    """Mean approximate tokens of the strategy's stored chunk texts; 0 when none is stored."""
    return db.mean_chunk_tokens(conn, strategy, config.CHARS_PER_TOKEN)


def _embed_usd(question_texts: list[str]) -> float:
    tokens = sum(approx_tokens(text) for text in question_texts) * TITAN_TOKENS_PER_ESTIMATE
    return tokens / 1000 * config.EMBED_USD_PER_1K_TOKENS


def plan_run(conn, questions_: list[dict], experiments_: list[dict]) -> dict:
    """What a run would do, with no paid call: calls per model, estimated cost and minutes.

    A rerank count is exact when the question vector is cached (the free search is run and the cached scores are
    looked up), else one call is counted. HNSW experiments are planned with the HNSW search they rerank (free, but
    the throwaway index is built per question). Experiments that would share rerank scores are each counted, so
    the count can run high.
    """
    texts = [question["question"] for question in questions_]
    needs_vector = any(exp["mode"] != "keyword" for exp in experiments_)
    uncached = search.uncached_questions(conn, texts) if needs_vector else []
    rerank_calls = 0
    for exp in experiments_:
        if not exp["rerank"]:
            continue
        for text in texts:
            if exp["mode"] != "keyword" and text in uncached:
                rerank_calls += 1
                continue
            vector = None if exp["mode"] == "keyword" else search.question_vector(conn, text, _no_paid_call)
            document = _document_filter(text, exp)
            hits = (db.hnsw_nearest(conn, vector, exp["strategy"], exp["n"], document) if exp["hnsw"]
                    else _search(conn, text, exp, vector, document))
            rerank_calls += rerank.calls_needed(conn, text, hits)
    generating = [exp for exp in experiments_ if exp["generate"]]
    generate_calls = len(texts) * len(generating)
    mean_tokens = {exp["strategy"]: _mean_chunk_tokens(conn, exp["strategy"]) for exp in generating}
    est_input_tokens = round(sum(
        len(texts) * (exp["k"] * mean_tokens[exp["strategy"]] + PROMPT_TOKENS) for exp in generating
    ))
    cost = {
        "embed": _embed_usd(uncached),
        "rerank": rerank_calls * config.RERANK_USD_PER_QUERY,
        "generate": (est_input_tokens / 1000 * config.GENERATE_USD_PER_1K_INPUT
                     + generate_calls * ANSWER_TOKENS / 1000 * config.GENERATE_USD_PER_1K_OUTPUT),
    }
    cost["total"] = sum(cost.values())
    return {
        "experiments": [exp["name"] for exp in experiments_],
        "questions": len(texts),
        "embed_calls": len(uncached),
        "rerank_calls": rerank_calls,
        "generate_calls": generate_calls,
        "est_input_tokens": est_input_tokens,
        "est_cost_usd": cost,
        "est_minutes": (rerank_calls * config.RERANK_MIN_INTERVAL_S / 60 + len(uncached) / config.EMBED_PER_MIN
                        + generate_calls / config.GENERATE_PER_MIN),
    }


def print_plan(plan: dict) -> None:
    cost = plan["est_cost_usd"]
    print(f"Experiments ({len(plan['experiments'])}): {', '.join(plan['experiments'])}")
    print(f"Questions:              {plan['questions']:>7,}")
    print(f"Embed calls (Titan):    {plan['embed_calls']:>7,}  ${cost['embed']:.4f}")
    print(f"Rerank calls (Cohere):  {plan['rerank_calls']:>7,}  ${cost['rerank']:.4f}"
          f"  at ${config.RERANK_USD_PER_QUERY:.3f} per query")
    print(f"Generate calls (Nova):  {plan['generate_calls']:>7,}  ${cost['generate']:.4f}"
          f"  ({plan['est_input_tokens']:,} input tokens, {ANSWER_TOKENS} output per answer, estimated)")
    print(f"Estimated cost:         ${cost['total']:.4f}")
    print(f"Estimated time:         {plan['est_minutes']:.1f} minutes"
          f"  (rerank calls are {config.RERANK_MIN_INTERVAL_S} s apart)")


def _elapsed_ms(clock, start: float) -> float:
    return (clock() - start) * 1000


def run_question(conn, question: dict, exp: dict, embed_fn=None, rerank_client=None, generate_client=None,
                 sleep=time.sleep, clock=time.perf_counter) -> dict:
    """One question through one experiment: search, rerank and generate as the experiment says, each timed."""
    text, sources = question["question"], question["sources"]
    document = _document_filter(text, exp)

    vector, embed_ms, embed_cached = None, None, None
    if exp["mode"] != "keyword":
        embed_cached = text not in search.uncached_questions(conn, [text])
        start = clock()
        vector = search.question_vector(conn, text, embed_fn)
        embed_ms = _elapsed_ms(clock, start)

    exact_ids = None
    if exp["hnsw"]:
        if exp["mode"] != "vector":
            raise ValueError(f"the HNSW experiment needs mode 'vector', got {exp['mode']!r}")
        exact_ids = [hit["id"] for hit in search.vector_search(conn, vector, exp["n"], exp["strategy"], document)]
        # Not timed: the throwaway index is built inside each call, so the time would be the build, not the search.
        hits = db.hnsw_nearest(conn, vector, exp["strategy"], exp["n"], document)
        search_ms = None
    else:
        start = clock()
        hits = _search(conn, text, exp, vector, document)
        search_ms = _elapsed_ms(clock, start)

    reranked, rerank_ms, rerank_wait_ms, rerank_calls = None, None, None, None
    if exp["rerank"]:
        rerank_calls = rerank.calls_needed(conn, text, hits)
        waited = [0.0]

        def wrapped(seconds: float) -> None:
            waited[0] += seconds
            sleep(seconds)

        start = clock()
        reranked = rerank.rerank(conn, text, hits, k=config.EVAL_RERANK_K, enabled=True, client=rerank_client,
                                 sleep=wrapped)
        rerank_wait_ms = waited[0] * 1000
        # Never below 0: the spacing inside `rerank` reads its own clock, which a test may not share.
        rerank_ms = max(_elapsed_ms(clock, start) - rerank_wait_ms, 0.0)

    generated, generate_ms = None, None
    if exp["generate"]:
        passages = (reranked if exp["rerank"] else hits)[:exp["k"]]
        start = clock()
        generated = generate.generate(text, passages, client=generate_client)
        generate_ms = _elapsed_ms(clock, start)

    return {
        "id": question["id"],
        "type": question["type"],
        "document_filter": document,
        "search_hits": _hit_names(hits),
        "rerank_hits": None if reranked is None else _hit_names(reranked),
        "search_rank": metrics.first_correct_rank(hits, sources),
        "rerank_rank": None if reranked is None else metrics.first_correct_rank(reranked, sources),
        "embed_ms": embed_ms,
        "embed_cached": embed_cached,
        "search_ms": search_ms,
        "rerank_ms": rerank_ms,
        "rerank_wait_ms": rerank_wait_ms,
        "rerank_calls": rerank_calls,
        "generate_ms": generate_ms,
        "answer": None if generated is None else generated["answer"],
        "cited": None if generated is None else [f"{s['document']}:{s['page']}" for s in generated["sources"]],
        "not_contained": None if generated is None else generated["not_contained"],
        "invalid": None if generated is None else generated["invalid"],
        "input_tokens": None if generated is None else generated["input_tokens"],
        "output_tokens": None if generated is None else generated["output_tokens"],
        "_question": text,
        "_search": hits,
        "_rerank": reranked,
        "_exact_ids": exact_ids,
        "_generated": generated,
    }


def _median(values) -> float | None:
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def _mean(values) -> float | None:
    values = [value for value in values if value is not None]
    return statistics.mean(values) if values else None


def _retrieval_metrics(results: list[list[dict]], truths: list) -> dict:
    """recall@k for each k in EVAL_KS and mrr@10; None for all when no question has an answer."""
    keys = [f"recall@{k}" for k in config.EVAL_KS] + ["mrr@10"]
    if not any(truths):
        return dict.fromkeys(keys)
    values = {f"recall@{k}": metrics.recall_at_k(results, truths, k) for k in config.EVAL_KS}
    values["mrr@10"] = metrics.mrr(results, truths, k=10)
    return values


def _generation_metrics(rows: list[dict], truths: list) -> dict:
    answers = [row["_generated"] for row in rows]
    refusals = metrics.refusal_rates(answers, truths)
    return {
        "generate_ms": _median(row["generate_ms"] for row in rows),
        "citation_hit_rate": metrics.citation_hit_rate(answers, truths),
        "unanswerable_refused": refusals["unanswerable_refused"],
        "answerable_refused": refusals["answerable_refused"],
        "invalid_labels": sum(len(answer["invalid"]) for answer in answers),
    }


def _cost_per_query(rows: list[dict], exp: dict, reranked: bool, generated: bool) -> float:
    """What one uncached query costs in this setup: question embed, rerank queries, generate tokens."""
    cost = 0.0
    if exp["mode"] != "keyword" and rows:
        cost += _embed_usd([row["_question"] for row in rows]) / len(rows)
    if reranked:
        cost += math.ceil(exp["n"] / config.RERANK_MAX_DOCS) * config.RERANK_USD_PER_QUERY
    if generated:
        cost += (_mean(row["input_tokens"] for row in rows) or 0) / 1000 * config.GENERATE_USD_PER_1K_INPUT
        cost += (_mean(row["output_tokens"] for row in rows) or 0) / 1000 * config.GENERATE_USD_PER_1K_OUTPUT
    return cost


def _summary_row(rows: list[dict], truths: list, exp: dict, reranked: bool, spent: float, run_at: str) -> dict:
    """One row of runs/results.csv: the search order (reranked False) or the reranked order (True)."""
    results = [row["_rerank"] if reranked else row["_search"] for row in rows]
    # Generation metrics go on the row of the order the passages were taken from.
    generated = exp["generate"] and reranked == exp["rerank"]
    index_recall = None
    if exp["hnsw"] and any(row["_exact_ids"] for row in rows):
        index_recall = metrics.index_recall([[hit["id"] for hit in row["_search"]] for row in rows],
                                            [row["_exact_ids"] for row in rows])
    reranking = [row for row in rows if reranked and row["rerank_calls"]]
    return {
        "experiment": exp["name"], "strategy": exp["strategy"], "mode": exp["mode"], "ts_config": exp["ts_config"],
        "rerank": reranked, "k": exp["k"], "detect_part": exp["detect_part"], "hnsw": exp["hnsw"],
        "questions": len(rows), "answerable": sum(1 for truth in truths if truth),
        **_retrieval_metrics(results, truths),
        "index_recall": index_recall,
        "embed_ms": _median(row["embed_ms"] for row in rows if row["embed_cached"] is False),
        "search_ms": _median(row["search_ms"] for row in rows),
        "rerank_ms": _median(row["rerank_ms"] for row in reranking),
        "rerank_wait_ms": _median(row["rerank_wait_ms"] for row in reranking),
        **(_generation_metrics(rows, truths) if generated else
           dict.fromkeys(("generate_ms", "citation_hit_rate", "unanswerable_refused", "answerable_refused",
                          "invalid_labels"))),
        "cost_per_query_usd": _cost_per_query(rows, exp, reranked, generated),
        "spent_usd": spent,
        "run_at": run_at,
    }


def _spent_since(logged_before: int | None) -> float | None:
    """Cost of the cost log rows added since `logged_before` rows; None when the log cannot be read, so the
    error that stopped a run is never hidden by this one."""
    spent = costlog.spent_since(logged_before)
    if spent is None:
        print(f"Could not read the cost log. Check {config.COST_LOG} by hand.", file=sys.stderr)
    return spent


def run_experiment(conn, questions_: list[dict], exp: dict, embed_fn=None, rerank_client=None,
                   generate_client=None, sleep=time.sleep, clock=time.perf_counter) -> dict:
    """Every question through one experiment: the per-question rows and one or two summary rows."""
    logged_before = costlog.row_count()
    rows = []
    for question in questions_:
        rows.append(run_question(conn, question, exp, embed_fn, rerank_client, generate_client, sleep, clock))
    spent = _spent_since(logged_before)
    run_at = datetime.now(UTC).isoformat(timespec="seconds")
    truths = [question["sources"] for question in questions_]
    summary = [_summary_row(rows, truths, exp, False, spent, run_at)]
    if exp["rerank"]:
        summary.append(_summary_row(rows, truths, exp, True, spent, run_at))
    return {"rows": rows, "summary": summary}


def _read_csv(path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _key(row: dict) -> tuple[str, str, str]:
    """(experiment, rerank, type) as text, so a row read back from the file matches a new one. Summary rows have
    no type; per-type rows need it, or each type would replace the one before."""
    return str(row["experiment"]), str(row["rerank"]), str(row.get("type", ""))


def _upsert(old_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    """An old row with the key of a new row is replaced where it stood; a new row with a key not seen goes at the end."""
    new_by_key = {_key(row): row for row in new_rows}
    old_keys = {_key(row) for row in old_rows}
    merged = [new_by_key.get(_key(row), row) for row in old_rows]
    return merged + [row for key, row in new_by_key.items() if key not in old_keys]


def write_results(summary_rows: list[dict], path=None) -> None:
    """Add the summary rows to runs/results.csv, replacing rows of the same experiment and reranker state."""
    path = config.RESULTS if path is None else path
    _write_csv(path, SUMMARY_FIELDS, _upsert(_read_csv(path), summary_rows))


def _cell(value):
    return "; ".join(str(item) for item in value) if isinstance(value, list) else value


def write_rows(rows: list[dict], path) -> None:
    """The per-question rows as CSV. Lists are joined with "; " and private `_` keys are left out."""
    fields = list(dict.fromkeys(key for row in rows for key in row if not key.startswith("_")))
    _write_csv(path, fields, [{key: _cell(row.get(key)) for key in fields} for row in rows])


def _type_rows(rows: list[dict], reranked: bool) -> list[dict]:
    """Per question: hit at rank 5 or better (0/1) and reciprocal rank within 10; None for no_answer."""
    values = []
    for row in rows:
        rank = row["rerank_rank" if reranked else "search_rank"]
        answerable = row["type"] != "no_answer"
        values.append({
            "type": row["type"],
            "recall@5": (rank is not None and rank <= 5) if answerable else None,
            "mrr@10": (1 / rank if rank is not None and rank <= 10 else 0.0) if answerable else None,
        })
    return values


def write_by_type(result: dict, path=None) -> None:
    """recall@5 and mrr@10 per question type for each summary row of one `run_experiment` result.

    Written to runs/results_by_type.csv, replacing rows of the same experiment and reranker state. A type with
    no answerable question (no_answer) is left out.
    """
    path = config.RESULTS_BY_TYPE if path is None else path
    new_rows = []
    for summary in result["summary"]:
        values = _type_rows(result["rows"], summary["rerank"])
        recall, mrr = metrics.by_type(values, "recall@5"), metrics.by_type(values, "mrr@10")
        for type_ in recall:
            count = sum(1 for value in values if value["type"] == type_ and value["recall@5"] is not None)
            new_rows.append({"experiment": summary["experiment"], "rerank": summary["rerank"], "type": type_,
                             "questions": count, "recall@5": recall[type_], "mrr@10": mrr[type_]})
    _write_csv(path, BY_TYPE_FIELDS, _upsert(_read_csv(path), new_rows))


def _selected(args) -> list[str]:
    if args.all:
        return list(experiments.EXPERIMENTS)
    if args.group:
        return experiments.group(args.group)
    return args.names


def _print_summary(summary: list[dict]) -> None:
    for row in summary:
        recall, mrr = row["recall@5"], row["mrr@10"]
        shown = "n/a" if recall is None else f"recall@5 {recall:.3f}, mrr@10 {mrr:.3f}"
        print(f"  {'reranked' if row['rerank'] else 'search  '}  {shown}", flush=True)


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run evaluation experiments over the question set.")
    parser.add_argument("names", nargs="*", help="experiment names (see rag/eval/experiments.py)")
    parser.add_argument("--group", help="every experiment of one group")
    parser.add_argument("--all", action="store_true", help="every experiment")
    parser.add_argument("--dry-run", action="store_true", help="print the calls, cost and time, then stop")
    parser.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    parser.add_argument("--questions", help=f"question file (default: {config.QUESTIONS})")
    args = parser.parse_args(argv)
    if sum((bool(args.names), bool(args.group), args.all)) != 1:
        parser.error("name experiments, or give --group or --all (one of the three)")
    try:
        exps = [experiments.experiment(name) for name in _selected(args)]
        questions_ = questions.load_questions(Path(args.questions) if args.questions else None)
    except (KeyError, ValueError, OSError) as error:
        sys.exit(f"Cannot start: {error}")

    try:
        conn = db.connect()
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    with conn:
        plan = plan_run(conn, questions_, exps)
        print_plan(plan)
        if args.dry_run:
            print("\nNothing was called. Leave out --dry-run to run.")
            return
        paid = plan["embed_calls"] + plan["rerank_calls"] + plan["generate_calls"]
        if paid and not args.yes and input("\nRun? [y/N] ").strip().lower() != "y":
            print("Stopped. Nothing was called.")
            return

        logged_before = costlog.row_count()
        try:
            for exp in exps:
                print(f"\n{exp['name']} ({exp['group']})", flush=True)
                result = run_experiment(conn, questions_, exp)
                write_rows(result["rows"], config.EVAL_DIR / f"{exp['name']}.csv")
                write_results(result["summary"])
                write_by_type(result)
                _print_summary(result["summary"])
        except (ClientError, BotoCoreError, psycopg.Error) as error:
            source = "The database" if isinstance(error, psycopg.Error) else "Bedrock"
            print(f"\n{source} stopped the run: {error}\nFinished experiments are written, and paid results are "
                  "cached. Run the same command again to carry on.")
            raise SystemExit(1) from error
        finally:
            spent = _spent_since(logged_before)
            if spent is not None:
                print(f"\nThis run: ${spent:.4f} ({config.COST_LOG})")
        print(f"Results: {config.RESULTS}, {config.RESULTS_BY_TYPE}, {config.EVAL_DIR}")


if __name__ == "__main__":
    main(sys.argv[1:])
