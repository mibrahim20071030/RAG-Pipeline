"""Evaluation metrics. Pure functions: no database, no AWS.

A hit is a dict with at least `document` and `page`. A truth (`sources`) is a list of acceptable (document, page)
pairs, as tuples or 2-lists. A question with an empty truth has no answer in the datasheets: the retrieval metrics
skip it, and `refusal_rates` checks that its answer is a refusal.
"""
from statistics import mean


def _pairs(sources) -> set[tuple[str, int]]:
    return {(document, page) for document, page in sources}


def is_correct(hit: dict, sources) -> bool:
    """True when the hit's (document, page) is one of the sources."""
    return (hit["document"], hit["page"]) in _pairs(sources)


def first_correct_rank(hits: list[dict], sources) -> int | None:
    """1-based rank of the first correct hit, or None when no hit is correct."""
    pairs = _pairs(sources)
    for rank, hit in enumerate(hits, start=1):
        if (hit["document"], hit["page"]) in pairs:
            return rank
    return None


def _answerable(results: list, truths: list) -> list[tuple[list[dict], list]]:
    """(hits, truth) for the questions that have an answer; ValueError on unequal lengths or none left."""
    if len(results) != len(truths):
        raise ValueError(f"{len(results)} result lists for {len(truths)} truths")
    pairs = [(hits, truth) for hits, truth in zip(results, truths) if truth]
    if not pairs:
        raise ValueError("no question with an answer to score")
    return pairs


def recall_at_k(results: list[list[dict]], truths: list, k: int) -> float:
    """Hit rate: the share of answerable questions with a correct hit in the first k."""
    pairs = _answerable(results, truths)
    return mean(0.0 if first_correct_rank(hits[:k], truth) is None else 1.0 for hits, truth in pairs)


def mrr(results: list[list[dict]], truths: list, k: int | None = None) -> float:
    """Mean reciprocal rank of the first correct hit over answerable questions; 0 when it is past k or missing."""
    scores = []
    for hits, truth in _answerable(results, truths):
        rank = first_correct_rank(hits, truth)
        scores.append(1 / rank if rank is not None and (k is None or rank <= k) else 0.0)
    return mean(scores)


def index_recall(approx_ids: list[list[int]], exact_ids: list[list[int]]) -> float:
    """Mean share of the exact search's ids that the approximate search also found. Empty exact lists are skipped."""
    if len(approx_ids) != len(exact_ids):
        raise ValueError(f"{len(approx_ids)} approximate id lists for {len(exact_ids)} exact ones")
    shares = [len(set(approx) & set(exact)) / len(set(exact)) for approx, exact in zip(approx_ids, exact_ids) if exact]
    if not shares:
        raise ValueError("no question with exact ids to compare")
    return mean(shares)


def _share(flags: list[bool]) -> float | None:
    return mean(1.0 if flag else 0.0 for flag in flags) if flags else None


def refusal_rates(answers: list[dict], truths: list) -> dict:
    """Share of unanswerable questions refused (want 1) and of answerable ones refused (want 0); None for no group."""
    if len(answers) != len(truths):
        raise ValueError(f"{len(answers)} answers for {len(truths)} truths")
    unanswerable = [answer["not_contained"] for answer, truth in zip(answers, truths) if not truth]
    answerable = [answer["not_contained"] for answer, truth in zip(answers, truths) if truth]
    return {"unanswerable_refused": _share(unanswerable), "answerable_refused": _share(answerable)}


def citation_hit_rate(answers: list[dict], truths: list) -> float | None:
    """Share of answerable questions whose answer cites at least one correct (document, page); None for none.
    A refusal is a miss even when labels follow it."""
    if len(answers) != len(truths):
        raise ValueError(f"{len(answers)} answers for {len(truths)} truths")
    hits = [
        not answer["not_contained"] and any(is_correct(source, truth) for source in answer["sources"])
        for answer, truth in zip(answers, truths)
        if truth
    ]
    return _share(hits)


def by_type(rows: list[dict], key: str) -> dict[str, float]:
    """Mean of `row[key]` per `row["type"]`, None values ignored (bools count as 0 or 1). Types with no value are left out."""
    values: dict[str, list[float]] = {}
    for row in rows:
        if row[key] is not None:
            values.setdefault(row["type"], []).append(float(row[key]))
    return {kind: mean(numbers) for kind, numbers in values.items()}
