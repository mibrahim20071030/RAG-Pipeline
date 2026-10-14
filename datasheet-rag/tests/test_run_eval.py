"""Layer 7 tests for the experiments and the evaluation runner. Written from the layer's contract, not from the code.

The experiment table and the CSV writers need nothing. The runs need the local Postgres and skip themselves without
it: they work in a throwaway schema (the `conn` fixture in conftest.py) on a few hand-made chunks with hand-made
vectors. The embedder, the reranker and the answer model are fakes, and the real Bedrock clients fail the test if
anything asks for one, so nothing here is paid.
"""
import csv
import math
from datetime import datetime

import pytest

from rag import config, costlog, db, embed, generate, rerank
from rag.chunk_base import approx_tokens
from rag.eval import experiments, run_eval

STRATEGY = "fixed_400"
DIMS = config.EMBED_DIMENSIONS

SUMMARY_COLUMNS = [
    "experiment", "strategy", "mode", "ts_config", "rerank", "k", "detect_part", "hnsw", "questions", "answerable",
    "recall@1", "recall@3", "recall@5", "recall@10", "mrr@10", "index_recall", "embed_ms", "search_ms", "rerank_ms",
    "rerank_wait_ms", "generate_ms", "citation_hit_rate", "unanswerable_refused", "answerable_refused",
    "invalid_labels", "cost_per_query_usd", "spent_usd", "run_at",
]
ROW_KEYS = {
    "id", "type", "document_filter", "search_hits", "rerank_hits", "search_rank", "rerank_rank", "embed_ms",
    "embed_cached", "search_ms", "rerank_ms", "rerank_wait_ms", "rerank_calls", "generate_ms", "answer", "cited",
    "not_contained", "invalid", "input_tokens", "output_tokens",
}
PLAN_KEYS = {"experiments", "questions", "embed_calls", "rerank_calls", "generate_calls", "est_input_tokens",
             "est_cost_usd", "est_minutes"}
GENERATION_COLUMNS = ["generate_ms", "citation_hit_rate", "unanswerable_refused", "answerable_refused",
                      "invalid_labels"]


# ---------------------------------------------------------------- experiments (no database)

EXPECTED_EXPERIMENTS = {
    "baseline": ("baseline", {}),
    "chunk_page": ("chunking", {"strategy": "page"}),
    "chunk_fixed_256": ("chunking", {"strategy": "fixed_256"}),
    "chunk_fixed_256_overlap": ("chunking", {"strategy": "fixed_256_overlap"}),
    "chunk_fixed_400_overlap": ("chunking", {"strategy": "fixed_400_overlap"}),
    "chunk_structure_aware": ("chunking", {"strategy": "structure_aware"}),
    "chunk_fixed_1024": ("chunking", {"strategy": "fixed_1024"}),
    "vector_only": ("retrieval", {"mode": "vector"}),
    "keyword_only": ("retrieval", {"mode": "keyword"}),
    "simple_hybrid": ("keyword", {"ts_config": "simple"}),
    "simple_keyword_only": ("keyword", {"ts_config": "simple", "mode": "keyword"}),
    "detect_part": ("filter", {"detect_part": True}),
    "hnsw": ("index", {"hnsw": True, "mode": "vector"}),
    "header_prefix": ("prefix", {"strategy": "fixed_400_prefix"}),
}


def test_baseline_is_the_contract_baseline():
    assert experiments.BASELINE == {
        "strategy": "fixed_400", "mode": "hybrid", "ts_config": "english", "n": 30, "rerank": True, "k": 5,
        "detect_part": False, "hnsw": False, "generate": False,
    }


@pytest.mark.parametrize("name", list(EXPECTED_EXPERIMENTS))
def test_every_contract_experiment_exists_with_its_group_and_overrides(name):
    group, overrides = EXPECTED_EXPERIMENTS[name]
    assert experiments.EXPERIMENTS[name] == {"group": group, **overrides}


def test_no_other_experiment_except_the_generation_group():
    extra = set(experiments.EXPERIMENTS) - set(EXPECTED_EXPERIMENTS)
    assert all(experiments.EXPERIMENTS[name]["group"] == "generation" for name in extra)


@pytest.mark.parametrize("name", list(EXPECTED_EXPERIMENTS))
def test_experiment_merges_the_overrides_on_the_baseline_and_adds_the_name(name):
    group, overrides = EXPECTED_EXPERIMENTS[name]
    assert experiments.experiment(name) == {**experiments.BASELINE, **overrides, "name": name, "group": group}


