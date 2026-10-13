"""Layer 3 tests for the embedding calls. Bedrock is replaced by a fake, so nothing here is paid."""
import io
import json

import pytest

from rag import config, costlog, embed


class FakeBedrock:
    """Answers like Titan V2: a vector whose first number is the text length, and one token per word."""

    def __init__(self, fail_on: str | None = None):
        self.bodies = []
        self.fail_on = fail_on

    def invoke_model(self, modelId, body, accept, contentType):
        request = json.loads(body)
        self.bodies.append(request)
        self.model_id = modelId
        if request["inputText"] == self.fail_on:
            raise RuntimeError("throttled")
        answer = {
            "embedding": [float(len(request["inputText"]))] + [0.0] * (request["dimensions"] - 1),
            "inputTextTokenCount": len(request["inputText"].split()),
        }
        return {"body": io.BytesIO(json.dumps(answer).encode())}


@pytest.fixture
def bedrock(monkeypatch, tmp_path):
    fake = FakeBedrock()
    monkeypatch.setattr(embed, "_bedrock", lambda: fake)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    return fake


def test_request_has_the_titan_v2_fields(bedrock):
    vector = embed.embed_text("supply voltage")
    assert bedrock.bodies == [{"inputText": "supply voltage", "dimensions": 1024, "normalize": config.EMBED_NORMALIZE}]
    assert bedrock.model_id == "amazon.titan-embed-text-v2:0"
    assert len(vector) == 1024


def test_embed_text_logs_one_row(bedrock):
    embed.embed_text("one two three")
    rows = costlog.read_rows()
    assert [(r["operation"], r["unit"], r["quantity"]) for r in rows] == [("embed", "input_tokens", "3")]


def test_embed_texts_keeps_the_order_of_the_texts(bedrock):
    texts = ["a" * n for n in range(1, 41)]
    vectors = embed.embed_texts(texts, workers=4)
    assert [vector[0] for vector in vectors] == [float(n) for n in range(1, 41)]
    assert all(len(vector) == 1024 for vector in vectors)


def test_embed_texts_logs_one_row_with_the_token_total(bedrock):
    embed.embed_texts(["one", "two words", "three more words"], label="lm35/page")
    rows = costlog.read_rows()
    assert len(rows) == 1
    assert rows[0]["operation"] == "embed_batch:lm35/page"
    assert rows[0]["model_id"] == config.EMBED_MODEL_ID
    assert int(rows[0]["quantity"]) == 6
    assert float(rows[0]["est_cost_usd"]) == pytest.approx(6 / 1000 * config.EMBED_USD_PER_1K_TOKENS)


def test_embed_texts_with_nothing_makes_no_call_and_no_row(bedrock):
    assert embed.embed_texts([]) == []
    assert bedrock.bodies == []
    assert costlog.read_rows() == []


def test_a_failed_call_raises_and_the_finished_calls_are_still_logged(bedrock):
    bedrock.fail_on = "bad"
    with pytest.raises(RuntimeError):
        embed.embed_texts(["one two", "three four", "bad", "five six"], workers=1)
    rows = costlog.read_rows()
    assert len(rows) == 1
    # One worker: the two calls before the failure finished, the one after it was never started.
    assert int(rows[0]["quantity"]) == 4
    assert [b["inputText"] for b in bedrock.bodies] == ["one two", "three four", "bad"]
