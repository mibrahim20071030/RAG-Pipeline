"""Layer 6 tests for generation and citations. Bedrock is replaced by a fake, so nothing here is paid."""
import pytest

from rag import config, costlog, generate

QUESTION = "What is the maximum current per GPIO pin?"


def chunks(n: int = 3) -> list[dict]:
    return [
        {"id": 100 + index, "document": "lm35", "page": index, "page_label": str(index), "section": None,
         "kind": "text", "text": f"passage text {index}", "score": 1 / index}
        for index in range(1, n + 1)
    ]


class FakeLLM:
    """Answers like the Converse API with a fixed reply."""

    def __init__(self, text: str = "It is 25 mA [1].", stop: str = "end_turn"):
        self.text, self.stop, self.requests = text, stop, []

    def converse(self, **request):
        self.requests.append(request)
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
            "stopReason": self.stop,
            "usage": {"inputTokens": 1200, "outputTokens": 30, "totalTokens": 1230},
            "metrics": {"latencyMs": 640},
        }


@pytest.fixture(autouse=True)
def cost_log(monkeypatch, tmp_path):
    """The cost log in a temporary folder, and no real client."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "COST_LOG", tmp_path / "cost_log.csv")
    monkeypatch.setattr(generate, "_bedrock", lambda: pytest.fail("the real client must not be made in a test"))


# build_context and build_prompt

def test_labels_start_at_one_and_follow_the_order():
    context = generate.build_context(chunks(3))
    blocks = context.split("\n\n")
    assert [block.split(" ", 1)[0] for block in blocks] == ["[1]", "[2]", "[3]"]
    assert blocks[1].endswith("passage text 2")


def test_label_names_part_document_and_page():
    hit = {"document": "lm35", "page": 4, "page_label": "4", "text": "x"}
    assert generate.passage_label(hit) == "LM35 (lm35), page 4"


def test_label_adds_printed_page_when_it_differs():
    hit = {"document": "esp32_datasheet_en", "page": 12, "page_label": "10", "text": "x"}
    assert generate.passage_label(hit) == "ESP32 (esp32_datasheet_en), page 12, printed page 10"


def test_label_of_unknown_document_uses_its_name():
    assert generate.passage_label({"document": "other", "page": 1, "page_label": None}) == "other, page 1"


def test_context_keeps_the_full_text():
    long = "v" * 5000
    assert long in generate.build_context([{"document": "lm35", "page": 1, "text": long}])


def test_prompt_has_rules_in_system_and_question_last():
    system, messages = generate.build_prompt(QUESTION, chunks(2))
    assert system == [{"text": generate.system_prompt()}]
    assert system[0]["text"].endswith(config.NOT_CONTAINED)
    assert len(messages) == 1 and messages[0]["role"] == "user"
    user = messages[0]["content"][0]["text"]
    assert user.endswith(f"Question: {QUESTION}")
    assert user.index("[1] ") < user.index("[2] ") < user.index("Question:")


# resolve_citations

def test_label_two_maps_to_the_second_chunk():
    result = generate.resolve_citations("The value is 10 mV/°C [2].", chunks(3))
    assert result["invalid"] == []
    assert result["sources"] == [{"label": 2, "id": 102, "document": "lm35", "page": 2, "page_label": "2"}]


def test_out_of_range_labels_are_invalid():
    result = generate.resolve_citations("Yes [250]. No [0]. Maybe [1]. Again [250].", chunks(3))
    assert result["invalid"] == [0, 250]
    assert [s["label"] for s in result["sources"]] == [1]


def test_repeated_label_gives_one_source():
    result = generate.resolve_citations("A [2]. B [2]. C [2].", chunks(3))
    assert [s["label"] for s in result["sources"]] == [2]


@pytest.mark.parametrize("answer", ["A [1, 3].", "A [1][3].", "A [1] [3].", "A [ 1 ,3 ]."])
def test_several_labels_in_one_place(answer):
    assert [s["label"] for s in generate.resolve_citations(answer, chunks(3))["sources"]] == [1, 3]


def test_sources_are_in_order_of_first_use():
    assert [s["label"] for s in generate.resolve_citations("A [3]. B [1]. C [3].", chunks(3))["sources"]] == [3, 1]


def test_no_labels_gives_no_sources():
    assert generate.resolve_citations("No citation here.", chunks(3)) == {"sources": [], "invalid": []}


def test_footnote_marker_copied_from_a_passage_is_not_a_citation():
    passages = chunks(3)
    passages[0]["text"] = "Pin: GPIO16[2], Type: I/O"
    result = generate.resolve_citations("Pin GPIO16[2] is an input [1].", passages)
    assert [s["label"] for s in result["sources"]] == [1]


def test_label_glued_to_a_unit_is_a_citation():
    assert [s["label"] for s in generate.resolve_citations("It is 25 mA[2].", chunks(3))["sources"]] == [2]


def test_label_after_punctuation_counts():
    assert [s["label"] for s in generate.resolve_citations("It is 25 mA.[2]", chunks(3))["sources"]] == [2]


@pytest.mark.parametrize("answer", ["See [1-3].", "See [1–3].", "See [1 - 3]."])
def test_range_gives_every_label_in_it(answer):
    assert [s["label"] for s in generate.resolve_citations(answer, chunks(3))["sources"]] == [1, 2, 3]


def test_range_past_the_passages_reports_its_end():
    result = generate.resolve_citations("See [2-300].", chunks(3))
    assert [s["label"] for s in result["sources"]] == [2, 3]
    assert result["invalid"] == [300]


def test_other_brackets_are_not_labels():
    assert generate.resolve_citations("Bits [7:0] and [a] and [].", chunks(3)) == {"sources": [], "invalid": []}


# is_not_contained

def test_not_contained_is_found_whatever_the_case_and_spacing():
    assert generate.is_not_contained(config.NOT_CONTAINED)
    assert generate.is_not_contained("  " + config.NOT_CONTAINED.upper().replace(" ", "\n  "))


def test_an_answer_is_not_not_contained():
    assert not generate.is_not_contained("The maximum is 25 mA [1].")


def test_sentence_without_its_full_stop_counts():
    assert generate.is_not_contained(config.NOT_CONTAINED.rstrip("."))


def test_cited_answer_with_the_sentence_is_an_answer():
    answer = f"The scale factor is 10 mV/°C [1]. {config.NOT_CONTAINED}"
    sources = generate.resolve_citations(answer, chunks(3))["sources"]
    assert not generate.is_not_contained(answer, sources)


def test_prompt_follows_a_changed_sentence(monkeypatch):
    monkeypatch.setattr(config, "NOT_CONTAINED", "Not in the datasheets.")
    assert generate.system_prompt().endswith("Not in the datasheets.")
    assert generate.is_not_contained("Not in the datasheets.")


# call_llm

def test_call_sends_the_converse_fields():
    fake = FakeLLM()
    system, messages = generate.build_prompt(QUESTION, chunks(2))
    generate.call_llm(system, messages, client=fake)
    assert fake.requests == [{
        "modelId": config.GENERATE_MODEL_ID,
        "system": system,
        "messages": messages,
        "inferenceConfig": {"maxTokens": config.GENERATE_MAX_TOKENS, "temperature": config.GENERATE_TEMPERATURE},
    }]


def test_call_arguments_override_config():
    fake = FakeLLM()
    generate.call_llm([], [], max_tokens=10, temperature=0.5, client=fake)
    assert fake.requests[0]["inferenceConfig"] == {"maxTokens": 10, "temperature": 0.5}


def test_call_reads_text_and_usage():
    reply = generate.call_llm([], [], client=FakeLLM("  It is 25 mA [1].\n"))
    assert reply == {"text": "It is 25 mA [1].", "input_tokens": 1200, "output_tokens": 30,
                     "stop_reason": "end_turn", "latency_ms": 640}


def test_call_writes_two_cost_rows():
    generate.call_llm([], [], client=FakeLLM())
    rows = costlog.read_rows()
    assert [(r["operation"], r["model_id"], r["unit"], r["quantity"]) for r in rows] == [
        ("generate", config.GENERATE_MODEL_ID, "input_tokens", "1200"),
        ("generate", config.GENERATE_MODEL_ID, "output_tokens", "30"),
    ]
    assert float(rows[0]["est_cost_usd"]) == pytest.approx(1.2 * config.GENERATE_USD_PER_1K_INPUT)
    assert float(rows[1]["est_cost_usd"]) == pytest.approx(0.03 * config.GENERATE_USD_PER_1K_OUTPUT)


def test_cut_off_answer_is_returned():
    assert generate.call_llm([], [], client=FakeLLM(stop="max_tokens"))["stop_reason"] == "max_tokens"


def test_blocked_answer_raises_but_is_logged():
    with pytest.raises(RuntimeError, match="content_filtered"):
        generate.call_llm([], [], client=FakeLLM(stop="content_filtered"))
    assert len(costlog.read_rows()) == 2


def test_cost_log_failure_keeps_the_answer(monkeypatch, capsys):
    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(generate, "log_cost", broken)
    assert generate.call_llm([], [], client=FakeLLM())["text"] == "It is 25 mA [1]."
    assert "cost log not written" in capsys.readouterr().err


# generate

def test_generate_returns_answer_sources_and_flags():
    result = generate.generate(QUESTION, chunks(3), client=FakeLLM("It is 25 mA [1] [9]."))
    assert result["answer"] == "It is 25 mA [1] [9]."
    assert [s["label"] for s in result["sources"]] == [1]
    assert result["invalid"] == [9]
    assert result["not_contained"] is False and result["cut_off"] is False
    assert (result["input_tokens"], result["output_tokens"], result["latency_ms"]) == (1200, 30, 640)


def test_generate_flags_not_contained():
    result = generate.generate(QUESTION, chunks(3), client=FakeLLM(config.NOT_CONTAINED))
    assert result["not_contained"] is True and result["sources"] == []


def test_generate_marks_a_cut_off_answer():
    assert generate.generate(QUESTION, chunks(3), client=FakeLLM("It is [", stop="max_tokens"))["cut_off"] is True


def test_generate_with_no_chunks_calls_nothing():
    fake = FakeLLM()
    result = generate.generate(QUESTION, [], client=fake)
    assert fake.requests == []
    assert result["answer"] == config.NOT_CONTAINED and result["not_contained"] is True
    assert costlog.read_rows() == []