def test_experiment_does_not_change_the_baseline_or_the_table():
    baseline, table = dict(experiments.BASELINE), {k: dict(v) for k, v in experiments.EXPERIMENTS.items()}
    result = experiments.experiment("hnsw")
    result["n"] = 1
    experiments.experiment("chunk_page")["strategy"] = "changed"
    assert experiments.BASELINE == baseline
    assert experiments.EXPERIMENTS == table
    assert experiments.experiment("hnsw")["n"] == 30


def test_unknown_experiment_raises_key_error_naming_the_known_ones():
    with pytest.raises(KeyError) as error:
        experiments.experiment("no_such_experiment")
    assert "baseline" in str(error.value)


def test_chunking_group_has_the_six_names_in_order():
    assert experiments.group("chunking") == [
        "chunk_page", "chunk_fixed_256", "chunk_fixed_256_overlap", "chunk_fixed_400_overlap",
        "chunk_structure_aware", "chunk_fixed_1024",
    ]


def test_other_groups():
    assert experiments.group("baseline") == ["baseline"]
    assert experiments.group("retrieval") == ["vector_only", "keyword_only"]
    assert experiments.group("keyword") == ["simple_hybrid", "simple_keyword_only"]
    assert experiments.group("index") == ["hnsw"]


def test_unknown_group_raises_key_error():
    with pytest.raises(KeyError):
        experiments.group("no_such_group")


# ---------------------------------------------------------------- write_results and write_rows (no database)

def summary_row(experiment: str, rerank_on: bool, recall: float = 0.5) -> dict:
    row = dict.fromkeys(SUMMARY_COLUMNS)
    row.update({"experiment": experiment, "strategy": STRATEGY, "mode": "hybrid", "ts_config": "english",
                "rerank": rerank_on, "k": 5, "detect_part": False, "hnsw": False, "questions": 3, "answerable": 2,
                "recall@1": recall, "recall@3": recall, "recall@5": recall, "recall@10": recall, "mrr@10": recall,
                "spent_usd": 0.0, "run_at": "2026-10-14T12:00:00+00:00"})
    return row


