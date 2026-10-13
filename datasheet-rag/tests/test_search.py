"""Layer 4 tests: retrieval. Written from the layer's contract, not from the code.

The pure functions (`rrf`, `query_terms`, `detect_part`) need nothing and run everywhere. The search tests need the
local Postgres and skip themselves without it: they run in a throwaway schema (the `conn` fixture in conftest.py)
on a few hand-made chunks with hand-made vectors. The embedder is always a fake, so no test calls AWS.
"""
import json
import math

import pytest

from rag import config, db, embed, search

STRATEGY = "fixed_400"
OTHER_STRATEGY = "page"
DIMS = config.EMBED_DIMENSIONS
HIT_KEYS = {"id", "document", "page", "page_label", "section", "kind", "text", "score"}
HYBRID_KEYS = HIT_KEYS | {"vector_rank", "keyword_rank"}


# ---------------------------------------------------------------- helpers

def mix(a: float, b: float) -> list[float]:
    """A vector with `a` on axis 0 and `b` on axis 1. With a*a + b*b = 1 its cosine with axis 0 is exactly `a`."""
    return [float(a), float(b)] + [0.0] * (DIMS - 2)


QUERY_VEC = mix(1, 0)


def make_chunk(text: str, page: int = 1, page_label: str | None = None, section: str | None = None,
               kind: str = "text") -> dict:
    return {"page": page, "page_label": page_label if page_label is not None else str(page), "section": section,
            "kind": kind, "text": text}


def add(conn, document: str, strategy: str, items: list) -> None:
    """Store chunks for one document and strategy. An item is a text (vector: axis 0) or a (text, vector) pair.

    The chunk at position i gets page i + 1.
    """
    pairs = [(item, QUERY_VEC) if isinstance(item, str) else item for item in items]
    chunks = [make_chunk(text, page=number) for number, (text, _) in enumerate(pairs, start=1)]
    document_id = db.upsert_document(conn, document)
    db.replace_chunks(conn, document_id, strategy, chunks, [vector for _, vector in pairs])


def texts(hits: list[dict]) -> list[str]:
    return [hit["text"] for hit in hits]


def floats(vector) -> list[float]:
    """The numbers of a vector given as a list or as pgvector text."""
    return [float(x) for x in (json.loads(vector) if isinstance(vector, str) else vector)]


