"""Layer 7 tests for the retrieval and answer metrics. Written from the layer's contract, not from the code.

The metrics are pure functions: toy hit lists with known answers, no database and no AWS.
"""
import pytest

from rag.eval import metrics


def hit(document: str, page: int, chunk_id: int = 0) -> dict:
    return {"id": chunk_id, "document": document, "page": page, "text": f"{document} page {page}", "score": 0.5}


def ranked(*pairs: tuple[str, int]) -> list[dict]:
    """A hit list in the given order."""
    return [hit(document, page, index) for index, (document, page) in enumerate(pairs, start=1)]


def filler(count: int) -> list[tuple[str, int]]:
    """Hits that are never correct: pages of a document no question names."""
    return [("other", 100 + n) for n in range(count)]


def answer(not_contained: bool, sources: list[tuple[str, int]] = ()) -> dict:
    """A `generate()` result, with only the fields the metrics read filled in."""
    return {"answer": "x", "not_contained": not_contained, "invalid": [],
            "sources": [{"label": n, "id": n, "document": d, "page": p, "page_label": str(p)}
                        for n, (d, p) in enumerate(sources, start=1)]}


# ---------------------------------------------------------------- is_correct

def test_is_correct_with_tuple_sources():
    assert metrics.is_correct(hit("lm35", 3), [("lm35", 3)])
    assert not metrics.is_correct(hit("lm35", 4), [("lm35", 3)])
    assert not metrics.is_correct(hit("esp32_datasheet_en", 3), [("lm35", 3)])


def test_is_correct_with_two_item_lists_as_sources():
    assert metrics.is_correct(hit("lm35", 3), [["lm35", 3]])
    assert not metrics.is_correct(hit("lm35", 2), [["lm35", 3]])


def test_is_correct_any_of_several_sources():
    sources = [("lm35", 3), ("lm35", 7), ("esp32_datasheet_en", 12)]
    assert metrics.is_correct(hit("lm35", 7), sources)
    assert metrics.is_correct(hit("esp32_datasheet_en", 12), sources)
    assert not metrics.is_correct(hit("esp32_datasheet_en", 3), sources)


def test_is_correct_with_no_sources_is_false():
    assert not metrics.is_correct(hit("lm35", 3), [])


def test_is_correct_reads_only_document_and_page():
    assert metrics.is_correct({"document": "lm35", "page": 3}, [("lm35", 3)])


# ---------------------------------------------------------------- first_correct_rank

def test_first_correct_rank_is_one_based():
    assert metrics.first_correct_rank(ranked(("lm35", 3), ("lm35", 4)), [("lm35", 3)]) == 1
    assert metrics.first_correct_rank(ranked(("lm35", 1), ("lm35", 2), ("lm35", 3)), [("lm35", 3)]) == 3


def test_first_correct_rank_takes_the_first_of_several_correct_hits():
    hits = ranked(("lm35", 1), ("lm35", 7), ("lm35", 3))
    assert metrics.first_correct_rank(hits, [("lm35", 3), ("lm35", 7)]) == 2


def test_first_correct_rank_none_when_no_hit_is_correct():
    assert metrics.first_correct_rank(ranked(("lm35", 1), ("lm35", 2)), [("lm35", 3)]) is None


def test_first_correct_rank_of_an_empty_list_is_none():
    assert metrics.first_correct_rank([], [("lm35", 3)]) is None


def test_first_correct_rank_with_list_sources():
    assert metrics.first_correct_rank(ranked(("lm35", 1), ("lm35", 3)), [["lm35", 3]]) == 2


# ---------------------------------------------------------------- recall_at_k

TRUTH = [("lm35", 3)]


def at_rank(rank: int, length: int = 10) -> list[dict]:
    """A hit list of `length` with the correct hit (lm35 page 3) at `rank`, or nowhere when rank is None."""
    pairs = filler(length)
    if rank is not None:
        pairs[rank - 1] = ("lm35", 3)
    return ranked(*pairs)


def test_recall_at_k_known_answer():
    # Ranks 1, 2, 4 and none: top 1 has one of four, top 3 two, top 5 three.
    results = [at_rank(1), at_rank(2), at_rank(4), at_rank(None)]
    truths = [TRUTH] * 4
    assert metrics.recall_at_k(results, truths, 1) == pytest.approx(0.25)
    assert metrics.recall_at_k(results, truths, 3) == pytest.approx(0.5)
    assert metrics.recall_at_k(results, truths, 5) == pytest.approx(0.75)
    assert metrics.recall_at_k(results, truths, 10) == pytest.approx(0.75)


def test_recall_at_k_counts_a_question_once_however_many_correct_hits_it_has():
    hits = ranked(("lm35", 3), ("lm35", 7), ("lm35", 3))
    assert metrics.recall_at_k([hits], [[("lm35", 3), ("lm35", 7)]], 3) == 1.0


def test_recall_at_k_a_hit_exactly_at_rank_k_counts():
    assert metrics.recall_at_k([at_rank(5)], [TRUTH], 5) == 1.0
    assert metrics.recall_at_k([at_rank(5)], [TRUTH], 4) == 0.0