def read_csv(path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def keys(rows: list[dict]) -> list[tuple[str, str]]:
    return [(row["experiment"], row["rerank"]) for row in rows]


def test_write_results_header_is_the_contract_columns_in_order(tmp_path):
    path = tmp_path / "results.csv"
    run_eval.write_results([summary_row("baseline", False)], path)
    header, rows = read_csv(path)
    assert header == SUMMARY_COLUMNS
    assert len(rows) == 1
    assert rows[0]["experiment"] == "baseline" and rows[0]["recall@5"] == "0.5"


def test_write_results_none_is_an_empty_cell(tmp_path):
    path = tmp_path / "results.csv"
    run_eval.write_results([summary_row("baseline", False)], path)
    row = read_csv(path)[1][0]
    assert row["index_recall"] == "" and row["rerank_ms"] == ""


def test_write_results_upserts_by_experiment_and_reranker_state(tmp_path):
    path = tmp_path / "results.csv"
    run_eval.write_results([summary_row("baseline", False, 0.1), summary_row("baseline", True, 0.2)], path)
    run_eval.write_results([summary_row("chunk_page", False, 0.3)], path)
    run_eval.write_results([summary_row("baseline", True, 0.9), summary_row("vector_only", False, 0.4)], path)
    header, rows = read_csv(path)
    assert header == SUMMARY_COLUMNS
    assert sorted(keys(rows)) == sorted([("baseline", "False"), ("baseline", "True"), ("chunk_page", "False"),
                                         ("vector_only", "False")])
    recall = {(row["experiment"], row["rerank"]): row["recall@5"] for row in rows}
    assert recall == {("baseline", "False"): "0.1", ("baseline", "True"): "0.9", ("chunk_page", "False"): "0.3",
                      ("vector_only", "False"): "0.4"}
    # Rows not replaced keep their order; a row never seen before goes at the end.
    kept = [key for key in keys(rows) if key != ("baseline", "True")]
    assert kept == [("baseline", "False"), ("chunk_page", "False"), ("vector_only", "False")]


def test_write_by_type_keeps_one_row_per_type(tmp_path):
    """Found on the real run: the merge key had no type, so each type replaced the one before."""
    path = tmp_path / "by_type.csv"
    rows = [{"type": "single_value", "search_rank": 1, "rerank_rank": 1},
            {"type": "table_lookup", "search_rank": 7, "rerank_rank": 2},
            {"type": "no_answer", "search_rank": None, "rerank_rank": None}]
    result = {"rows": rows, "summary": [{"experiment": "baseline", "rerank": False},
                                        {"experiment": "baseline", "rerank": True}]}
    run_eval.write_by_type(result, path)
    run_eval.write_by_type(result, path)
    _, written = read_csv(path)
    assert sorted((row["rerank"], row["type"], row["recall@5"]) for row in written) == [
        ("False", "single_value", "1.0"), ("False", "table_lookup", "0.0"),
        ("True", "single_value", "1.0"), ("True", "table_lookup", "1.0")]


def test_write_results_the_same_rows_twice_do_not_grow_the_file(tmp_path):
    path = tmp_path / "results.csv"
    rows = [summary_row("baseline", False), summary_row("baseline", True)]
    run_eval.write_results(rows, path)
    run_eval.write_results(rows, path)
    assert len(read_csv(path)[1]) == 2


def test_write_results_path_none_writes_the_configured_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS", tmp_path / "results.csv")
    run_eval.write_results([summary_row("baseline", False)])
    assert keys(read_csv(tmp_path / "results.csv")[1]) == [("baseline", "False")]


def test_write_rows_drops_private_keys_and_joins_lists(tmp_path):
    rows = [
        {"id": "q01", "type": "single_value", "search_hits": ["lm35:2", "lm35:1"], "search_rank": 1,
         "_search": [{"id": 1}], "_generated": {"answer": "x"}},
        {"id": "q02", "type": "no_answer", "search_hits": [], "search_rank": None, "_search": []},
    ]
    path = tmp_path / "eval" / "nested" / "baseline.csv"
    run_eval.write_rows(rows, path)
    header, read = read_csv(path)
    assert not any(column.startswith("_") for column in header)
    assert {"id", "type", "search_hits", "search_rank"} <= set(header)
    assert read[0]["search_hits"] == "lm35:2; lm35:1"
    assert read[0]["id"] == "q01" and read[0]["search_rank"] == "1"
    assert read[1]["id"] == "q02" and read[1]["search_rank"] == ""


# ---------------------------------------------------------------- database helpers

def axis(index: int, value: float = 1.0) -> list[float]:
    vector = [0.0] * DIMS
    vector[index] = value
    return vector


def mix(a: float, b: float) -> list[float]:
    """A vector with `a` on axis 0 and `b` on axis 1. With a*a + b*b = 1 its cosine with axis 0 is exactly `a`."""
    return [float(a), float(b)] + [0.0] * (DIMS - 2)


QUERY_VEC = mix(1, 0)

# (document, page, text, vector). Cosine with the question vector: 1.0, 0.8, 0.6, 0.28, then 0 for the rest, so
# the exact vector order is lm35:2, esp32:1, lm35:1, esp32:2, then the others by id.
CHUNKS = [
    ("lm35", 1, "LM35 temperature sensor general description", mix(0.6, 0.8)),
    ("lm35", 2, "Supply voltage range 4 V to 30 V", mix(1, 0)),
    ("lm35", 3, "Package outline drawing", mix(0, 1)),
    ("esp32_datasheet_en", 1, "ESP32 overview wifi bluetooth", mix(0.8, 0.6)),
    ("esp32_datasheet_en", 2, "GPIO maximum current 40 mA per pin", mix(0.28, 0.96)),
] + [("esp32_datasheet_en", page, f"filler block number {page}", axis(page)) for page in range(3, 11)]

QUESTIONS = [
    {"id": "q1", "question": "What is the supply voltage range of the LM35 sensor?", "expected_answer": "4 V to 30 V",
     "sources": [("lm35", 2)], "type": "single_value"},
    {"id": "q2", "question": "What is the maximum GPIO current per pin?", "expected_answer": "40 mA",
     "sources": [("esp32_datasheet_en", 2)], "type": "table_lookup"},
    {"id": "q3", "question": "What is the price of the chip?", "expected_answer": "Not in the datasheets.",
     "sources": [], "type": "no_answer"},
]


def store(conn, strategy: str = STRATEGY) -> None:
    for document in ("lm35", "esp32_datasheet_en"):
        items = [c for c in CHUNKS if c[0] == document]
        chunks = [{"page": page, "page_label": str(page), "section": None, "kind": "text", "text": text}
                  for _, page, text, _ in items]
        db.replace_chunks(conn, db.upsert_document(conn, document), strategy, chunks, [v for *_, v in items])


def exp(name: str, **overrides) -> dict:
    return {**experiments.experiment(name), **overrides}


class FakeEmbedder:
    def __init__(self):
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return list(QUERY_VEC)


def no_embedding(text):
    raise AssertionError(f"the embedder must not be called (asked for {text!r})")


class FakeReranker:
    """Answers like the Rerank API. A document's score is the share of the question's words it contains."""

    def __init__(self):
        self.requests = []

    def rerank(self, **request):
        self.requests.append(request)
        words = set(request["queries"][0]["textQuery"]["text"].lower().split())
        results = []
        for index, source in enumerate(request["sources"]):
            text = source["inlineDocumentSource"]["textDocument"]["text"].lower().split()
            results.append({"index": index, "relevanceScore": len(words & set(text)) / len(words)})
        return {"results": sorted(results, key=lambda r: -r["relevanceScore"])}


class FakeLLM:
    """Answers like the Converse API: the fixed refusal for the price question, otherwise a cited answer."""

    def __init__(self, answer: str = "The value is given there [1]."):
        self.answer, self.requests = answer, []

    def converse(self, **request):
        self.requests.append(request)
        user = request["messages"][0]["content"][0]["text"]
        text = config.NOT_CONTAINED if "price" in user.split("Question:")[-1] else self.answer
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1200, "outputTokens": 30, "totalTokens": 1230},
            "metrics": {"latencyMs": 640},
        }


