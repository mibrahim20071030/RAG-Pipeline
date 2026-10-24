"""Print our pipeline and the Knowledge Base pipeline side by side on the frozen questions (Cycle 2 step 8c).

Run from datasheet-rag:  python scripts\\compare_pipelines.py

Free: it reads only what the runs wrote. Ours is the frozen setup's local run (gen_prefix_k10, reranked order), the
KB is `python -m rag.eval.kb_eval`. Both gave their top 10 passages to the same answer step.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import config
from rag.eval import questions
from rag.eval.run_eval import _read_csv as read

PIPELINES = {"ours": config.DEPLOYED_LOCAL_REFERENCE, "kb": "kb_managed"}
METRICS = [("recall@1", "R@1"), ("recall@3", "R@3"), ("recall@5", "R@5"), ("recall@10", "R@10"),
           ("mrr@10", "MRR@10"), ("citation_hit_rate", "Citation hit"), ("answerable_refused", "Answerable refused"),
           ("unanswerable_refused", "Unanswerable refused"), ("invalid_labels", "Invalid labels"),
           ("generate_ms", "Answer ms (median)"), ("cost_per_query_usd", "Cost per question $")]


def summary(name: str) -> dict:
    rows = [r for r in read(config.RESULTS) if r["experiment"] == name and r["rerank"] == "True"]
    if not rows:
        raise SystemExit(f"No reranked row for {name} in {config.RESULTS}. Run it first.")
    return rows[0]


def outcome(row: dict, sources: list) -> dict:
    """Per question: retrieved in the top 5, and answered right (a correct page cited, or refused when no answer)."""
    rank = int(row["rerank_rank"]) if row["rerank_rank"] else None
    cited = {tuple(c.rsplit(":", 1)) for c in row["cited"].split("; ") if c}
    truth = {(doc, str(page)) for doc, page in sources}
    refused = row["not_contained"] == "True"
    right = refused if not sources else (not refused and bool(cited & truth))
    return {"top5": rank is not None and rank <= 5, "right": right, "rank": rank, "refused": refused}


def fmt(value: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value or "–"
    return f"{number:.4f}" if number < 0.1 and number != 0 else f"{number:.3f}" if number <= 1 else f"{number:,.0f}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sums = {key: summary(name) for key, name in PIPELINES.items()}
    print(f"{'':24}{'Ours':>12}{'Knowledge Base':>18}")
    for field, label in METRICS:
        print(f"{label:24}{fmt(sums['ours'][field]):>12}{fmt(sums['kb'][field]):>18}")

    by_type = [r for r in read(config.RESULTS_BY_TYPE) if r["rerank"] == "True"]
    print(f"\nRecall@5 by type{'':8}{'Ours':>12}{'Knowledge Base':>18}")
    types = dict.fromkeys(r["type"] for r in by_type if r["experiment"] in PIPELINES.values())
    for type_ in types:
        values = {key: next((r["recall@5"] for r in by_type if r["experiment"] == name and r["type"] == type_), "")
                  for key, name in PIPELINES.items()}
        print(f"  {type_:22}{fmt(values['ours']):>12}{fmt(values['kb']):>18}")

    qs = {q["id"]: q for q in questions.load_questions()}
    rows = {key: {r["id"]: r for r in read(config.EVAL_DIR / f"{name}.csv")} for key, name in PIPELINES.items()}
    print("\nQuestions where only one pipeline answered right (rank = first correct passage):")
    for qid, q in qs.items():
        ours, theirs = (outcome(rows[key][qid], q["sources"]) for key in ("ours", "kb"))
        if ours["right"] != theirs["right"]:
            winner = "ours" if ours["right"] else "kb"
            print(f"  {qid} {q['type']:13} only {winner:4}  rank ours {ours['rank'] or '–'}, kb {theirs['rank'] or '–'}"
                  f"  {q['question'][:70]}")


if __name__ == "__main__":
    main()