def reference_rrf(ranked_lists: list[list[int]], c: int) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion worked out here, for lists without repeats, to check the code against."""
    scores: dict[int, float] = {}
    for ids in ranked_lists:
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (c + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


class CountingEmbedder:
    """Stands in for embed.embed_text: one fixed vector, and a record of the texts it was asked for."""

    def __init__(self, vector: list[float] | None = None):
        self.vector = vector if vector is not None else QUERY_VEC
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return list(self.vector)


def no_embedding(text):
    raise AssertionError(f"the embedder must not be called (asked for {text!r})")


# ---------------------------------------------------------------- rrf

def test_rrf_known_lists_give_the_known_order_and_scores():
    fused = search.rrf([[1, 2, 3], [3, 2, 4]], c=60)
    assert [chunk_id for chunk_id, _ in fused] == [3, 2, 1, 4]
    assert [score for _, score in fused] == pytest.approx(
        [1 / 63 + 1 / 61, 1 / 62 + 1 / 62, 1 / 61, 1 / 63])


def test_rrf_returns_id_and_score_pairs():
    fused = search.rrf([[10, 20]], c=60)
    assert isinstance(fused, list)
    for pair in fused:
        chunk_id, score = pair
        assert isinstance(chunk_id, int) and isinstance(score, float)


def test_rrf_an_id_in_both_lists_beats_one_at_the_same_rank_in_one_list():
    fused = search.rrf([[5, 9], [5, 8]], c=60)
    assert fused[0] == (5, pytest.approx(2 / 61))
    # Rank 2 in both lists beats rank 1 in only one.
    fused = search.rrf([[1, 7], [2, 7]], c=60)
    assert [chunk_id for chunk_id, _ in fused] == [7, 1, 2]


def test_rrf_equal_scores_come_out_by_id_ascending():
    fused = search.rrf([[3], [1], [2]], c=60)
    assert [chunk_id for chunk_id, _ in fused] == [1, 2, 3]
    assert [score for _, score in fused] == pytest.approx([1 / 61] * 3)
    fused = search.rrf([[9, 4], [4, 9]], c=60)
    assert [chunk_id for chunk_id, _ in fused] == [4, 9]


def test_rrf_a_different_c_changes_the_scores():
    assert [score for _, score in search.rrf([[1, 2]], c=1)] == pytest.approx([1 / 2, 1 / 3])
    assert [score for _, score in search.rrf([[1, 2]], c=0)] == pytest.approx([1.0, 0.5])
    assert search.rrf([[1, 2]], c=1)[0][1] != pytest.approx(search.rrf([[1, 2]], c=60)[0][1])


def test_rrf_default_c_is_the_config_value():
    assert config.RRF_C == 60
    default = search.rrf([[1, 2, 3], [3, 1]])
    explicit = search.rrf([[1, 2, 3], [3, 1]], c=60)
    assert [chunk_id for chunk_id, _ in default] == [chunk_id for chunk_id, _ in explicit]
    assert [score for _, score in default] == pytest.approx([score for _, score in explicit])


def test_rrf_a_repeat_inside_one_list_counts_once_at_its_first_rank():
    fused = dict(search.rrf([[4, 5, 4]], c=60))
    assert fused == {4: pytest.approx(1 / 61), 5: pytest.approx(1 / 62)}
    fused = search.rrf([[4, 4, 4]], c=60)
    assert fused == [(4, pytest.approx(1 / 61))]
    # A repeat in one list must not outweigh an id that two lists agree on.
    fused = search.rrf([[4, 4, 6], [6]], c=60)
    assert [chunk_id for chunk_id, _ in fused] == [6, 4]
    assert dict(fused)[4] == pytest.approx(1 / 61)


def test_rrf_a_single_list_keeps_its_order():
    ids = [42, 7, 19, 3, 100]
    fused = search.rrf([ids], c=60)
    assert [chunk_id for chunk_id, _ in fused] == ids
    assert [score for _, score in fused] == pytest.approx([1 / (60 + rank) for rank in range(1, 6)])


def test_rrf_a_single_id():
    assert search.rrf([[8]], c=60) == [(8, pytest.approx(1 / 61))]


@pytest.mark.parametrize("lists", [[], [[]], [[], []]])
def test_rrf_empty_input_gives_an_empty_list(lists):
    assert search.rrf(lists) == []


def test_rrf_an_empty_list_beside_a_full_one_adds_nothing():
    fused = search.rrf([[], [2, 1]], c=60)
    assert fused == [(2, pytest.approx(1 / 61)), (1, pytest.approx(1 / 62))]


# ---------------------------------------------------------------- query_terms

def test_query_terms_contract_example():
    question = "Which pins does the ESP32-WROOM-32 use for VDD_SDIO (3.3 V)?"
    assert search.query_terms(question) == ["Which", "pins", "does", "the", "ESP32-WROOM-32", "use", "for",
                                            "VDD_SDIO", "3.3", "V"]


@pytest.mark.parametrize("token", ["ESP32-WROOM-32", "3.3", "VDD_SDIO", "±0.5°C", "STM32F205", "1,200", "-40",
                                   "LM35CA", "µA", "0.1%"])
def test_query_terms_keeps_part_numbers_and_values_whole(token):
    assert search.query_terms(token) == [token]
    assert search.query_terms(f"Is it ({token})?") == ["Is", "it", token]


def test_query_terms_keeps_a_value_with_its_symbols():
    assert search.query_terms("Accuracy is ±0.5°C (typical).") == ["Accuracy", "is", "±0.5°C", "typical"]


def test_query_terms_strips_punctuation_from_both_ends_only():
    assert search.query_terms('"quoted" [2] {x} (VDD), end. it\'s') == ["quoted", "2", "x", "VDD", "end", "it's"]
    assert search.query_terms("what?!.. ((nested))") == ["what", "nested"]


def test_query_terms_splits_on_any_whitespace():
    assert search.query_terms("  supply\tvoltage\n range  ") == ["supply", "voltage", "range"]


def test_query_terms_drops_repeats_ignoring_case_and_keeps_the_first_spelling():
    assert search.query_terms("The pin and the PIN and Pin") == ["The", "pin", "and"]
    assert search.query_terms("GPIO gpio (Gpio) gpio.") == ["GPIO"]


def test_query_terms_a_single_word():
    assert search.query_terms("voltage") == ["voltage"]


@pytest.mark.parametrize("question", ["", " ", " \t\n  ", "?", "?!.,;:()[]{}\"'", "? ! ... ( ) [ ] \" '"])
def test_query_terms_nothing_to_search_for_gives_an_empty_list(question):
    assert search.query_terms(question) == []


# ---------------------------------------------------------------- detect_part

@pytest.mark.parametrize("question, document", [
    ("What is the ESP32 supply voltage?", "esp32_datasheet_en"),
    ("Which pins does the ESP32-WROOM-32 use for VDD_SDIO?", "esp32_datasheet_en"),
    ("What is the accuracy of the LM35?", "lm35"),
    ("What is the accuracy of the LM35CA at 25 °C?", "lm35"),
    ("How many timers does the STM32F205 have?", "DS_CD00237391"),
    ("How much flash is in the STM32F207VG?", "DS_CD00237391"),
    ("What is the detection temperature of the TCTH022BE?", "TCTH022BE_datasheet_en_20230425"),
    ("Is the (LM35) a sensor?", "lm35"),
])
def test_detect_part_one_part_named(question, document):
    assert search.detect_part(question) == document


@pytest.mark.parametrize("question", [
    "What is the maximum supply voltage?",
    "",
    "   ",
    "How many timers does the STM32F103 have?",  # another family: the prefix is stm32f20
    "What does the XLM35 do?",  # the word must start with the prefix
])
def test_detect_part_none_named(question):
    assert search.detect_part(question) is None


def test_detect_part_two_datasheets_named_gives_none():
    assert search.detect_part("Can the ESP32 read the LM35 output?") is None
    assert search.detect_part("Compare the STM32F205, the TCTH022BE and the ESP32.") is None


def test_detect_part_the_same_datasheet_named_twice_still_returns_it():
    assert search.detect_part("Does the LM35 differ from the LM35CA?") == "lm35"
    assert search.detect_part("STM32F205 versus STM32F207") == "DS_CD00237391"
    assert search.detect_part("ESP32 and esp32") == "esp32_datasheet_en"


@pytest.mark.parametrize("question, document", [
    ("esp32 deep sleep current", "esp32_datasheet_en"),
    ("Esp32 deep sleep current", "esp32_datasheet_en"),
    ("what is the lm35ca accuracy", "lm35"),
    ("Lm35Ca accuracy", "lm35"),
    ("stm32f205 timers", "DS_CD00237391"),
    ("tcth022be threshold", "TCTH022BE_datasheet_en_20230425"),
])
def test_detect_part_case_does_not_matter(question, document):
    assert search.detect_part(question) == document


def test_detect_part_reads_the_aliases_at_call_time(monkeypatch):
    assert search.detect_part("What is the BME280 pressure range?") is None
    monkeypatch.setattr(config, "PART_ALIASES", {"bme280_doc": ("bme280",), "two_names": ("abc", "xyz")})
    assert search.detect_part("What is the BME280 pressure range?") == "bme280_doc"
    assert search.detect_part("What is the ESP32 supply voltage?") is None
    # Two prefixes of one document name one document, not two.
    assert search.detect_part("abc123 or XYZ9?") == "two_names"
    assert search.detect_part("abc123 or BME280?") is None


# ---------------------------------------------------------------- vector search

NEAR, CLOSE, MID, FAR, OPPOSITE = "near", "close", "mid", "far", "opposite"
ELSEWHERE = "in another document"
VECTOR_ORDER = [NEAR, ELSEWHERE, CLOSE, MID, FAR, OPPOSITE]
VECTOR_SCORES = [1.0, 0.96, 0.8, 0.6, 0.0, -1.0]


@pytest.fixture
def vector_rows(conn):
    """Six chunks of STRATEGY in two documents with known cosine similarity to QUERY_VEC, stored out of order."""
    add(conn, "vdoc", STRATEGY, [(MID, mix(0.6, 0.8)), (OPPOSITE, mix(-1, 0)), (NEAR, mix(1, 0)),
                                 (FAR, mix(0, 1)), (CLOSE, mix(0.8, 0.6))])
    add(conn, "vother", STRATEGY, [(ELSEWHERE, mix(0.96, 0.28))])
    # The same vector as the query, under another strategy: it must never be returned.
    add(conn, "vdoc", OTHER_STRATEGY, [("another strategy", mix(1, 0))])
    return conn


def test_vector_nearest_first_and_the_order_follows_similarity(vector_rows):
    hits = db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 10)
    assert texts(hits) == VECTOR_ORDER


def test_vector_score_is_one_minus_cosine_distance(vector_rows):
    hits = db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 10)
    assert [hit["score"] for hit in hits] == pytest.approx(VECTOR_SCORES, abs=1e-5)
    assert all(isinstance(hit["score"], float) for hit in hits)


def test_vector_score_does_not_depend_on_the_length_of_the_question_vector(vector_rows):
    hits = db.nearest_chunks(vector_rows, mix(2.5, 0), STRATEGY, 10)
    assert texts(hits) == VECTOR_ORDER
    assert [hit["score"] for hit in hits] == pytest.approx(VECTOR_SCORES, abs=1e-5)


@pytest.mark.parametrize("n, expected", [(1, 1), (3, 3), (6, 6), (7, 6), (50, 6)])
def test_vector_returns_n_rows_or_all_there_are(vector_rows, n, expected):
    hits = db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, n)
    assert texts(hits) == VECTOR_ORDER[:expected]


def test_vector_rows_of_another_strategy_never_appear(vector_rows):
    assert "another strategy" not in texts(db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 50))
    assert texts(db.nearest_chunks(vector_rows, QUERY_VEC, OTHER_STRATEGY, 50)) == ["another strategy"]
    assert db.nearest_chunks(vector_rows, QUERY_VEC, "fixed_256", 50) == []


def test_vector_document_filter(vector_rows):
    only = db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 50, document="vdoc")
    assert texts(only) == [NEAR, CLOSE, MID, FAR, OPPOSITE]
    assert {hit["document"] for hit in only} == {"vdoc"}
    assert texts(db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 50, document="vother")) == [ELSEWHERE]
    assert texts(db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 2, document="vdoc")) == [NEAR, CLOSE]
    assert db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 50, document="no_such_document") == []
    everything = db.nearest_chunks(vector_rows, QUERY_VEC, STRATEGY, 50, document=None)
    assert {hit["document"] for hit in everything} == {"vdoc", "vother"}


def test_vector_as_a_list_and_as_pgvector_text_give_the_same_result(vector_rows):
    question = mix(0.6, 0.8)
    from_list = db.nearest_chunks(vector_rows, question, STRATEGY, 10)
    from_text = db.nearest_chunks(vector_rows, db.vector_text(question), STRATEGY, 10)
    assert from_list == from_text
    assert from_list[0]["text"] == MID


def test_vector_hits_have_exactly_the_contract_keys_and_the_stored_values(conn):
    document_id = db.upsert_document(conn, "keys_doc")
    chunk = make_chunk("Pin 7 is GPIO16.", page=12, page_label="iv", section="5.1 Pins", kind="table")
    db.replace_chunks(conn, document_id, STRATEGY, [make_chunk("first", page=3), chunk], [mix(0, 1), mix(1, 0)])
    hits = db.nearest_chunks(conn, QUERY_VEC, STRATEGY, 5)
    assert all(set(hit) == HIT_KEYS for hit in hits)
    best = hits[0]
    assert isinstance(best["id"], int)
    assert len({hit["id"] for hit in hits}) == 2
    assert {key: best[key] for key in HIT_KEYS - {"id", "score"}} == {
        "document": "keys_doc", "page": 12, "page_label": "iv", "section": "5.1 Pins", "kind": "table",
        "text": "Pin 7 is GPIO16."}
    assert hits[1]["section"] is None and hits[1]["page"] == 3


def test_vector_search_on_an_empty_table_gives_an_empty_list(conn):
    assert db.nearest_chunks(conn, QUERY_VEC, STRATEGY, 5) == []
    assert search.vector_search(conn, QUERY_VEC, n=5, strategy=STRATEGY) == []


def test_search_vector_search_returns_the_same_as_db_nearest_chunks(vector_rows):
    question = mix(0.8, 0.6)
    assert (search.vector_search(vector_rows, question, n=4, strategy=STRATEGY)
            == db.nearest_chunks(vector_rows, question, STRATEGY, 4))
    assert (search.vector_search(vector_rows, db.vector_text(question), n=4, strategy=STRATEGY)
            == db.nearest_chunks(vector_rows, question, STRATEGY, 4))
    assert (search.vector_search(vector_rows, question, n=10, strategy=STRATEGY, document="vother")
            == db.nearest_chunks(vector_rows, question, STRATEGY, 10, document="vother"))
    assert (search.vector_search(vector_rows, question, n=10, strategy=OTHER_STRATEGY)
            == db.nearest_chunks(vector_rows, question, OTHER_STRATEGY, 10))


def test_explain_nearest_sorts_by_the_cosine_distance_operator(vector_rows):
    plan = db.explain_nearest(vector_rows, QUERY_VEC, STRATEGY, 5)
    assert isinstance(plan, str)
    assert "<=>" in plan
    ordering = [line for line in plan.splitlines() if "Sort Key" in line or "Order By" in line]
    assert ordering, plan[:500]
    assert any("<=>" in line for line in ordering)


# ---------------------------------------------------------------- keyword search

# Only this chunk names the part, once, and it has neither of the common words.
PART_CHUNK = ("Ordering information. The TCTH022BE is shipped in tape and reel packaging of 3000 pieces. The package "
              "marking shows the lot code and the week of manufacture on the top side.")
REPEATERS = [
    f"Supply voltage of the {rail} rail. The supply voltage must be stable. A supply voltage below the minimum "
    f"supply voltage stops the device. Raise the supply voltage slowly, because the supply voltage ramp is limited. "
    f"Supply voltage noise couples into the output, so filter the supply voltage."
    for rail in ("analog", "digital", "backup", "radio")
]
MENTIONS = [
    "The output is valid once the supply voltage has settled.",
    "Detection accuracy does not depend on the supply voltage.",
    "Current consumption rises with the supply voltage.",
    "The supply voltage is applied to pin 5.",
    "Absolute maximum ratings: the supply voltage must stay below 6 V.",
]
FILLERS = [
    "The package is a small outline type with five leads.",
    "Operating temperature is from -40 to 125 degrees.",
]


@pytest.fixture
def part_rows(conn):
    """Twelve chunks: one names the part, nine have the common words (four of them many times), two have neither."""
    items = REPEATERS[:2] + MENTIONS[:3] + [PART_CHUNK] + REPEATERS[2:] + MENTIONS[3:] + FILLERS
    assert len(items) == 12
    add(conn, "tcth", STRATEGY, items)
    return conn


@pytest.mark.parametrize("ts_config", ["english", "simple"])
@pytest.mark.parametrize("question", ["TCTH022BE supply voltage", "What is the supply voltage of the TCTH022BE?"])
def test_keyword_exact_part_number_chunk_is_in_the_top_3(part_rows, ts_config, question):
    hits = search.keyword_search(part_rows, question, n=10, strategy=STRATEGY, ts_config=ts_config)
    assert len(hits) == 10  # the part chunk and the nine chunks with the common words
    assert PART_CHUNK in texts(hits)[:3]


def test_keyword_part_number_chunk_is_in_the_top_3_when_n_is_3(part_rows):
    hits = search.keyword_search(part_rows, "TCTH022BE supply voltage", n=3, strategy=STRATEGY, ts_config="english")
    assert len(hits) == 3
    assert PART_CHUNK in texts(hits)


def test_keyword_a_rare_term_weighs_more_than_a_common_one(part_rows):
    rare = db.keyword_chunks(part_rows, ["TCTH022BE"], STRATEGY, 10)
    common = db.keyword_chunks(part_rows, ["voltage"], STRATEGY, 10)
    assert texts(rare) == [PART_CHUNK]
    assert len(common) == 9
    # One mention of the rare word outweighs eight mentions of the common one.
    assert rare[0]["score"] > common[0]["score"]


PIN_CHUNK = "Each GPIO pin can source at most 40 mA."
CURRENT_CHUNK = "The quiescent current of the sensor is 60 µA at room temperature."
SUPPLY_CHUNK = "The supply voltage range is 4 V to 30 V."
STORAGE_CHUNK = "Storage temperature is from -65 °C to 150 °C."
WATCHDOG_CHUNK = "The watchdog timer resets the chip."
KEYWORD_CHUNKS = [PIN_CHUNK, CURRENT_CHUNK, SUPPLY_CHUNK, STORAGE_CHUNK, WATCHDOG_CHUNK]
ZENER_CHUNK = "A zener diode clamps the input."
BANDGAP_CHUNK = "The bandgap reference needs no supply decoupling."


@pytest.fixture
def keyword_rows(conn):
    """Five chunks of STRATEGY in `kdoc`, one in `kother`, and one chunk of another strategy in `kdoc`."""
    add(conn, "kdoc", STRATEGY, KEYWORD_CHUNKS)
    add(conn, "kother", STRATEGY, [BANDGAP_CHUNK])
    add(conn, "kdoc", OTHER_STRATEGY, [ZENER_CHUNK])
    return conn


def keyword(conn, question, n=10, ts_config="english", strategy=STRATEGY, document=None):
    return search.keyword_search(conn, question, n=n, strategy=strategy, ts_config=ts_config, document=document)


def test_keyword_a_long_natural_question_returns_rows(keyword_rows):
    question = "How much current does the temperature sensor draw from the supply when it is running at room temperature?"
    # No chunk has every word: an all-words query would return nothing.
    for text in KEYWORD_CHUNKS + [BANDGAP_CHUNK]:
        assert "draw" not in text and "running" not in text
    hits = keyword(keyword_rows, question)
    assert len(hits) >= 1
    assert hits[0]["text"] == CURRENT_CHUNK  # it matches four of the words, three of them found nowhere else
    assert set(texts(hits)) == {CURRENT_CHUNK, SUPPLY_CHUNK, STORAGE_CHUNK, BANDGAP_CHUNK}


@pytest.mark.parametrize("question", ["what is the of", "", "   ", "?!", "The of a?"])
def test_keyword_stop_words_only_or_nothing_gives_an_empty_list(keyword_rows, question):
    assert keyword(keyword_rows, question, ts_config="english") == []


def test_keyword_chunks_with_no_terms_or_only_stop_word_terms_gives_an_empty_list(keyword_rows):
    assert db.keyword_chunks(keyword_rows, [], STRATEGY, 10) == []
    assert db.keyword_chunks(keyword_rows, [], STRATEGY, 10, ts_config="simple") == []
    assert db.keyword_chunks(keyword_rows, ["the", "of", "is"], STRATEGY, 10, ts_config="english") == []


def test_keyword_a_stop_word_term_is_dropped_and_the_others_still_match(keyword_rows):
    with_stop_words = db.keyword_chunks(keyword_rows, ["the", "watchdog", "of"], STRATEGY, 10)
    alone = db.keyword_chunks(keyword_rows, ["watchdog"], STRATEGY, 10)
    assert texts(with_stop_words) == [WATCHDOG_CHUNK]
    assert with_stop_words[0]["score"] == pytest.approx(alone[0]["score"], rel=1e-5)


def test_keyword_english_stems_and_simple_does_not(keyword_rows):
    assert all("pins" not in text.lower() for text in KEYWORD_CHUNKS + [BANDGAP_CHUNK])
    assert texts(keyword(keyword_rows, "pins", ts_config="english")) == [PIN_CHUNK]
    assert keyword(keyword_rows, "pins", ts_config="simple") == []
    # The word as written is found by both.
    assert texts(keyword(keyword_rows, "pin", ts_config="simple")) == [PIN_CHUNK]
    assert texts(keyword(keyword_rows, "pin", ts_config="english")) == [PIN_CHUNK]


def test_keyword_terms_that_normalise_to_the_same_word_count_once(keyword_rows):
    once = db.keyword_chunks(keyword_rows, ["pin"], STRATEGY, 10, ts_config="english")
    repeated = db.keyword_chunks(keyword_rows, ["pin", "pins", "PIN", "Pins"], STRATEGY, 10, ts_config="english")
    assert texts(once) == texts(repeated) == [PIN_CHUNK]
    assert repeated[0]["score"] == pytest.approx(once[0]["score"], rel=1e-5)


def test_keyword_matching_ignores_case(keyword_rows):
    for ts_config in config.KEYWORD_CONFIGS:
        assert texts(keyword(keyword_rows, "WATCHDOG", ts_config=ts_config)) == [WATCHDOG_CHUNK]
        assert texts(keyword(keyword_rows, "gpio", ts_config=ts_config)) == [PIN_CHUNK]


@pytest.mark.parametrize("ts_config", ["english", "simple"])
def test_keyword_awkward_characters_and_sql_do_not_raise_or_change_the_rows(keyword_rows, ts_config):
    question = "What's the \"watchdog\" C:\\temp 100% a & b | c :* !x <-> (y) '); DROP TABLE chunks; -- %s %(name)s"
    before = db.strategy_counts(keyword_rows)
    hits = keyword(keyword_rows, question, ts_config=ts_config)
    assert isinstance(hits, list)
    assert WATCHDOG_CHUNK in texts(hits)
    assert db.strategy_counts(keyword_rows) == before
    assert db.count_chunks(keyword_rows, STRATEGY) == 6
    # The table still answers afterwards.
    assert texts(keyword(keyword_rows, "watchdog", ts_config=ts_config)) == [WATCHDOG_CHUNK]


@pytest.mark.parametrize("term", ["'", '"', "\\", "%", "&", "|", ":*", "!", "'); DROP TABLE chunks; --", "a & | b",
                                  "%s", "watchdog:*", "\x27\x27"])
def test_keyword_chunks_takes_any_term_as_plain_text(keyword_rows, term):
    hits = db.keyword_chunks(keyword_rows, [term, "watchdog"], STRATEGY, 10)
    assert WATCHDOG_CHUNK in texts(hits)
    assert isinstance(db.keyword_chunks(keyword_rows, [term], STRATEGY, 10, ts_config="simple"), list)
    assert db.count_chunks(keyword_rows) == 7


@pytest.mark.parametrize("ts_config", ["german", "", "English", "tsv_english", "english'; DROP TABLE chunks; --"])
def test_keyword_a_bad_ts_config_raises_value_error(keyword_rows, ts_config):
    with pytest.raises(ValueError):
        db.keyword_chunks(keyword_rows, ["watchdog"], STRATEGY, 10, ts_config=ts_config)
    with pytest.raises(ValueError):
        keyword(keyword_rows, "watchdog", ts_config=ts_config)
    assert db.count_chunks(keyword_rows) == 7


def test_keyword_strategy_filter(keyword_rows):
    assert keyword(keyword_rows, "zener diode") == []
    assert texts(keyword(keyword_rows, "zener diode", strategy=OTHER_STRATEGY)) == [ZENER_CHUNK]
    assert keyword(keyword_rows, "watchdog", strategy=OTHER_STRATEGY) == []
    assert keyword(keyword_rows, "watchdog", strategy="fixed_256") == []


def test_keyword_document_filter(keyword_rows):
    everywhere = keyword(keyword_rows, "supply")
    assert set(texts(everywhere)) == {SUPPLY_CHUNK, BANDGAP_CHUNK}
    assert {hit["document"] for hit in everywhere} == {"kdoc", "kother"}
    assert texts(keyword(keyword_rows, "supply", document="kdoc")) == [SUPPLY_CHUNK]
    assert texts(keyword(keyword_rows, "supply", document="kother")) == [BANDGAP_CHUNK]
    assert keyword(keyword_rows, "watchdog", document="kother") == []
    assert keyword(keyword_rows, "supply", document="no_such_document") == []
    assert texts(db.keyword_chunks(keyword_rows, ["supply"], STRATEGY, 10, document="kother")) == [BANDGAP_CHUNK]


@pytest.mark.parametrize("n, expected", [(1, 1), (3, 3), (9, 9), (10, 10), (50, 10)])
def test_keyword_returns_at_most_n_rows(part_rows, n, expected):
    hits = keyword(part_rows, "TCTH022BE supply voltage", n=n)
    assert len(hits) == expected
    assert texts(hits) == texts(keyword(part_rows, "TCTH022BE supply voltage", n=50))[:expected]


def test_keyword_hits_have_exactly_the_contract_keys_and_the_stored_values(conn):
    document_id = db.upsert_document(conn, "keys_doc")
    chunk = make_chunk("Pin 7 is GPIO16.", page=12, page_label="iv", section="5.1 Pins", kind="table")
    db.replace_chunks(conn, document_id, STRATEGY, [make_chunk("nothing here", page=3), chunk],
                      [QUERY_VEC, QUERY_VEC])
    for ts_config in config.KEYWORD_CONFIGS:
        hits = keyword(conn, "GPIO16", ts_config=ts_config)
        assert len(hits) == 1
        hit = hits[0]
        assert set(hit) == HIT_KEYS
        assert isinstance(hit["id"], int) and isinstance(hit["score"], float) and hit["score"] > 0
        assert {key: hit[key] for key in HIT_KEYS - {"id", "score"}} == {
            "document": "keys_doc", "page": 12, "page_label": "iv", "section": "5.1 Pins", "kind": "table",
            "text": "Pin 7 is GPIO16."}


@pytest.mark.parametrize("ts_config", ["english", "simple"])
def test_keyword_words_that_match_nothing_give_an_empty_list(keyword_rows, ts_config):
    assert keyword(keyword_rows, "zebra xylophone quux", ts_config=ts_config) == []
    assert db.keyword_chunks(keyword_rows, ["zebra", "xylophone"], STRATEGY, 10, ts_config=ts_config) == []


def test_keyword_search_on_an_empty_table_gives_an_empty_list(conn):
    assert keyword(conn, "supply voltage") == []


def test_keyword_rows_are_ordered_by_score_then_id(part_rows):
    hits = keyword(part_rows, "TCTH022BE supply voltage", n=50)
    assert all(hit["score"] > 0 for hit in hits)
    keys = [(-hit["score"], hit["id"]) for hit in hits]
    assert keys == sorted(keys)


def test_keyword_equal_scores_come_out_by_id(conn):
    same = "The brownout detector trips at 2.4 V."
    add(conn, "ties", STRATEGY, [same, "Unrelated filler text.", same, same])
    hits = keyword(conn, "brownout detector")
    assert len(hits) == 3
    assert [hit["score"] for hit in hits] == pytest.approx([hits[0]["score"]] * 3)
    assert [hit["id"] for hit in hits] == sorted(hit["id"] for hit in hits)
    assert [hit["page"] for hit in hits] == [1, 3, 4]


def test_keyword_search_is_query_terms_then_keyword_chunks(keyword_rows):
    question = "What is the supply voltage range (in V) of the sensor?"
    for ts_config in config.KEYWORD_CONFIGS:
        direct = db.keyword_chunks(keyword_rows, search.query_terms(question), STRATEGY, 10, ts_config=ts_config)
        assert keyword(keyword_rows, question, ts_config=ts_config) == direct
        assert direct


VOLTAGE_ONCE = "The reference voltage is trimmed at the factory."


@pytest.fixture
def weight_rows(conn):
    """`wa`: four chunks, one with "voltage". `wb`: four chunks, all with "voltage"."""
    add(conn, "wa", STRATEGY, [VOLTAGE_ONCE, "The package has five leads.", "Soldering profile follows the standard.",
                               "Tape and reel of 3000 pieces."])
    add(conn, "wb", STRATEGY, [f"Rail {number} voltage is monitored." for number in range(1, 5)])
    return conn


def score_of(hits: list[dict], text: str) -> float:
    return next(hit["score"] for hit in hits if hit["text"] == text)


def test_keyword_term_weight_follows_how_many_searched_chunks_have_the_term(weight_rows):
    # Same chunk, same term: only the weight ln(1 + (N - df + 0.5) / (df + 0.5)) differs between the two searches.
    in_document = score_of(db.keyword_chunks(weight_rows, ["voltage"], STRATEGY, 10, document="wa"), VOLTAGE_ONCE)
    in_everything = score_of(db.keyword_chunks(weight_rows, ["voltage"], STRATEGY, 10), VOLTAGE_ONCE)
    weight_in_document = math.log(1 + (4 - 1 + 0.5) / (1 + 0.5))  # N = 4, df = 1
    weight_in_everything = math.log(1 + (8 - 5 + 0.5) / (5 + 0.5))  # N = 8, df = 5
    assert in_document > in_everything > 0
    assert in_document / in_everything == pytest.approx(weight_in_document / weight_in_everything, rel=1e-3)


def test_keyword_chunks_of_another_strategy_do_not_count_in_the_term_weight(weight_rows):
    before = score_of(db.keyword_chunks(weight_rows, ["voltage"], STRATEGY, 10, document="wa"), VOLTAGE_ONCE)
    add(weight_rows, "wa", OTHER_STRATEGY, ["Voltage here.", "Voltage there.", "Voltage everywhere."])
    after = score_of(db.keyword_chunks(weight_rows, ["voltage"], STRATEGY, 10, document="wa"), VOLTAGE_ONCE)
    assert after == pytest.approx(before, rel=1e-5)


def test_keyword_score_is_the_sum_over_the_terms_a_chunk_matches(weight_rows):
    def score(terms):
        return score_of(db.keyword_chunks(weight_rows, terms, STRATEGY, 10), VOLTAGE_ONCE)

    assert score(["voltage", "reference"]) == pytest.approx(score(["voltage"]) + score(["reference"]), rel=1e-3)
    # A term the chunk does not have adds nothing to it.
    assert score(["voltage", "monitored"]) == pytest.approx(score(["voltage"]), rel=1e-5)


# ---------------------------------------------------------------- hybrid search

V_FIRST = "Deep sleep draws very little current."
BOTH = "The watchdog timer resets the chip after a timeout if the software stops responding."
V_THIRD = "Flash memory holds the program."
K_STRONG = "Watchdog watchdog watchdog."
K_WEAK = ("Several independent peripherals can wake the processor from its low power state, among them the real time "
          "clock, the touch sensor, an external interrupt line and, when it has been enabled, the watchdog.")
NEITHER = "The antenna needs a matching network."


@pytest.fixture
def hybrid_rows(conn):
    """Six chunks. Nearest three to QUERY_VEC: V_FIRST, BOTH, V_THIRD. With "watchdog": BOTH, K_STRONG, K_WEAK."""
    add(conn, "hdoc", STRATEGY, [(V_FIRST, mix(1, 0)), (BOTH, mix(0.8, 0.6)), (V_THIRD, mix(0.6, 0.8)),
                                 (K_STRONG, mix(0.28, 0.96)), (K_WEAK, mix(0, 1)), (NEITHER, mix(-0.6, 0.8))])
    return conn


def hybrid(conn, question="watchdog", n=3, document=None, c=60):
    return search.hybrid_search(conn, question, QUERY_VEC, n=n, strategy=STRATEGY, ts_config="english",
                                document=document, c=c)


def two_lists(conn, question="watchdog", n=3, document=None):
    vector_hits = search.vector_search(conn, QUERY_VEC, n=n, strategy=STRATEGY, document=document)
    keyword_hits = search.keyword_search(conn, question, n=n, strategy=STRATEGY, ts_config="english",
                                         document=document)
    return vector_hits, keyword_hits


def test_hybrid_a_chunk_in_both_lists_ranks_above_chunks_in_one(hybrid_rows):
    vector_hits, keyword_hits = two_lists(hybrid_rows)
    assert texts(vector_hits) == [V_FIRST, BOTH, V_THIRD]
    assert set(texts(keyword_hits)) == {BOTH, K_STRONG, K_WEAK}
    hits = hybrid(hybrid_rows)
    assert hits[0]["text"] == BOTH
    assert hits[0]["vector_rank"] == 2 and hits[0]["keyword_rank"] in (1, 2, 3)
    assert all(hit["score"] < hits[0]["score"] for hit in hits[1:])
    assert all(hit["vector_rank"] is None or hit["keyword_rank"] is None for hit in hits[1:])


def test_hybrid_order_scores_and_ranks_match_rrf_of_the_two_lists(hybrid_rows):
    vector_hits, keyword_hits = two_lists(hybrid_rows)
    vector_ids = [hit["id"] for hit in vector_hits]
    keyword_ids = [hit["id"] for hit in keyword_hits]
    expected = reference_rrf([vector_ids, keyword_ids], 60)[:3]
    by_id = {hit["id"]: hit for hit in vector_hits + keyword_hits}
    hits = hybrid(hybrid_rows)
    assert [hit["id"] for hit in hits] == [chunk_id for chunk_id, _ in expected]
    assert [hit["score"] for hit in hits] == pytest.approx([score for _, score in expected])
    for hit in hits:
        assert set(hit) == HYBRID_KEYS
        assert isinstance(hit["score"], float)
        assert hit["vector_rank"] == (vector_ids.index(hit["id"]) + 1 if hit["id"] in vector_ids else None)
        assert hit["keyword_rank"] == (keyword_ids.index(hit["id"]) + 1 if hit["id"] in keyword_ids else None)
        for key in HIT_KEYS - {"score"}:
            assert hit[key] == by_id[hit["id"]][key]


def test_hybrid_rank_is_none_for_the_list_a_chunk_is_not_in(hybrid_rows):
    hits = {hit["text"]: hit for hit in hybrid(hybrid_rows, n=6)}
    # With n = 6 every chunk is in the vector list; only the three with the word are in the keyword list.
    assert set(hits) == {V_FIRST, BOTH, V_THIRD, K_STRONG, K_WEAK, NEITHER}
    assert [hits[text]["vector_rank"] for text in (V_FIRST, BOTH, V_THIRD, K_STRONG, K_WEAK, NEITHER)] == [
        1, 2, 3, 4, 5, 6]
    assert sorted(hits[text]["keyword_rank"] for text in (BOTH, K_STRONG, K_WEAK)) == [1, 2, 3]
    assert [hits[text]["keyword_rank"] for text in (V_FIRST, V_THIRD, NEITHER)] == [None, None, None]
    assert hits[NEITHER]["score"] == pytest.approx(1 / 66)
    # With n = 3 the two keyword-only chunks are not in the vector list.
    short = {hit["text"]: hit for hit in hybrid(hybrid_rows, n=3)}
    for text in (K_STRONG, K_WEAK):
        if text in short:
            assert short[text]["vector_rank"] is None and short[text]["keyword_rank"] in (1, 2, 3)
    assert short[V_FIRST]["vector_rank"] == 1 and short[V_FIRST]["keyword_rank"] is None
    assert short[V_FIRST]["score"] == pytest.approx(1 / 61)


@pytest.mark.parametrize("n, expected", [(1, 1), (2, 2), (3, 3), (6, 6), (50, 6)])
def test_hybrid_returns_at_most_n_rows(hybrid_rows, n, expected):
    hits = hybrid(hybrid_rows, n=n)
    assert len(hits) == expected
    assert len({hit["id"] for hit in hits}) == expected
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_c_changes_the_score(hybrid_rows):
    vector_hits, keyword_hits = two_lists(hybrid_rows)
    expected = reference_rrf([[hit["id"] for hit in vector_hits], [hit["id"] for hit in keyword_hits]], 10)[:3]
    hits = hybrid(hybrid_rows, c=10)
    assert [hit["id"] for hit in hits] == [chunk_id for chunk_id, _ in expected]
    assert [hit["score"] for hit in hits] == pytest.approx([score for _, score in expected])
    assert hits[0]["score"] > hybrid(hybrid_rows, c=60)[0]["score"]


def test_hybrid_with_no_keyword_match_follows_the_vector_order(hybrid_rows):
    hits = hybrid(hybrid_rows, question="zebra xylophone", n=3)
    assert texts(hits) == [V_FIRST, BOTH, V_THIRD]
    assert [hit["vector_rank"] for hit in hits] == [1, 2, 3]
    assert [hit["keyword_rank"] for hit in hits] == [None, None, None]
    assert [hit["score"] for hit in hits] == pytest.approx([1 / 61, 1 / 62, 1 / 63])


def test_hybrid_document_and_strategy_filters(hybrid_rows):
    add(hybrid_rows, "hother", STRATEGY, [("The watchdog of another document.", mix(1, 0))])
    add(hybrid_rows, "hdoc", OTHER_STRATEGY, [("The watchdog of another strategy.", mix(1, 0))])
    assert {hit["document"] for hit in hybrid(hybrid_rows, n=50)} == {"hdoc", "hother"}
    assert "The watchdog of another strategy." not in texts(hybrid(hybrid_rows, n=50))
    only = hybrid(hybrid_rows, n=50, document="hother")
    assert texts(only) == ["The watchdog of another document."]
    assert (only[0]["vector_rank"], only[0]["keyword_rank"]) == (1, 1)
    assert only[0]["score"] == pytest.approx(2 / 61)
    assert len(hybrid(hybrid_rows, n=50, document="hdoc")) == 6


# ---------------------------------------------------------------- question_vector

def test_question_vector_embeds_once_and_then_reads_the_cache(conn):
    vector = [0.25, -1.5] + [0.0] * (DIMS - 2)
    embedder = CountingEmbedder(vector)
    question = "What is the maximum supply voltage?"
    first = search.question_vector(conn, question, embed_fn=embedder)
    assert embedder.texts == [question]
    assert isinstance(first, str) and first.startswith("[") and first.endswith("]")
    assert floats(first) == pytest.approx(vector)
    assert len(floats(first)) == DIMS

    second = search.question_vector(conn, question, embed_fn=embedder)
    assert embedder.texts == [question]  # no second call
    assert isinstance(second, str)
    assert floats(second) == pytest.approx(floats(first))
    # Not even an embedder that fails is reached once the vector is stored.
    assert floats(search.question_vector(conn, question, embed_fn=no_embedding)) == pytest.approx(vector)


def test_question_vector_a_different_question_embeds_again(conn):
    embedder = CountingEmbedder()
    search.question_vector(conn, "What is the maximum supply voltage?", embed_fn=embedder)
    search.question_vector(conn, "What is the minimum supply voltage?", embed_fn=embedder)
    assert embedder.texts == ["What is the maximum supply voltage?", "What is the minimum supply voltage?"]
    search.question_vector(conn, "What is the maximum supply voltage?", embed_fn=embedder)
    search.question_vector(conn, "What is the minimum supply voltage?", embed_fn=embedder)
    assert len(embedder.texts) == 2


def test_question_vector_keeps_each_question_with_its_own_vector(conn):
    one = search.question_vector(conn, "first question", embed_fn=CountingEmbedder(mix(1, 0)))
    two = search.question_vector(conn, "second question", embed_fn=CountingEmbedder(mix(0, 1)))
    assert floats(one)[:2] == [1.0, 0.0] and floats(two)[:2] == [0.0, 1.0]
    assert floats(search.question_vector(conn, "first question", embed_fn=no_embedding))[:2] == [1.0, 0.0]
    assert floats(search.question_vector(conn, "second question", embed_fn=no_embedding))[:2] == [0.0, 1.0]


def test_question_vector_uses_the_layer_3_embedding_cache(conn):
    """The cache is the one the load fills: text hash plus the model setup (see embed.text_hash, embed.embed_key)."""
    question = "What is the quiescent current?"
    vector = mix(0.6, 0.8)
    db.store_embeddings(conn, embed.embed_key(), {embed.text_hash(question): vector})
    assert floats(search.question_vector(conn, question, embed_fn=no_embedding)) == pytest.approx(vector)

    other = "What is the storage temperature?"
    search.question_vector(conn, other, embed_fn=CountingEmbedder(mix(0, 1)))
    assert db.cached_hashes(conn, [embed.text_hash(other)], embed.embed_key()) == {embed.text_hash(other)}


def test_question_vector_can_be_given_to_vector_search(vector_rows):
    question_vec = search.question_vector(vector_rows, "anything", embed_fn=CountingEmbedder(mix(0.6, 0.8)))
    hits = search.vector_search(vector_rows, question_vec, n=1, strategy=STRATEGY)
    assert texts(hits) == [MID]
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-5)


# ---------------------------------------------------------------- search

LM35_SUPPLY = "The LM35 operates from a supply voltage of 4 V to 30 V."
LM35_OUTPUT = "The output voltage is linear at 10 mV per degree."
LM35_PACKAGE = "Available packages are TO-92 and SOIC."
ESP32_SUPPLY = "The ESP32 supply voltage on VDD3P3 is 2.3 V to 3.6 V."
ESP32_SLEEP = "Deep sleep reduces the supply current to 10 µA."
ESP32_RADIO = "The radio supports 2.4 GHz operation."
LM35_TEXTS = [LM35_SUPPLY, LM35_OUTPUT, LM35_PACKAGE]
ESP32_TEXTS = [ESP32_SUPPLY, ESP32_SLEEP, ESP32_RADIO]


@pytest.fixture
def two_documents(conn):
    """Three chunks in each of two real document names, so `detect_part` can name them."""
    add(conn, "lm35", STRATEGY, [(LM35_SUPPLY, mix(0.6, 0.8)), (LM35_OUTPUT, mix(0.28, 0.96)),
                                 (LM35_PACKAGE, mix(0, 1))])
    add(conn, "esp32_datasheet_en", STRATEGY, [(ESP32_SUPPLY, mix(1, 0)), (ESP32_SLEEP, mix(0.8, 0.6)),
                                               (ESP32_RADIO, mix(-1, 0))])
    return conn


def run_search(conn, question, mode, embed_fn, n=10, document=None, ts_config="english"):
    return search.search(conn, question, mode=mode, n=n, strategy=STRATEGY, ts_config=ts_config, document=document,
                         embed_fn=embed_fn)


def test_search_keyword_mode_never_calls_the_embedder(two_documents):
    hits = run_search(two_documents, "supply voltage", "keyword", no_embedding)
    assert hits == search.keyword_search(two_documents, "supply voltage", n=10, strategy=STRATEGY,
                                         ts_config="english")
    assert set(texts(hits)) == {LM35_SUPPLY, LM35_OUTPUT, ESP32_SUPPLY, ESP32_SLEEP}
    assert all(set(hit) == HIT_KEYS for hit in hits)


def test_search_vector_mode_calls_the_embedder_once(two_documents):
    embedder = CountingEmbedder(QUERY_VEC)
    question = "What is the supply voltage?"
    hits = run_search(two_documents, question, "vector", embedder)
    assert embedder.texts == [question]
    assert texts(hits) == [ESP32_SUPPLY, ESP32_SLEEP, LM35_SUPPLY, LM35_OUTPUT, LM35_PACKAGE, ESP32_RADIO]
    assert hits == search.vector_search(two_documents, QUERY_VEC, n=10, strategy=STRATEGY)
    assert all(set(hit) == HIT_KEYS for hit in hits)


def test_search_hybrid_mode_calls_the_embedder_once(two_documents):
    embedder = CountingEmbedder(QUERY_VEC)
    question = "What is the supply voltage?"
    hits = run_search(two_documents, question, "hybrid", embedder)
    assert embedder.texts == [question]
    assert hits == search.hybrid_search(two_documents, question, QUERY_VEC, n=10, strategy=STRATEGY,
                                        ts_config="english")
    assert all(set(hit) == HYBRID_KEYS for hit in hits)
    assert hits[0]["text"] == ESP32_SUPPLY  # nearest vector, and it has both words


def test_search_the_same_question_again_is_not_embedded_again(two_documents):
    embedder = CountingEmbedder(QUERY_VEC)
    first = run_search(two_documents, "What is the supply voltage?", "vector", embedder)
    second = run_search(two_documents, "What is the supply voltage?", "hybrid", embedder)
    assert len(embedder.texts) == 1
    assert texts(first)[0] == texts(second)[0] == ESP32_SUPPLY


def test_search_n_and_ts_config_are_passed_on(two_documents):
    assert len(run_search(two_documents, "supply voltage", "keyword", no_embedding, n=2)) == 2
    assert len(run_search(two_documents, "supply voltage", "vector", CountingEmbedder(), n=2)) == 2
    assert len(run_search(two_documents, "supply voltage", "hybrid", CountingEmbedder(), n=2)) == 2
    # "packages" is in the text; "package" finds it only when words are stemmed.
    assert texts(run_search(two_documents, "package", "keyword", no_embedding, ts_config="english")) == [LM35_PACKAGE]
    assert run_search(two_documents, "package", "keyword", no_embedding, ts_config="simple") == []


@pytest.mark.parametrize("mode", ["", "Hybrid", "semantic", "vector,keyword", None])
def test_search_a_bad_mode_raises_value_error(two_documents, mode):
    with pytest.raises(ValueError):
        run_search(two_documents, "supply voltage", mode, CountingEmbedder())


def test_detect_part_is_off_by_default():
    assert config.DETECT_PART is False


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_search_with_detect_part_on_returns_only_the_named_document(two_documents, monkeypatch, mode):
    monkeypatch.setattr(config, "DETECT_PART", True)
    hits = run_search(two_documents, "What is the LM35 supply voltage?", mode, CountingEmbedder(QUERY_VEC))
    assert hits
    assert {hit["document"] for hit in hits} == {"lm35"}
    assert LM35_SUPPLY in texts(hits)
    hits = run_search(two_documents, "What is the ESP32 supply voltage?", mode, CountingEmbedder(QUERY_VEC))
    assert hits
    assert {hit["document"] for hit in hits} == {"esp32_datasheet_en"}


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_search_with_detect_part_off_both_documents_can_appear(two_documents, monkeypatch, mode):
    monkeypatch.setattr(config, "DETECT_PART", False)
    hits = run_search(two_documents, "What is the LM35 supply voltage?", mode, CountingEmbedder(QUERY_VEC))
    assert {hit["document"] for hit in hits} == {"lm35", "esp32_datasheet_en"}


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_search_with_detect_part_on_and_no_single_part_named_searches_everything(two_documents, monkeypatch, mode):
    monkeypatch.setattr(config, "DETECT_PART", True)
    for question in ("What is the supply voltage?", "Is the LM35 supply voltage higher than the ESP32 one?"):
        hits = run_search(two_documents, question, mode, CountingEmbedder(QUERY_VEC))
        assert {hit["document"] for hit in hits} == {"lm35", "esp32_datasheet_en"}


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
@pytest.mark.parametrize("detect", [True, False])
def test_search_an_explicit_document_is_respected(two_documents, monkeypatch, mode, detect):
    monkeypatch.setattr(config, "DETECT_PART", detect)
    # The question names the LM35, but the caller asked for the ESP32 datasheet.
    hits = run_search(two_documents, "What is the LM35 supply voltage?", mode, CountingEmbedder(QUERY_VEC),
                      document="esp32_datasheet_en")
    assert hits
    assert {hit["document"] for hit in hits} == {"esp32_datasheet_en"}
    assert set(texts(hits)) <= set(ESP32_TEXTS)
    hits = run_search(two_documents, "supply voltage", mode, CountingEmbedder(QUERY_VEC), document="lm35")
    assert hits
    assert set(texts(hits)) <= set(LM35_TEXTS)