class FakeTime:
    """A clock that moves 1 ms at each reading and by the slept seconds at each sleep."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self) -> float:
        self.now += 0.001
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def env(conn, monkeypatch, tmp_path):
    """The two documents stored, every output file in a temporary folder, fakes in place of the paid clients."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(config, "RESULTS", tmp_path / "results.csv")
    monkeypatch.setattr(config, "RESULTS_BY_TYPE", tmp_path / "results_by_type.csv")
    monkeypatch.setattr(config, "EVAL_DIR", tmp_path / "eval")
    monkeypatch.setattr(config, "DETECT_PART", False)
    monkeypatch.setattr(rerank, "_last_call", None)
    for module in (rerank, generate, embed):
        monkeypatch.setattr(module, "_bedrock", lambda: pytest.fail("the real client must not be made in a test"))
    monkeypatch.setattr(embed, "embed_text", no_embedding)
    store(conn)

    class Env:
        pass

    e = Env()
    e.conn, e.embedder, e.reranker, e.llm, e.time = conn, FakeEmbedder(), FakeReranker(), FakeLLM(), FakeTime()

    def run(experiment: dict, questions=QUESTIONS, embed_fn=None):
        return run_eval.run_experiment(conn, questions, experiment, embed_fn=embed_fn or e.embedder,
                                       rerank_client=e.reranker, generate_client=e.llm, sleep=e.time.sleep,
                                       clock=e.time.clock)

    def one(question: dict, experiment: dict, embed_fn=None):
        return run_eval.run_question(conn, question, experiment, embed_fn=embed_fn or e.embedder,
                                     rerank_client=e.reranker, generate_client=e.llm, sleep=e.time.sleep,
                                     clock=e.time.clock)

    e.run, e.one = run, one
    return e


def by_rerank(result: dict, on: bool) -> dict:
    rows = [row for row in result["summary"] if row["rerank"] is on]
    assert len(rows) == 1, result["summary"]
    return rows[0]


# ---------------------------------------------------------------- run_experiment: summary rows

def test_reranker_on_gives_two_summary_rows_with_every_column(env):
    result = env.run(exp("baseline"))
    assert sorted(row["rerank"] for row in result["summary"]) == [False, True]
    for row in result["summary"]:
        assert set(SUMMARY_COLUMNS) <= set(row)
        assert row["experiment"] == "baseline"
        assert (row["strategy"], row["mode"], row["ts_config"], row["k"]) == (STRATEGY, "hybrid", "english", 5)
        assert row["detect_part"] is False and row["hnsw"] is False
        assert (row["questions"], row["answerable"]) == (3, 2)
    assert len(result["rows"]) == 3


def test_reranker_off_gives_one_summary_row(env):
    result = env.run(exp("vector_only", rerank=False))
    assert [row["rerank"] for row in result["summary"]] == [False]
    assert set(SUMMARY_COLUMNS) <= set(result["summary"][0])
    assert env.reranker.requests == []


