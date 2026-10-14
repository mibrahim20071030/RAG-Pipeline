"""Layer 7b tests for the judge inputs. No paid call and no RAGAS import, so they run on the project's Python and
in CI. The rebuild tests need the local Postgres (a throwaway schema) and skip themselves without it.
"""
import asyncio
import csv
import inspect
import math
from types import SimpleNamespace

import pytest

from rag import config, costlog, db, embed, rerank
from rag.eval import experiments, judge_inputs, ragas_run, run_eval

QUESTION = {"id": "q01", "type": "single_value", "question": "What is the absolute maximum supply voltage of the LM35?",
            "expected_answer": "35 V", "sources": [["lm35", 4]]}
NO_ANSWER = {"id": "q99", "type": "no_answer", "question": "What is the unit price of the LM35?",
             "expected_answer": "", "sources": []}


def _hit(page: int, text: str, document: str = "lm35") -> dict:
    return {"id": page, "document": document, "page": page, "page_label": str(page), "section": None,
            "kind": "text", "text": text, "score": 0.0}


PASSAGES = [_hit(4, "Supply voltage: -0.2 V to 35 V"), _hit(7, "Pin GPIO16[2] is shared."),
            _hit(10, "Quiescent current: 56 µA")]


# ---------------------------------------------------------------- strip_labels

def test_strip_labels_removes_citations_and_tidies_spaces():
    answer = "The maximum is 35 V [1]. The current is 56 µA [3] [1, 3]."
    assert judge_inputs.strip_labels(answer, PASSAGES) == "The maximum is 35 V. The current is 56 µA."


def test_strip_labels_keeps_a_copied_footnote_marker():
    assert judge_inputs.strip_labels("GPIO16[2] is shared [2].", PASSAGES) == "GPIO16[2] is shared."


def test_strip_labels_handles_ranges_and_glued_labels():
    assert judge_inputs.strip_labels("Up to 35 V[1-3].", PASSAGES) == "Up to 35 V."


def test_strip_labels_changes_nothing_but_the_labels():
    answer = "Offset is .5 mV [1] , see Note : VDD.\n\n- first [2]\n- second"
    assert judge_inputs.strip_labels(answer, PASSAGES) == "Offset is .5 mV , see Note : VDD.\n\n- first\n- second"


# ---------------------------------------------------------------- build_sample

RANKED = ["lm35:4", "lm35:7", "lm35:10"]


def _answer(text: str, cited: list[str], refused: bool = False, ranked: list[str] = RANKED) -> dict:
    return {"answer": text, "cited": cited, "not_contained": refused, "ranked": ranked}


def test_build_sample_shape():
    sample = judge_inputs.build_sample(QUESTION, _answer("It is 35 V [1].", ["lm35:4"]), PASSAGES, 3)
    assert sample["id"] == "q01" and sample["type"] == "single_value"
    assert sample["user_input"] == QUESTION["question"]
    assert sample["response"] == "It is 35 V."
    assert sample["reference"] == "35 V"
    assert sample["refused"] is False
    assert sample["retrieved_contexts"][0] == "LM35 (lm35), page 4\nSupply voltage: -0.2 V to 35 V"
    assert len(sample["retrieved_contexts"]) == len(PASSAGES)


def test_build_sample_raises_when_citations_differ():
    with pytest.raises(ValueError, match="q01"):
        judge_inputs.build_sample(QUESTION, _answer("It is 35 V [2].", ["lm35:4"]), PASSAGES, 3)


@pytest.mark.parametrize("cited", [[], ["lm35:4"]])
def test_build_sample_accepts_a_refusal_with_or_without_its_labels(cited):
    sample = judge_inputs.build_sample(QUESTION, _answer(f"{config.NOT_CONTAINED} [1]", cited, True), PASSAGES, 3)
    assert sample["refused"] is True
    assert sample["response"] == config.NOT_CONTAINED


def test_build_sample_raises_when_the_ranking_differs():
    with pytest.raises(ValueError, match="ranking"):
        judge_inputs.build_sample(QUESTION, _answer(config.NOT_CONTAINED, [], True, ["lm35:7"]), PASSAGES, 3)