def test_recall_at_k_larger_than_the_list():
    assert metrics.recall_at_k([at_rank(2, length=3)], [TRUTH], 10) == 1.0


def test_recall_at_k_any_of_several_acceptable_pages():
    truths = [[("lm35", 3), ("lm35", 9)], [("lm35", 3), ("lm35", 9)]]
    results = [ranked(("lm35", 9), *filler(4)), ranked(*filler(4), ("lm35", 3))]
    assert metrics.recall_at_k(results, truths, 1) == pytest.approx(0.5)
    assert metrics.recall_at_k(results, truths, 5) == 1.0


def test_recall_at_k_a_question_with_no_hits_counts_as_a_miss():
    assert metrics.recall_at_k([[], at_rank(1)], [TRUTH, TRUTH], 5) == pytest.approx(0.5)


def test_recall_at_k_skips_unanswerable_questions():
    results = [at_rank(1), at_rank(None), at_rank(None)]
    truths = [TRUTH, [], []]
    assert metrics.recall_at_k(results, truths, 5) == 1.0


def test_recall_at_k_a_single_question():
    assert metrics.recall_at_k([at_rank(1)], [TRUTH], 1) == 1.0
    assert metrics.recall_at_k([at_rank(None)], [TRUTH], 1) == 0.0


def test_recall_at_k_accepts_list_sources():
    assert metrics.recall_at_k([at_rank(2)], [[["lm35", 3]]], 3) == 1.0


def test_recall_at_k_only_unanswerable_questions_raises():
    with pytest.raises(ValueError):
        metrics.recall_at_k([at_rank(1), at_rank(None)], [[], []], 5)


def test_recall_at_k_no_questions_raises():
    with pytest.raises(ValueError):
        metrics.recall_at_k([], [], 5)


def test_recall_at_k_length_mismatch_raises():
    with pytest.raises(ValueError):
        metrics.recall_at_k([at_rank(1), at_rank(2)], [TRUTH], 5)


# ---------------------------------------------------------------- mrr

def test_same_recall_at_5_different_mrr():
    """The required case: the correct chunk at rank 1 in one strategy and at rank 5 in the other."""
    first, fifth = [at_rank(1)], [at_rank(5)]
    assert metrics.recall_at_k(first, [TRUTH], 5) == metrics.recall_at_k(fifth, [TRUTH], 5) == 1.0
    assert metrics.mrr(first, [TRUTH]) == pytest.approx(1.0)
    assert metrics.mrr(fifth, [TRUTH]) == pytest.approx(0.2)


def test_same_recall_at_5_different_mrr_over_several_questions():
    truths = [TRUTH] * 3
    strategy_a = [at_rank(1), at_rank(1), at_rank(None)]
    strategy_b = [at_rank(5), at_rank(5), at_rank(None)]
    assert metrics.recall_at_k(strategy_a, truths, 5) == metrics.recall_at_k(strategy_b, truths, 5)
    assert metrics.mrr(strategy_a, truths) == pytest.approx(2 / 3)
    assert metrics.mrr(strategy_b, truths) == pytest.approx(0.4 / 3)


def test_mrr_known_answer():
    results = [at_rank(1), at_rank(2), at_rank(4), at_rank(None)]
    assert metrics.mrr(results, [TRUTH] * 4) == pytest.approx((1 + 1 / 2 + 1 / 4 + 0) / 4)


def test_mrr_uses_the_first_correct_rank():
    hits = ranked(("other", 1), ("lm35", 9), ("lm35", 3))
    assert metrics.mrr([hits], [[("lm35", 3), ("lm35", 9)]]) == pytest.approx(0.5)


def test_mrr_without_k_counts_any_rank():
    assert metrics.mrr([at_rank(20, length=25)], [TRUTH]) == pytest.approx(1 / 20)


def test_mrr_with_k_cuts_later_ranks_to_zero():
    results = [at_rank(1), at_rank(10, length=12), at_rank(11, length=12)]
    truths = [TRUTH] * 3
    assert metrics.mrr(results, truths, k=10) == pytest.approx((1 + 1 / 10 + 0) / 3)
    assert metrics.mrr(results, truths, k=9) == pytest.approx(1 / 3)
    assert metrics.mrr(results, truths) == pytest.approx((1 + 1 / 10 + 1 / 11) / 3)


def test_mrr_skips_unanswerable_questions():
    assert metrics.mrr([at_rank(2), at_rank(None)], [TRUTH, []]) == pytest.approx(0.5)


def test_mrr_only_unanswerable_questions_raises():
    with pytest.raises(ValueError):
        metrics.mrr([at_rank(1)], [[]])


def test_mrr_length_mismatch_raises():
    with pytest.raises(ValueError):
        metrics.mrr([at_rank(1)], [TRUTH, TRUTH])


# ---------------------------------------------------------------- index_recall

def test_index_recall_known_answer():
    approx = [[1, 2, 3], [5, 6]]
    exact = [[1, 2, 4], [5, 6]]
    assert metrics.index_recall(approx, exact) == pytest.approx((2 / 3 + 1) / 2)