def test_recall_and_mrr_follow_the_search_and_the_reranked_order(env):
    result = env.run(exp("vector_only"))
    off, on = by_rerank(result, False), by_rerank(result, True)
    # Search order: q1 correct at rank 1, q2 at rank 4. Reranked: both at rank 1. q3 has no answer and is skipped.
    assert [off[f"recall@{k}"] for k in config.EVAL_KS] == [0.5, 0.5, 1.0, 1.0]
    assert off["mrr@10"] == pytest.approx((1 + 1 / 4) / 2)
    assert [on[f"recall@{k}"] for k in config.EVAL_KS] == [1.0, 1.0, 1.0, 1.0]
    assert on["mrr@10"] == pytest.approx(1.0)


def test_columns_not_measured_are_empty(env):
    result = env.run(exp("vector_only"))
    off, on = by_rerank(result, False), by_rerank(result, True)
    for row in (off, on):
        assert row["index_recall"] is None
        for column in GENERATION_COLUMNS:
            assert row[column] is None, column
    assert off["rerank_ms"] is None and off["rerank_wait_ms"] is None
    assert on["rerank_ms"] is not None and on["rerank_wait_ms"] is not None


def test_run_at_is_utc_iso_time_in_seconds(env):
    row = env.run(exp("vector_only", rerank=False))["summary"][0]
    moment = datetime.fromisoformat(row["run_at"])
    assert moment.microsecond == 0
    assert moment.utcoffset() is not None and moment.utcoffset().total_seconds() == 0


def test_spent_is_what_the_run_added_to_the_cost_log(env):
    costlog.log_cost("earlier", "m", "queries", 1, 5.0)  # a row from before the run: not counted
    result = env.run(exp("vector_only"))
    assert len(env.reranker.requests) == 3
    for row in result["summary"]:
        assert row["spent_usd"] == pytest.approx(3 * config.RERANK_USD_PER_QUERY)


def test_cost_per_query_adds_the_rerank_query_on_the_rerank_row(env):
    result = env.run(exp("vector_only"))
    off, on = by_rerank(result, False), by_rerank(result, True)
    rerank_cost = math.ceil(30 / config.RERANK_MAX_DOCS) * config.RERANK_USD_PER_QUERY
    assert on["cost_per_query_usd"] - off["cost_per_query_usd"] == pytest.approx(rerank_cost)
    assert off["cost_per_query_usd"] > 0  # the question embedding


def test_cost_per_query_in_keyword_mode_without_reranker_is_zero(env):
    row = env.run(exp("keyword_only", rerank=False), embed_fn=no_embedding)["summary"][0]
    assert row["cost_per_query_usd"] == pytest.approx(0.0)


# ---------------------------------------------------------------- run_question / rows

def test_a_row_has_every_contract_key(env):
    for row in env.run(exp("baseline", generate=True))["rows"]:
        assert {key for key in row if not key.startswith("_")} == ROW_KEYS


def test_a_row_without_reranker_and_generation_has_every_key_with_those_fields_empty(env):
    row = env.one(QUESTIONS[0], exp("vector_only", rerank=False))
    assert {key for key in row if not key.startswith("_")} == ROW_KEYS
    for key in ("rerank_hits", "rerank_rank", "rerank_ms", "rerank_wait_ms", "rerank_calls", "generate_ms",
                "answer", "cited", "not_contained", "invalid", "input_tokens", "output_tokens"):
        assert row[key] is None, key


def test_rows_follow_the_question_order_and_carry_id_and_type(env):
    rows = env.run(exp("vector_only", rerank=False))["rows"]
    assert [(row["id"], row["type"]) for row in rows] == [("q1", "single_value"), ("q2", "table_lookup"),
                                                         ("q3", "no_answer")]


def test_search_rank_and_rerank_rank_are_where_the_correct_page_is(env):
    rows = env.run(exp("vector_only"))["rows"]
    assert [row["search_rank"] for row in rows] == [1, 4, None]
    assert [row["rerank_rank"] for row in rows] == [1, 1, None]


def test_search_hits_are_the_first_ten_as_document_and_page(env):
    rows = env.run(exp("vector_only"))["rows"]
    assert rows[0]["search_hits"][:5] == ["lm35:2", "esp32_datasheet_en:1", "lm35:1", "esp32_datasheet_en:2",
                                          "lm35:3"]
    assert len(rows[0]["search_hits"]) == 10  # 13 chunks are found, only 10 are listed
    assert rows[1]["rerank_hits"][0] == "esp32_datasheet_en:2"
    assert len(rows[1]["rerank_hits"]) == min(10, config.EVAL_RERANK_K)