def test_build_sample_keeps_the_first_k_as_passages():
    sample = judge_inputs.build_sample(QUESTION, _answer("It is 35 V [1].", ["lm35:4"]), PASSAGES, 2)
    assert len(sample["retrieved_contexts"]) == 2


def test_build_sample_refuses_an_answer_of_labels_only():
    with pytest.raises(ValueError, match="only labels"):
        judge_inputs.build_sample(QUESTION, _answer("[1]", ["lm35:4"]), PASSAGES, 3)


def test_build_sample_refusal_with_wrong_labels_raises():
    with pytest.raises(ValueError):
        judge_inputs.build_sample(QUESTION, _answer(f"{config.NOT_CONTAINED} [1]", ["lm35:10"], True), PASSAGES, 3)


# ---------------------------------------------------------------- load_answers

def test_load_answers_reads_a_7a_csv(tmp_path):
    rows = [{"id": "q01", "type": "single_value", "search_hits": ["lm35:9"], "rerank_hits": RANKED,
             "answer": "It is 35 V [1].", "cited": ["lm35:4", "lm35:7"], "not_contained": False},
            {"id": "q02", "type": "table_lookup", "search_hits": ["lm35:9", "lm35:5"], "rerank_hits": None,
             "answer": config.NOT_CONTAINED, "cited": [], "not_contained": True}]
    run_eval.write_rows(rows, tmp_path / "gen_x.csv")
    answers = judge_inputs.load_answers("gen_x", tmp_path)
    assert answers["q01"] == {"answer": "It is 35 V [1].", "cited": ["lm35:4", "lm35:7"], "not_contained": False,
                              "ranked": RANKED}
    # Without the reranker the passages came from the search order.
    assert answers["q02"] == {"answer": config.NOT_CONTAINED, "cited": [], "not_contained": True,
                              "ranked": ["lm35:9", "lm35:5"]}


def test_load_answers_refuses_a_retrieval_experiment(tmp_path):
    run_eval.write_rows([{"id": "q01", "type": "single_value", "search_rank": 1}], tmp_path / "baseline.csv")
    with pytest.raises(ValueError, match="generation"):
        judge_inputs.load_answers("baseline", tmp_path)


# ---------------------------------------------------------------- plan_judge

def _sample(id_: str, context: str) -> dict:
    return {"id": id_, "retrieved_contexts": [context]}


def test_plan_judge_counts_only_samples_not_done(monkeypatch):
    monkeypatch.setattr(config, "JUDGE_USD_PER_1K_INPUT", 0.001)
    monkeypatch.setattr(config, "JUDGE_USD_PER_1K_OUTPUT", 0.002)
    monkeypatch.setattr(config, "JUDGE_MIN_INTERVAL_S", 6)
    monkeypatch.setattr(config, "EMBED_PER_MIN", 60)
    samples = [_sample("q01", "x" * 4000), _sample("q02", "x" * 4000), _sample("q03", "x" * 4000)]
    plan = judge_inputs.plan_judge(samples, ["q02"], calls_per_answer=5, embeds_per_answer=4, context_reads=2,
                                   overhead_tokens=500, output_tokens=300)
    assert plan["answers"] == 2 and plan["skipped"] == 1
    assert plan["judge_calls"] == 10 and plan["embed_calls"] == 8
    # 2 answers x (1,000 context tokens x 2 reads + 500) x 1.3
    assert plan["est_input_tokens"] == 6500
    assert plan["est_output_tokens"] == 600
    assert plan["est_cost_usd"]["judge"] == pytest.approx(6.5 * 0.001 + 0.6 * 0.002)
    assert plan["est_cost_usd"]["total"] == pytest.approx(plan["est_cost_usd"]["judge"]
                                                          + plan["est_cost_usd"]["embed"])
    assert plan["est_minutes"] == pytest.approx(10 * 6 / 60 + 8 / 60)


def test_plan_judge_with_everything_done_is_free():
    plan = judge_inputs.plan_judge([_sample("q01", "abc")], {"q01"}, 5, 4, 1, 500, 300)
    assert plan["answers"] == 0 and plan["judge_calls"] == 0 and plan["est_cost_usd"]["total"] == 0