def test_index_recall_ignores_order():
    assert metrics.index_recall([[3, 2, 1]], [[1, 2, 3]]) == 1.0


def test_index_recall_fewer_approximate_ids_than_exact():
    assert metrics.index_recall([[1]], [[1, 2, 3, 4]]) == pytest.approx(0.25)


def test_index_recall_nothing_in_common():
    assert metrics.index_recall([[7, 8]], [[1, 2]]) == 0.0


def test_index_recall_skips_questions_with_an_empty_exact_list():
    assert metrics.index_recall([[1, 2], [], [9]], [[1, 2], [], []]) == 1.0


def test_index_recall_nothing_left_raises():
    with pytest.raises(ValueError):
        metrics.index_recall([[1], []], [[], []])


def test_index_recall_length_mismatch_raises():
    with pytest.raises(ValueError):
        metrics.index_recall([[1], [2]], [[1]])


# ---------------------------------------------------------------- refusal_rates

def test_refusal_rates_known_answer():
    answers = [answer(True), answer(False), answer(True), answer(False), answer(False), answer(True)]
    truths = [[], [], TRUTH, TRUTH, TRUTH, TRUTH]
    rates = metrics.refusal_rates(answers, truths)
    assert rates == {"unanswerable_refused": pytest.approx(0.5), "answerable_refused": pytest.approx(0.5)}


def test_refusal_rates_all_unanswerable_refused_and_no_answerable_refused():
    rates = metrics.refusal_rates([answer(True), answer(False, TRUTH)], [[], TRUTH])
    assert rates == {"unanswerable_refused": 1.0, "answerable_refused": 0.0}


def test_refusal_rates_no_unanswerable_question_gives_none():
    rates = metrics.refusal_rates([answer(False), answer(True)], [TRUTH, TRUTH])
    assert rates["unanswerable_refused"] is None
    assert rates["answerable_refused"] == pytest.approx(0.5)


def test_refusal_rates_no_answerable_question_gives_none():
    rates = metrics.refusal_rates([answer(True)], [[]])
    assert rates == {"unanswerable_refused": 1.0, "answerable_refused": None}


# ---------------------------------------------------------------- citation_hit_rate

def test_citation_hit_rate_known_answer():
    answers = [
        answer(False, [("lm35", 3)]),                 # correct
        answer(False, [("lm35", 1), ("lm35", 3)]),    # one of two cited pages correct
        answer(False, [("lm35", 1)]),                 # wrong page
        answer(True),                                 # refused, nothing cited
        answer(True),                                 # unanswerable: not counted
    ]
    truths = [TRUTH, TRUTH, TRUTH, TRUTH, []]
    assert metrics.citation_hit_rate(answers, truths) == pytest.approx(0.5)


def test_citation_hit_rate_any_acceptable_source_counts():
    truths = [[("lm35", 3), ("esp32_datasheet_en", 12)]]
    assert metrics.citation_hit_rate([answer(False, [("esp32_datasheet_en", 12)])], truths) == 1.0


def test_citation_hit_rate_same_page_in_another_document_is_wrong():
    assert metrics.citation_hit_rate([answer(False, [("esp32_datasheet_en", 3)])], [TRUTH]) == 0.0


def test_citation_hit_rate_refusal_with_labels_is_a_miss():
    assert metrics.citation_hit_rate([answer(True, [("lm35", 3)])], [TRUTH]) == 0.0


def test_citation_hit_rate_none_without_answerable_questions():
    assert metrics.citation_hit_rate([answer(True), answer(False, [("lm35", 3)])], [[], []]) is None


# ---------------------------------------------------------------- by_type

def test_by_type_means_per_type():
    rows = [
        {"type": "single_value", "rank": 1.0},
        {"type": "single_value", "rank": 0.0},
        {"type": "table_lookup", "rank": 0.5},
    ]
    assert metrics.by_type(rows, "rank") == {"single_value": pytest.approx(0.5), "table_lookup": pytest.approx(0.5)}


def test_by_type_ignores_none_values():
    rows = [
        {"type": "single_value", "hit": 1.0},
        {"type": "single_value", "hit": None},
        {"type": "single_value", "hit": 0.0},
    ]
    assert metrics.by_type(rows, "hit") == {"single_value": pytest.approx(0.5)}


def test_by_type_leaves_out_a_type_with_only_none():
    rows = [{"type": "no_answer", "hit": None}, {"type": "comparison", "hit": 1}]
    assert metrics.by_type(rows, "hit") == {"comparison": pytest.approx(1.0)}


def test_by_type_counts_bools_as_zero_and_one():
    rows = [
        {"type": "rephrased", "ok": True},
        {"type": "rephrased", "ok": False},
        {"type": "rephrased", "ok": True},
        {"type": "rephrased", "ok": True},
    ]
    assert metrics.by_type(rows, "ok") == {"rephrased": pytest.approx(0.75)}


def test_by_type_of_no_rows_is_empty():
    assert metrics.by_type([], "hit") == {}