def test_rank_is_read_from_the_full_search_list_not_the_first_ten(env):
    question = {**QUESTIONS[0], "sources": [("esp32_datasheet_en", 10)]}  # the last of the 13 in vector order
    row = env.one(question, exp("vector_only", rerank=False))
    assert row["search_rank"] == 13


def test_keyword_mode_never_calls_the_embedder(env):
    result = env.run(exp("keyword_only", rerank=False), embed_fn=no_embedding)
    for row in result["rows"]:
        assert row["embed_ms"] is None and row["embed_cached"] is None
    assert result["summary"][0]["embed_ms"] is None
    assert result["rows"][0]["search_rank"] is not None  # "supply voltage range" is found by keyword


def test_a_question_is_embedded_once_and_then_read_from_the_cache(env):
    first = env.run(exp("vector_only", rerank=False))
    assert [row["embed_cached"] for row in first["rows"]] == [False, False, False]
    assert all(row["embed_ms"] is not None for row in first["rows"])
    assert first["summary"][0]["embed_ms"] is not None
    second = env.run(exp("vector_only", rerank=False), embed_fn=no_embedding)
    assert [row["embed_cached"] for row in second["rows"]] == [True, True, True]
    assert second["summary"][0]["embed_ms"] is None  # only questions that were really embedded count
    assert len(env.embedder.texts) == 3


def test_hybrid_and_vector_embed_each_question_once(env):
    env.one(QUESTIONS[0], exp("baseline", rerank=False))
    assert env.embedder.texts == [QUESTIONS[0]["question"]]


def test_search_ms_is_measured(env):
    result = env.run(exp("vector_only", rerank=False))
    assert all(row["search_ms"] is not None and row["search_ms"] > 0 for row in result["rows"])
    assert result["summary"][0]["search_ms"] > 0


def test_rerank_calls_and_waits_are_recorded(env):
    rows = env.run(exp("vector_only"))["rows"]
    assert [row["rerank_calls"] for row in rows] == [1, 1, 1]
    assert len(env.reranker.requests) == 3
    # The first call does not wait; the next ones wait about RERANK_MIN_INTERVAL_S, through the given sleep.
    assert len(env.time.sleeps) == 2
    assert sum(row["rerank_wait_ms"] for row in rows) == pytest.approx(sum(env.time.sleeps) * 1000, rel=1e-6)
    assert rows[0]["rerank_wait_ms"] == pytest.approx(0.0)
    assert all(row["rerank_ms"] >= 0 for row in rows)


def test_reranker_uses_the_eval_k_and_the_n_candidates(env):
    rows = env.run(exp("vector_only", n=6))["rows"]
    assert all(len(request["sources"]) == 6 for request in env.reranker.requests)
    assert len(rows[0]["rerank_hits"]) == min(6, config.EVAL_RERANK_K)


def test_a_second_run_reranks_nothing_and_has_no_rerank_latency(env):
    env.run(exp("vector_only"))
    calls = len(env.reranker.requests)
    result = env.run(exp("vector_only"))
    assert len(env.reranker.requests) == calls
    assert [row["rerank_calls"] for row in result["rows"]] == [0, 0, 0]
    assert by_rerank(result, True)["rerank_ms"] is None
    assert by_rerank(result, True)["recall@1"] == 1.0


# ---------------------------------------------------------------- generation

def test_generation_metrics_go_on_the_reranked_row_when_the_reranker_is_on(env):
    env.llm.answer = "It is in the passage [1]. See also [9]."
    result = env.run(exp("vector_only", generate=True, k=2))
    off, on = by_rerank(result, False), by_rerank(result, True)
    for column in GENERATION_COLUMNS:
        assert off[column] is None, column
    assert on["citation_hit_rate"] == pytest.approx(1.0)  # passage [1] is the correct page after reranking
    assert on["unanswerable_refused"] == pytest.approx(1.0)
    assert on["answerable_refused"] == pytest.approx(0.0)
    assert on["invalid_labels"] == 2  # [9] in each of the two answers, with only 2 passages
    assert on["generate_ms"] is not None
    assert len(env.llm.requests) == 3