# ---------------------------------------------------------------- rebuild (local Postgres)

KEYWORD_EXP = {**experiments.experiment("gen_baseline"), "mode": "keyword"}


def test_rebuild_refuses_a_retrieval_experiment():
    with pytest.raises(ValueError):
        judge_inputs.rebuild_passages(None, QUESTION["question"], experiments.experiment("baseline"))


def test_rebuild_never_embeds(conn):
    with pytest.raises(RuntimeError, match="must not embed"):
        judge_inputs.rebuild_passages(conn, QUESTION["question"], experiments.experiment("gen_baseline"))


def test_rebuild_never_reranks(conn, monkeypatch):
    monkeypatch.setattr(run_eval, "_search", lambda *args: list(PASSAGES))
    with pytest.raises(RuntimeError, match="rerank_cache"):
        judge_inputs.rebuild_passages(conn, QUESTION["question"], KEYWORD_EXP)


def test_rebuild_uses_cached_rerank_scores_and_keeps_k(conn, monkeypatch):
    monkeypatch.setattr(run_eval, "_search", lambda *args: list(PASSAGES))
    text = QUESTION["question"]
    scores = {embed.text_hash(rerank.rerank_text(text, hit["text"])): score
              for hit, score in zip(PASSAGES, (0.1, 0.9, 0.5), strict=True)}
    db.store_rerank_scores(conn, embed.text_hash(text), rerank.rerank_key(), scores)
    hits = judge_inputs.rebuild_passages(conn, text, {**KEYWORD_EXP, "k": 2})
    assert [hit["page"] for hit in hits] == [7, 10]
    assert [hit["page"] for hit in judge_inputs.rebuild_ranking(conn, text, KEYWORD_EXP)] == [7, 10, 4]


def test_build_samples_leaves_out_no_answer_questions(monkeypatch):
    monkeypatch.setattr(judge_inputs, "rebuild_ranking", lambda *args: list(PASSAGES))
    answers = {"q01": _answer("It is 35 V [1].", ["lm35:4"]), "q99": _answer(config.NOT_CONTAINED, [], True)}
    samples = judge_inputs.build_samples(None, [QUESTION, NO_ANSWER], KEYWORD_EXP, answers)
    assert [sample["id"] for sample in samples] == ["q01"]


def test_build_samples_raises_for_a_missing_answer(monkeypatch):
    monkeypatch.setattr(judge_inputs, "rebuild_ranking", lambda *args: list(PASSAGES))
    with pytest.raises(ValueError, match="q01"):
        judge_inputs.build_samples(None, [QUESTION], KEYWORD_EXP, {})


# ---------------------------------------------------------------- ragas_run (no RAGAS import, no paid call)


class ThrottlingException(Exception):
    pass


@pytest.fixture
def judge_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(config, "RAGAS_DIR", tmp_path / "ragas")
    monkeypatch.setattr(config, "RAGAS_RESULTS", tmp_path / "results_ragas.csv")
    monkeypatch.setattr(config, "JUDGE_MIN_INTERVAL_S", 5)
    monkeypatch.setattr(config, "JUDGE_USD_PER_1K_INPUT", 0.0008)
    monkeypatch.setattr(config, "JUDGE_USD_PER_1K_OUTPUT", 0.0032)
    return tmp_path


class FakeClock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _reply(prompt_tokens=1000, completion_tokens=100):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))


def test_logged_completion_spaces_calls_and_logs_tokens(judge_files):
    clock = FakeClock()

    async def completion(**kwargs):
        return _reply()

    judge = ragas_run.logged_completion(completion, clock.sleep, clock)
    assert inspect.iscoroutinefunction(judge)  # instructor makes an async client only from a coroutine function

    async def three():
        for _ in range(3):
            await judge(model="m", messages=[])
            clock.now += 1  # the caller spends 1 s between calls

    asyncio.run(three())
    assert clock.slept == [4, 4]
    assert judge.stats == {"calls": 3, "input_tokens": 3000, "output_tokens": 300}
    rows = costlog.read_rows()
    assert len(rows) == 6
    assert {row["unit"] for row in rows} == {"input_tokens", "output_tokens"}
    assert all(row["operation"] == "judge" and row["model_id"] == config.JUDGE_MODEL_ID for row in rows)
    assert sum(float(row["est_cost_usd"]) for row in rows) == pytest.approx(3 * (0.0008 + 0.1 * 0.0032))


