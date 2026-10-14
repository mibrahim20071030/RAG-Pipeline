"""Layer 5 tests for reranking. Bedrock is replaced by a fake, so nothing here is paid."""
import pytest

from rag import config, costlog, db, rerank
from rag.chunk_base import approx_tokens

QUESTION = "What is the maximum current per GPIO pin?"


class FakeReranker:
    """Answers like the Rerank API. A document's score is the share of the question's words it contains."""

    def __init__(self, fail_on_call: int | None = None):
        self.requests = []
        self.fail_on_call = fail_on_call

    def rerank(self, **request):
        self.requests.append(request)
        if len(self.requests) == self.fail_on_call:
            raise RuntimeError("throttled")
        words = set(request["queries"][0]["textQuery"]["text"].lower().split())
        results = []
        for index, source in enumerate(request["sources"]):
            text = source["inlineDocumentSource"]["textDocument"]["text"].lower().split()
            results.append({"index": index, "relevanceScore": len(words & set(text)) / len(words)})
        # Best first, as the real API answers; the index says which source a score belongs to.
        return {"results": sorted(results, key=lambda r: -r["relevanceScore"])}

    def sent(self, call: int = 0) -> list[str]:
        return [s["inlineDocumentSource"]["textDocument"]["text"] for s in self.requests[call]["sources"]]


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def hits(texts: list[str]) -> list[dict]:
    return [
        {"id": index, "document": "doc", "page": index, "page_label": None, "section": None, "kind": "text",
         "text": text, "score": 1 / index}
        for index, text in enumerate(texts, start=1)
    ]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A fake reranker and a fake clock, with the cost log in a temporary folder."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(rerank, "_last_call", None)
    monkeypatch.setattr(rerank, "_bedrock", lambda: pytest.fail("the real client must not be made in a test"))
    fake, clock = FakeReranker(), FakeClock()

    def run(conn, question, candidates, **kwargs):
        return rerank.rerank(conn, question, candidates, client=fake, sleep=clock.sleep, clock=clock.clock, **kwargs)

    run.fake, run.time = fake, clock
    return run