def test_generation_metrics_go_on_the_search_row_when_the_reranker_is_off(env):
    result = env.run(exp("vector_only", rerank=False, generate=True, k=1))
    row = result["summary"][0]
    # The single passage is the first search hit, lm35:2: right for q1, wrong for q2.
    assert row["citation_hit_rate"] == pytest.approx(0.5)
    assert row["invalid_labels"] == 0


def test_passages_are_the_first_k_hits(env):
    env.run(exp("vector_only", generate=True, k=2), questions=QUESTIONS[:1])
    user = env.llm.requests[0]["messages"][0]["content"][0]["text"]
    assert "[1] " in user and "[2] " in user and "[3] " not in user


def test_a_generated_row_has_the_answer_fields(env):
    rows = env.run(exp("vector_only", generate=True, k=2))["rows"]
    assert rows[0]["answer"] == env.llm.answer
    assert rows[0]["cited"] == ["lm35:2"]
    assert rows[0]["not_contained"] is False and rows[0]["invalid"] == []
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (1200, 30)
    assert rows[0]["generate_ms"] is not None
    assert rows[2]["not_contained"] is True and rows[2]["cited"] == []


def test_spent_includes_generation(env):
    result = env.run(exp("vector_only", rerank=False, generate=True))
    per_answer = 1200 / 1000 * config.GENERATE_USD_PER_1K_INPUT + 30 / 1000 * config.GENERATE_USD_PER_1K_OUTPUT
    assert result["summary"][0]["spent_usd"] == pytest.approx(3 * per_answer, rel=1e-3)


# ---------------------------------------------------------------- document filter

def test_detect_part_filters_to_the_named_datasheet(env):
    rows = env.run(exp("detect_part", rerank=False))["rows"]
    assert rows[0]["document_filter"] == "lm35"
    assert rows[0]["search_hits"] and all(hit.startswith("lm35:") for hit in rows[0]["search_hits"])
    assert rows[1]["document_filter"] is None


def test_the_config_switch_is_ignored(env, monkeypatch):
    monkeypatch.setattr(config, "DETECT_PART", True)
    row = env.one(QUESTIONS[0], exp("vector_only", rerank=False))
    assert row["document_filter"] is None
    assert any(hit.startswith("esp32_datasheet_en:") for hit in row["search_hits"])


# ---------------------------------------------------------------- HNSW

def hit_keys(hits: list[dict]) -> list[set]:
    return [set(hit) for hit in hits]


def test_hnsw_nearest_has_the_shape_and_order_of_the_exact_search(env):
    conn = env.conn
    exact = db.nearest_chunks(conn, QUERY_VEC, STRATEGY, 3)
    approx = db.hnsw_nearest(conn, QUERY_VEC, STRATEGY, 3)
    assert len(approx) == 3
    assert hit_keys(approx) == hit_keys(exact)
    assert [hit["id"] for hit in approx] == [hit["id"] for hit in exact]
    assert [hit["score"] for hit in approx] == pytest.approx([hit["score"] for hit in exact])
    assert [(hit["document"], hit["page"]) for hit in approx] == [("lm35", 2), ("esp32_datasheet_en", 1),
                                                                  ("lm35", 1)]


def test_hnsw_nearest_document_filter(env):
    hits = db.hnsw_nearest(env.conn, QUERY_VEC, STRATEGY, 2, document="esp32_datasheet_en")
    assert [(hit["document"], hit["page"]) for hit in hits] == [("esp32_datasheet_en", 1),
                                                               ("esp32_datasheet_en", 2)]


def test_hnsw_nearest_leaves_no_index_behind(env):
    assert db.hnsw_index_count(env.conn) == 0
    db.hnsw_nearest(env.conn, QUERY_VEC, STRATEGY, 5)
    assert db.hnsw_index_count(env.conn) == 0


def test_explain_hnsw_uses_an_index_and_leaves_none_behind(env):
    plan = db.explain_hnsw(env.conn, QUERY_VEC, STRATEGY, 5)
    assert "Index Scan" in plan
    assert db.hnsw_index_count(env.conn) == 0


def test_hnsw_experiment_fills_index_recall(env):
    result = env.run(exp("hnsw", rerank=False))
    row = result["summary"][0]
    assert row["hnsw"] is True and row["mode"] == "vector"
    assert row["index_recall"] == pytest.approx(1.0)  # 13 chunks: the index finds every one
    assert [r["search_rank"] for r in result["rows"]] == [1, 4, None]
    assert db.hnsw_index_count(env.conn) == 0


