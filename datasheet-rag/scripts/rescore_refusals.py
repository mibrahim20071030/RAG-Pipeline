"""Re-check the saved answers with the current refusal rule, for free (no model call).

Run from datasheet-rag:  python scripts\\rescore_refusals.py           (prints what would change, writes nothing)
                         python scripts\\rescore_refusals.py --write   (updates the CSVs and the summary rows)

`generate.is_not_contained` learned reworded refusals on 2026-10-24. The answers in runs/eval/*.csv and
runs/deployed/*.csv were scored with the old rule, so this re-applies the current one to each saved answer. A row's
sources are its `cited` column: an answer that cites a page is never newly flagged, so the citation hit cannot change.

With --write the changed CSVs are saved, then the summaries are brought up to date:
- runs/results.csv: `unanswerable_refused` and `answerable_refused` of the row the answers were generated from
  (experiment = file name, reranked row). These are the only columns of that file that read the flag.
- runs/results_deployed.csv: rebuilt with `deployed_eval.summarise`, the same code as `deployed_eval summary`, so
  every column that reads the flag (`same_as_local` too) follows. The Aurora share per question is kept from the old
  rows, because it comes from ACU-hours read by hand in the console.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import config, generate
from rag.eval import deployed_eval, metrics, questions
from rag.eval.run_eval import _read_csv, _write_csv

FLAG = "not_contained"


def rescore_rows(rows: list[dict]) -> list[str]:
    """Set each answered row's flag by the current rule, in place; return the ids whose flag changed."""
    changed = []
    for row in rows:
        answer = row.get("answer") or ""
        if not answer:
            continue
        sources = deployed_eval._answer(row)["sources"]  # the `cited` column, read as the deployed summary reads it
        refused = generate.is_not_contained(answer, sources)
        if refused == deployed_eval._flag(row.get(FLAG)):
            continue
        # A newly found refusal must cite nothing, or the citation hit would change. (Older Layer 7 rows of the
        # "sentence, then only labels" kind keep their label pages in `cited`; those were refusals already.)
        if refused and sources:
            raise AssertionError(f"{row.get('id')}: a row that cites pages must never become a refusal")
        row[FLAG] = str(refused)
        changed.append(row["id"])
    return changed


def local_rates(rows: list[dict], truths: dict) -> dict:
    """The refusal rates of a local run's answers (one row per question), as run_eval computes them."""
    answered = [row for row in rows if row.get("answer") and row["id"] in truths]
    answers = [{FLAG: deployed_eval._flag(row[FLAG])} for row in answered]
    return metrics.refusal_rates(answers, [truths[row["id"]] for row in answered])


def _update_results(path: Path, values: dict[str, dict]) -> None:
    """Set the refusal columns of each named experiment's reranked row, keeping the file's own columns."""
    rows = _read_csv(path)
    for row in rows:
        if row.get("experiment") in values and row.get("rerank") == "True" and row.get("unanswerable_refused"):
            row.update(values[row["experiment"]])
    if rows:
        _write_csv(path, list(rows[0]), rows)


def _rebuild_deployed(questions_: list[dict]) -> None:
    """results_deployed.csv from the pass files with deployed_eval's own summary code, Aurora share kept."""
    old = {row["pass"]: row for row in _read_csv(config.RESULTS_DEPLOYED)}
    rows = deployed_eval.summarise(questions_, None)
    for row in rows:
        aurora = old.get(row["pass"], {}).get("aurora_usd_per_query")
        row["aurora_usd_per_query"] = float(aurora) if aurora not in (None, "") else None
    _write_csv(config.RESULTS_DEPLOYED, deployed_eval.SUMMARY_FIELDS, rows)


def rescore(questions_: list[dict], write: bool) -> dict[str, dict]:
    """Re-check every saved answer. Returns, per changed file, the ids (and, for a local run, the rates before and
    after)."""
    truths = {q["id"]: q["sources"] for q in questions_}
    report = {}
    for path in sorted([*config.EVAL_DIR.glob("*.csv"), *config.DEPLOYED_DIR.glob("*.csv")]):
        rows = _read_csv(path)
        if not rows or "answer" not in rows[0] or FLAG not in rows[0]:
            continue
        deployed = path.parent == config.DEPLOYED_DIR
        before = None if deployed else local_rates(rows, truths)
        changed = rescore_rows(rows)
        if not changed:
            continue
        report[str(path)] = {"stem": path.stem, "deployed": deployed, "ids": changed, "before": before,
                             "after": None if deployed else local_rates(rows, truths)}
        if write:
            _write_csv(path, list(rows[0]), rows)
    if write and report:
        local = {r["stem"]: r["after"] for r in report.values() if not r["deployed"]}
        if local:
            _update_results(config.RESULTS, local)
        # The deployed summary reads the pass files and the local reference run.
        if config.RESULTS_DEPLOYED.exists() and (any(r["deployed"] for r in report.values())
                                                 or config.DEPLOYED_LOCAL_REFERENCE in local
                                                 or deployed_eval.KB_LOCAL_REFERENCE in local):
            _rebuild_deployed(questions_)
    return report


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="update the CSVs and the summary rows")
    args = parser.parse_args(argv)
    report = rescore(questions.load_questions(), args.write)
    if not report:
        print("No saved answer changes under the current rule.")
        return
    for r in report.values():
        print(f"{r['stem']} ({'deployed' if r['deployed'] else 'local'}): {', '.join(r['ids'])} changed")
        if r["before"]:
            b, a = r["before"], r["after"]
            print(f"  unanswerable refused {b['unanswerable_refused']:.3f} -> {a['unanswerable_refused']:.3f}, "
                  f"answerable refused {b['answerable_refused']:.3f} -> {a['answerable_refused']:.3f}")
    print("\nWritten." if args.write else "\nNothing was written. Run with --write to apply.")


if __name__ == "__main__":
    main(sys.argv[1:])