def test_request_has_the_rerank_api_fields(conn, env):
    env(conn, QUESTION, hits(["one", "two"]))
    assert env.fake.requests == [{
        "queries": [{"type": "TEXT", "textQuery": {"text": QUESTION}}],
        "sources": [
            {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": "one"}}},
            {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": "two"}}},
        ],
        "rerankingConfiguration": {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {
                    "modelArn": "arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"},
                "numberOfResults": 2,
            },
        },
    }]


def test_the_chunk_that_answers_the_question_comes_first(conn, env):
    candidates = hits(["total current for all pins", "the maximum current per gpio pin", "storage temperature"])
    result = env(conn, QUESTION, candidates, k=2)
    assert [hit["id"] for hit in result] == [2, 1]
    assert result[0]["score"] > result[1]["score"]


def test_output_has_exactly_k_items_sorted_by_score(conn, env):
    candidates = hits([f"current {'pin ' * (n % 4)}text {n}" for n in range(12)])
    for k in (3, 5, 10):
        result = env(conn, QUESTION, candidates, k=k)
        assert len(result) == k
        assert [hit["score"] for hit in result] == sorted((hit["score"] for hit in result), reverse=True)


def test_k_defaults_to_top_k(conn, env):
    # More candidates than TOP_K, whatever TOP_K is set to.
    assert len(env(conn, QUESTION, hits([f"text {n}" for n in range(config.TOP_K + 4)]))) == config.TOP_K


def test_k_larger_than_the_list_returns_every_hit(conn, env):
    assert len(env(conn, QUESTION, hits(["a", "b", "c"]), k=10)) == 3


def test_equal_scores_keep_the_search_order(conn, env):
    result = env(conn, QUESTION, hits(["alpha", "beta", "gamma"]), k=3)
    assert [hit["id"] for hit in result] == [1, 2, 3]


def test_a_hit_keeps_its_fields_and_its_search_score(conn, env):
    candidates = hits(["the maximum current", "nothing"])
    candidates[0]["vector_rank"] = 4
    before = [dict(hit) for hit in candidates]
    best = env(conn, QUESTION, candidates, k=1)[0]
    assert best == {**before[0], "score": best["score"], "search_score": before[0]["score"]}
    assert best["score"] == pytest.approx(3 / 8)
    assert candidates == before


def test_101_candidates_are_split_into_two_calls(conn, env):
    result = env(conn, QUESTION, hits([f"text {n}" for n in range(101)]), k=5)
    assert [len(request["sources"]) for request in env.fake.requests] == [100, 1]
    assert [r["rerankingConfiguration"]["bedrockRerankingConfiguration"]["numberOfResults"]
            for r in env.fake.requests] == [100, 1]
    assert len(result) == 5


def test_100_candidates_are_one_call(conn, env):
    env(conn, QUESTION, hits([f"text {n}" for n in range(100)]))
    assert len(env.fake.requests) == 1


def test_a_second_run_makes_no_call_and_logs_no_row(conn, env):
    candidates = hits(["the maximum current", "per pin", "nothing"])
    first = env(conn, QUESTION, candidates, k=3)
    assert env(conn, QUESTION, candidates, k=3) == first
    assert env(conn, QUESTION, candidates, k=1) == first[:1]
    assert len(env.fake.requests) == 1
    assert len(costlog.read_rows()) == 1


def test_only_the_texts_not_seen_before_are_sent(conn, env):
    env(conn, QUESTION, hits(["one", "two"]))
    env(conn, QUESTION, hits(["two", "three", "one"]))
    assert env.fake.sent(1) == ["three"]


def test_another_question_does_not_use_the_scores_of_the_first(conn, env):
    env(conn, QUESTION, hits(["one", "two"]))
    env(conn, "What is the supply voltage?", hits(["one", "two"]))
    assert len(env.fake.requests) == 2


def test_the_same_text_twice_is_sent_once_and_both_hits_are_scored(conn, env):
    result = env(conn, QUESTION, hits(["the current", "other", "the current"]), k=3)
    assert env.fake.sent() == ["the current", "other"]
    assert [hit["id"] for hit in result] == [1, 3, 2]
    assert result[0]["score"] == result[1]["score"]


def test_switched_off_returns_the_first_k_and_calls_nothing(env):
    candidates = hits(["a", "b", "c"])
    # No connection is passed: the database is not read either.
    result = env(None, QUESTION, candidates, k=2, enabled=False)
    assert result == [{**hit, "search_score": hit["score"]} for hit in candidates[:2]]
    assert result[0] is not candidates[0]
    assert env.fake.requests == []


def test_the_switch_is_read_from_config_when_not_given(env, monkeypatch):
    monkeypatch.setattr(config, "RERANK_ENABLED", False)
    candidates = hits(["a", "b"])
    assert [hit["id"] for hit in env(None, QUESTION, candidates, k=1)] == [1]
    assert env.fake.requests == []


@pytest.mark.parametrize("question, texts", [(QUESTION, []), ("", ["a"]), (" ?! ", ["a"])])
def test_nothing_to_rank_makes_no_call(env, question, texts):
    assert env(None, question, hits(texts)) == []
    assert env.fake.requests == []


def test_a_long_chunk_is_cut_so_that_question_and_text_fit(conn, env):
    long_text = "word " * 2000
    result = env(conn, QUESTION, hits([long_text, "short"]), k=2)
    sent = env.fake.sent()
    assert approx_tokens(QUESTION) + approx_tokens(sent[0]) == config.RERANK_MAX_TOKENS
    assert long_text.startswith(sent[0])
    assert sent[1] == "short"
    assert {hit["text"] for hit in result} == {long_text, "short"}


def test_rerank_text_leaves_room_for_a_very_long_question():
    assert rerank.rerank_text("q" * 10_000, "abcdefgh") == "abcd"


def test_calls_are_spaced_by_the_minimum_interval(conn, env):
    env(conn, QUESTION, hits(["one"]))
    assert env.time.sleeps == []
    env.time.now += 5
    env(conn, QUESTION, hits(["two"]))
    assert env.time.sleeps == [pytest.approx(config.RERANK_MIN_INTERVAL_S - 5)]
    env.time.now += config.RERANK_MIN_INTERVAL_S
    env(conn, QUESTION, hits(["three"]))
    assert len(env.time.sleeps) == 1


def test_the_two_calls_of_a_split_are_spaced_too(conn, env):
    env(conn, QUESTION, hits([f"text {n}" for n in range(101)]))
    assert env.time.sleeps == [pytest.approx(config.RERANK_MIN_INTERVAL_S)]


def test_each_call_logs_one_query(conn, env):
    env(conn, QUESTION, hits([f"text {n}" for n in range(101)]))
    rows = costlog.read_rows()
    assert [(r["operation"], r["model_id"], r["unit"], r["quantity"]) for r in rows] == [
        ("rerank", config.RERANK_MODEL_ID, "queries", "1")] * 2
    assert float(rows[0]["est_cost_usd"]) == pytest.approx(0.002)


def test_a_failed_call_raises_and_the_scores_before_it_are_kept(conn, env):
    env.fake.fail_on_call = 2
    candidates = hits([f"text {n}" for n in range(101)])
    with pytest.raises(RuntimeError):
        env(conn, QUESTION, candidates)
    assert len(costlog.read_rows()) == 1
    assert rerank.uncached_count(conn, QUESTION, candidates) == 1
    env(conn, QUESTION, candidates)
    assert len(env.fake.sent(2)) == 1


def test_a_page_token_is_followed(conn, env):
    class Paged(FakeReranker):
        def rerank(self, **request):
            results = super().rerank(**request)["results"]
            if "nextToken" not in request:
                return {"results": results[:1], "nextToken": "more"}
            return {"results": results[1:]}

    paged = Paged()
    result = rerank.rerank(conn, QUESTION, hits(["the current", "pin", "x"]), k=3, client=paged,
                           sleep=env.time.sleep, clock=env.time.clock)
    assert len(paged.requests) == 2 and len(result) == 3
    assert len(costlog.read_rows()) == 1


def test_an_answer_with_a_score_missing_raises(conn, env):
    class Short(FakeReranker):
        def rerank(self, **request):
            return {"results": super().rerank(**request)["results"][:1]}

    with pytest.raises(RuntimeError, match="1 of 2"):
        rerank.rerank(conn, QUESTION, hits(["a", "b"]), client=Short(), sleep=env.time.sleep, clock=env.time.clock)


def test_uncached_count_and_calls_needed_match_what_is_sent(conn, env):
    candidates = hits([f"text {n}" for n in range(101)] + ["text 0"])
    assert rerank.uncached_count(conn, QUESTION, candidates) == 101
    assert rerank.calls_needed(conn, QUESTION, candidates) == 2
    env(conn, QUESTION, candidates[:40])
    assert rerank.uncached_count(conn, QUESTION, candidates) == 61
    assert rerank.calls_needed(conn, QUESTION, candidates) == 1
    env(conn, QUESTION, candidates)
    assert rerank.calls_needed(conn, QUESTION, candidates) == 0
    assert rerank.calls_needed(conn, "", candidates) == 0


def test_a_change_of_the_token_limit_resends_only_the_texts_it_cuts(conn, env, monkeypatch):
    candidates = hits(["one", "word " * 2000])
    env(conn, QUESTION, candidates)
    monkeypatch.setattr(config, "RERANK_MAX_TOKENS", 300)
    env(conn, QUESTION, candidates)
    assert len(env.fake.sent(1)) == 1 and env.fake.sent(1)[0].startswith("word")


def test_a_change_of_model_starts_a_new_cache_set(conn, env, monkeypatch):
    env(conn, QUESTION, hits(["one"]))
    monkeypatch.setattr(config, "RERANK_MODEL_ID", "another-model")
    env(conn, QUESTION, hits(["one"]))
    assert len(env.fake.requests) == 2


def test_an_incomplete_answer_is_still_logged(conn, env):
    class Short(FakeReranker):
        def rerank(self, **request):
            return {"results": super().rerank(**request)["results"][:1]}

    with pytest.raises(RuntimeError):
        rerank.rerank(conn, QUESTION, hits(["a", "b"]), client=Short(), sleep=env.time.sleep, clock=env.time.clock)
    assert len(costlog.read_rows()) == 1


def test_the_interval_is_counted_from_the_end_of_a_request(conn, env):
    class Slow(FakeReranker):
        def rerank(self, **request):
            env.time.now += 15  # time spent inside the request, retries included
            return super().rerank(**request)

    slow = Slow()
    for text in ("one", "two"):
        rerank.rerank(conn, QUESTION, hits([text]), client=slow, sleep=env.time.sleep, clock=env.time.clock)
    assert env.time.sleeps == [pytest.approx(config.RERANK_MIN_INTERVAL_S)]


def test_stored_scores_round_trip(conn):
    db.store_rerank_scores(conn, "q", "m", {"a": 0.25, "b": 0.5})
    db.store_rerank_scores(conn, "q", "m", {"a": 0.75})
    assert db.cached_rerank_scores(conn, "q", ["a", "b", "c"], "m") == {"a": 0.75, "b": 0.5}
    assert db.cached_rerank_scores(conn, "other", ["a"], "m") == {}
    assert db.cached_rerank_scores(conn, "q", ["a"], "other") == {}