def test_logged_completion_retries_throttling(judge_files):
    clock, attempts = FakeClock(), []

    async def completion(**kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise ThrottlingException("Too many requests, please wait before trying again.")
        return _reply()

    judge = ragas_run.logged_completion(completion, clock.sleep, clock)
    asyncio.run(judge(model="m", messages=[]))
    assert len(attempts) == 3
    assert ragas_run.THROTTLE_WAIT_S in clock.slept and 2 * ragas_run.THROTTLE_WAIT_S in clock.slept
    assert judge.stats["calls"] == 1 and len(costlog.read_rows()) == 2


def test_logged_completion_raises_other_errors(judge_files):
    clock = FakeClock()

    async def completion(**kwargs):
        raise ValueError("bad request")

    judge = ragas_run.logged_completion(completion, clock.sleep, clock)
    with pytest.raises(ValueError):
        asyncio.run(judge(model="m", messages=[]))
    assert costlog.read_rows() == []


def _judged(id_, faithfulness, relevancy, refused=False):
    return {"id": id_, "type": "single_value", "refused": refused, "faithfulness": faithfulness,
            "answer_relevancy": relevancy, "statements": 2, "unsupported": "", "judge_calls": 3,
            "input_tokens": 1000, "output_tokens": 100}


def test_rows_resume_and_summary(judge_files):
    ragas_run.append_row("gen_x", _judged("q01", 1.0, 0.9))
    ragas_run.append_row("gen_x", _judged("q02", 0.5, 0.7))
    ragas_run.append_row("gen_x", _judged("q03", math.nan, 0.0, refused=True))
    samples = [{"id": f"q0{n}"} for n in range(1, 6)]
    assert [s["id"] for s in ragas_run.todo(samples, "gen_x")] == ["q04", "q05"]
    assert [s["id"] for s in ragas_run.todo(samples, "gen_x", limit=1)] == ["q04"]

    summary = ragas_run.summarise("gen_x", ragas_run.read_rows("gen_x"), "now")
    assert summary["answers"] == 3 and summary["answered"] == 2
    assert summary["faithfulness"] == pytest.approx(0.75)  # the NaN is left out
    assert summary["faithfulness_nan"] == 1
    assert summary["answer_relevancy"] == pytest.approx((0.9 + 0.7 + 0.0) / 3)
    assert summary["answer_relevancy_answered"] == pytest.approx(0.8)
    assert summary["judge_usd"] == pytest.approx(3 * (0.0008 + 0.1 * 0.0032))


def test_plan_counts_done_answers_and_the_limit(judge_files):
    ragas_run.append_row("gen_x", _judged("q01", 1.0, 0.9))
    samples = [{"id": f"q0{n}", "retrieved_contexts": ["x" * 400]} for n in range(1, 5)]
    plan = ragas_run.plan({"gen_x": samples})
    assert plan["answers"] == 3 and plan["skipped"] == 1
    assert plan["judge_calls"] == 3 * ragas_run.CALLS_PER_ANSWER
    limited = ragas_run.plan({"gen_x": samples}, limit=2)
    assert limited["answers"] == 2 and limited["skipped"] == 1


def test_write_summary_replaces_the_experiment_row(judge_files):
    base = {key: "" for key in ragas_run.SUMMARY_FIELDS}
    ragas_run.write_summary({**base, "experiment": "a", "answers": 1})
    ragas_run.write_summary({**base, "experiment": "b", "answers": 2})
    ragas_run.write_summary({**base, "experiment": "a", "answers": 3})
    with config.RAGAS_RESULTS.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(row["experiment"], row["answers"]) for row in rows] == [("b", "2"), ("a", "3")]