# ---------------------------------------------------------------- plan_run

def mean_chunk_tokens() -> float:
    return sum(approx_tokens(text) for _, _, text, _ in CHUNKS) / len(CHUNKS)


def test_plan_run_returns_the_contract_keys_and_calls_nothing(env):
    plan = run_eval.plan_run(env.conn, QUESTIONS, [exp("baseline")])
    assert set(plan) >= PLAN_KEYS
    assert set(plan["est_cost_usd"]) >= {"embed", "rerank", "generate", "total"}
    assert plan["experiments"] == ["baseline"]
    assert plan["questions"] == 3
    assert costlog.read_rows() == []
    assert env.reranker.requests == [] and env.llm.requests == [] and env.embedder.texts == []


def test_plan_run_counts_with_nothing_cached(env):
    plan = run_eval.plan_run(env.conn, QUESTIONS, [exp("baseline"), exp("vector_only", generate=True)])
    assert plan["experiments"] == ["baseline", "vector_only"]
    assert plan["embed_calls"] == 3  # each question once over both experiments
    assert plan["rerank_calls"] == 6  # no vector cached: one call per question and rerank experiment
    assert plan["generate_calls"] == 3
    cost = plan["est_cost_usd"]
    assert cost["rerank"] == pytest.approx(6 * config.RERANK_USD_PER_QUERY)
    input_tokens = 3 * (5 * mean_chunk_tokens() + 700)
    assert cost["generate"] == pytest.approx(input_tokens / 1000 * config.GENERATE_USD_PER_1K_INPUT
                                             + 3 * 100 / 1000 * config.GENERATE_USD_PER_1K_OUTPUT, rel=1e-3)
    assert cost["embed"] >= 0
    assert cost["total"] == pytest.approx(cost["embed"] + cost["rerank"] + cost["generate"])
    expected_minutes = (6 * config.RERANK_MIN_INTERVAL_S / 60 + 3 / config.EMBED_PER_MIN
                        + 3 / config.GENERATE_PER_MIN)
    assert plan["est_minutes"] == pytest.approx(expected_minutes)
    assert costlog.read_rows() == []


def test_plan_run_without_generation_or_reranker(env):
    plan = run_eval.plan_run(env.conn, QUESTIONS, [exp("vector_only", rerank=False)])
    assert plan["rerank_calls"] == 0 and plan["generate_calls"] == 0
    assert plan["est_cost_usd"]["rerank"] == 0 and plan["est_cost_usd"]["generate"] == 0


def test_plan_run_keyword_only_needs_no_embedding(env):
    plan = run_eval.plan_run(env.conn, QUESTIONS, [exp("keyword_only", rerank=False)])
    assert plan["embed_calls"] == 0
    assert plan["est_cost_usd"]["embed"] == pytest.approx(0.0)


def test_plan_run_after_a_run_counts_only_what_is_not_cached(env):
    env.run(exp("vector_only"))
    rows_before = len(costlog.read_rows())
    plan = run_eval.plan_run(env.conn, QUESTIONS, [exp("vector_only"), exp("vector_only", generate=True)])
    assert plan["embed_calls"] == 0
    assert plan["rerank_calls"] == 0  # vectors cached, so the free search is run and every score is cached
    assert plan["generate_calls"] == 3
    assert len(costlog.read_rows()) == rows_before


def test_plan_run_with_a_new_question_counts_only_that_one(env):
    env.run(exp("vector_only"))
    extra = {**QUESTIONS[0], "id": "q4", "question": "What package does the LM35 come in?"}
    plan = run_eval.plan_run(env.conn, QUESTIONS + [extra], [exp("vector_only")])
    assert plan["embed_calls"] == 1
    assert plan["rerank_calls"] == 1


def test_print_plan_prints_the_counts(env, capsys):
    plan = run_eval.plan_run(env.conn, QUESTIONS, [exp("baseline")])
    run_eval.print_plan(plan)
    out = capsys.readouterr().out
    assert out.strip()
    assert "baseline" in out


def test_plan_run_never_reranks_or_generates(env, monkeypatch):
    monkeypatch.setattr(rerank, "rerank", lambda *a, **k: pytest.fail("plan_run must not rerank"))
    monkeypatch.setattr(generate, "generate", lambda *a, **k: pytest.fail("plan_run must not generate"))
    plan = run_eval.plan_run(env.conn, QUESTIONS, [exp("baseline", generate=True)])
    assert plan["generate_calls"] == 3
