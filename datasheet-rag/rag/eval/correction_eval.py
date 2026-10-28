"""Before and after the reviewers' notes (Cycle 2 step 9b), on both pipelines, with data/correction_cases.jsonl.

Each note fixes one of the 49 questions (q08, q24, q33). Its cases are the original question, two paraphrases (the
note must reach them too) and a negative control: a nearby question the note must NOT reach. A case passes when the
answer contains every `must_contain` value (spaces ignored) and cites one of its `sources` pages; a negative passes
when no note was used.

Run from datasheet-rag, against the database that holds the published notes (DB_TARGET=aurora for the deployed ones):
    python -m rag.eval.correction_eval similarities [--yes]   how close each case and each of the 49 questions
                                                              is to each published note
    python -m rag.eval.correction_eval run [--pipeline ours|kb|both] [--state before|after|both] [--yes]

`similarities` costs nothing for questions whose vectors are cached (the 49 are). Both pipelines match notes with
the same cosine similarity, so it covers both. (Until 2026-10-28 the KB side matched with its own Retrieve, which
`--kb` measured; runs/feedback/similarities.csv and kb_scores_no_rerank.csv keep those scores.)
`run` prints the paid calls and their estimated cost, then asks. Rows go to runs/feedback/<pipeline>_<state>.csv and
a summary row per pipeline, state and kind to runs/feedback/summary.csv.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from rag import config, costlog, db, kb, pipeline, search
from rag.eval.questions import load_questions

KINDS = ("original", "paraphrase", "negative")
# Rough cost per answer, for the plan: a new question on ours pays the reranker; the KB pays one Retrieve, and a
# Titan embed of the question for its notes when they are on.
OURS_NEW_USD, OURS_CACHED_USD = 0.0023, 0.0003
KB_USD = config.KB_USD_PER_RETRIEVE + 0.0003
KB_NOTES_EMBED_USD = 0.00002


def load_cases(path: Path | str | None = None) -> list[dict]:
    """The cases in file order. Raises ValueError for a case that is missing a field or has an unknown kind."""
    cases = []
    for number, line in enumerate(Path(path or config.CORRECTION_CASES).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        missing = [key for key in ("id", "fixes", "kind", "question", "must_contain", "sources") if key not in case]
        if missing or case["kind"] not in KINDS:
            raise ValueError(f"line {number}: missing {missing} or unknown kind {case.get('kind')!r}")
        case["sources"] = [tuple(source) for source in case["sources"]]
        cases.append(case)
    return cases


def _squash(text: str) -> str:
    return "".join(text.split()).lower()


def contains_all(answer: str, values: list[str]) -> bool:
    return all(_squash(value) in _squash(answer) for value in values)


def score(case: dict, result: dict, pipeline_name: str, state: str) -> dict:
    """One row: what was answered, which notes were used, and whether the case passed."""
    notes = [hit["correction_id"] for hit in result.get("passages", []) if hit.get("correction_id")]
    cited = [(source["document"], source["page"]) for source in result.get("sources", [])]
    cites_source = any(source in case["sources"] for source in cited)
    if case["kind"] == "negative":
        passed = not notes
    else:
        passed = contains_all(result["answer"], case["must_contain"]) and cites_source and not result["not_contained"]
    return {"id": case["id"], "fixes": case["fixes"], "kind": case["kind"], "pipeline": pipeline_name,
            "state": state, "notes_used": ";".join(notes), "contains_all": contains_all(result["answer"],
                                                                                        case["must_contain"]),
            "cites_source": cites_source, "not_contained": result["not_contained"], "passed": passed,
            "cited": ";".join(f"{d}:{p}" for d, p in cited), "answer": result["answer"]}


def summarise(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["pipeline"], row["state"], row["kind"])].append(row)
    return [{"pipeline": p, "state": s, "kind": k, "cases": len(group),
             "passed": sum(1 for row in group if row["passed"]),
             "notes_used": sum(1 for row in group if row["notes_used"]),
             "refused": sum(1 for row in group if row["not_contained"])}
            for (p, s, k), group in sorted(groups.items())]


def saved_rows(folder: Path | None = None) -> list[dict]:
    """Every row of the saved runs (<pipeline>_<state>.csv), so the summary covers a state run on its own day."""
    rows = []
    for name in config.PIPELINES:
        for state in ("before", "after"):
            path = (folder or config.FEEDBACK_EVAL_DIR) / f"{name}_{state}.csv"
            if path.exists():
                with path.open(newline="", encoding="utf-8") as f:
                    rows += [{**row, "passed": row["passed"] == "True", "not_contained": row["not_contained"] == "True"}
                             for row in csv.DictReader(f)]
    return rows


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _confirm(args, text: str) -> bool:
    return args.yes or input(f"\n{text} [y/N] ").strip().lower() == "y"


def similarities(args) -> None:
    """How close every case and every frozen question is to each published note (the same numbers on both
    pipelines). For setting CORRECTION_MIN_SIMILARITY."""
    cases = load_cases()
    questions = [(case["id"], case["kind"], case["question"]) for case in cases]
    questions += [(q["id"], "frozen", q["question"]) for q in load_questions()]
    with db.connect() as conn:
        unpaid = search.uncached_questions(conn, [q for _, _, q in questions])
        cost = len(unpaid) * 0.00002
        print(f"{len(questions)} questions; {len(unpaid)} need a Titan embed. About ${cost:.4f}.")
        if unpaid and not _confirm(args, "Run?"):
            print("Stopped. Nothing was called.")
            return
        rows = []
        for name, kind, question in questions:
            found = db.correction_similarities(conn, search.question_vector(conn, question))
            rows.append({"id": name, "kind": kind, "question": question,
                         "similarity": json.dumps({cid: round(sim, 4) for cid, sim in sorted(found.items())})})
    if not rows:
        print("No questions.")
        return
    out = config.FEEDBACK_EVAL_DIR / "similarities_shared.csv"  # similarities.csv keeps the --kb scores
    _write(out, rows)
    for row in rows:
        print(f"{row['id']:6} {row['kind']:10} {row['similarity']}")
    print(f"\nWritten: {out}. Threshold now: {config.CORRECTION_MIN_SIMILARITY}.")


def run(args) -> None:
    cases = load_cases()
    pipelines = ("ours", "kb") if args.pipeline == "both" else (args.pipeline,)
    states = ("before", "after") if args.state == "both" else (args.state,)
    with db.connect() as conn:
        new = len(search.uncached_questions(conn, [case["question"] for case in cases])) if "ours" in pipelines else 0
        answers = len(cases) * len(states)
        cost = 0.0
        if "ours" in pipelines:
            cost += new * OURS_NEW_USD + (answers - new) * OURS_CACHED_USD
        if "kb" in pipelines:
            cost += answers * KB_USD + (len(cases) * KB_NOTES_EMBED_USD if "after" in states else 0)
        print(f"{len(cases)} cases x {len(states)} state(s) x {len(pipelines)} pipeline(s) = "
              f"{answers * len(pipelines)} answers ({new} new questions on ours). About ${cost:.3f}. "
              f"Database: {config.DB_TARGET}.")
        if not _confirm(args, "Run?"):
            print("Stopped. Nothing was called.")
            return
        logged_before = costlog.row_count()
        rows = []
        for name in pipelines:
            for state in states:
                config.CORRECTIONS_ENABLED = state == "after"
                part = []
                for case in cases:
                    if name == "ours":
                        result = pipeline.answer_question(conn, case["question"])
                    else:
                        result = kb.answer_question(case["question"])
                    part.append(score(case, result, name, state))
                    print(f"  {name:4} {state:6} {case['id']}  {'pass' if part[-1]['passed'] else 'FAIL'}  "
                          f"notes={part[-1]['notes_used'] or '-'}")
                _write(config.FEEDBACK_EVAL_DIR / f"{name}_{state}.csv", part)
                rows += part
    summary = summarise(saved_rows())
    _write(config.FEEDBACK_EVAL_DIR / "summary.csv", summary)
    for row in summary:
        print(f"{row['pipeline']:4} {row['state']:6} {row['kind']:10} passed {row['passed']}/{row['cases']}  "
              f"notes used {row['notes_used']}  refused {row['refused']}")
    spent = costlog.spent_since(logged_before)
    print(f"Spent: ${spent:.4f}" if spent is not None else "Could not read the cost log.")


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Before and after the reviewers' notes, on both pipelines.")
    commands = parser.add_subparsers(dest="command", required=True)
    near = commands.add_parser("similarities", help="closeness of every question to each published note")
    near.add_argument("--yes", action="store_true")
    go = commands.add_parser("run", help="answer every case with notes off and on")
    go.add_argument("--pipeline", choices=("ours", "kb", "both"), default="both")
    go.add_argument("--state", choices=("before", "after", "both"), default="both")
    go.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    (similarities if args.command == "similarities" else run)(args)


if __name__ == "__main__":
    main(sys.argv[1:])
