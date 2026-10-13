"""Layer 3 tests: the tables and the load. They need the local Postgres and skip themselves without it.

Everything runs in a throwaway schema (the `conn` fixture in conftest.py) with a fake embedder, so the real
rows are never touched and no test calls AWS.
"""
import psycopg
import pytest

from rag import chunk, config, db, load

STRATEGY = "fixed_256"
SENTENCE = "The supply voltage VDD must stay between 3.0 V and 3.6 V while the GPIO16 pins are running. "


def make_page(number: int, text: str) -> dict:
    return {"page": number, "page_label": str(number), "blank": not text, "text": text,
            "text_outside_tables": text, "tables": []}


def make_doc(pages: int, name: str = "demo") -> dict:
    """A parsed document whose pages hold different text, each long enough for several chunks."""
    return {"document": name, "file": f"{name}.pdf", "page_count": pages,
            "pages": [make_page(n, f"Page {n}. " + SENTENCE * 30) for n in range(1, pages + 1)]}


class FakeEmbedder:
    """Stands in for embed.embed_texts: a vector that depends on the text, and a record of what was asked."""

    def __init__(self):
        self.texts = []

    def __call__(self, texts, label=""):
        self.texts.extend(texts)
        return [[(len(text) % 97 + 1) / 100] + [0.0] * (config.EMBED_DIMENSIONS - 1) for text in texts]


def test_create_schema_twice_is_fine(conn):
    db.create_schema(conn)
    assert db.count_chunks(conn) == 0
    assert db.embedding_dims(conn) is None


def test_load_stores_every_chunk_with_a_1024_vector(conn):
    parsed = make_doc(3)
    expected = chunk.chunk_document(parsed, STRATEGY)
    result = load.load_document(conn, parsed, STRATEGY, FakeEmbedder())
    assert len(expected) > 3
    assert result["chunks"] == len(expected)
    assert db.count_chunks(conn, STRATEGY) == len(expected)
    assert db.count_chunks(conn, STRATEGY, "demo") == len(expected)
    assert db.count_chunks(conn, "page") == 0
    assert db.embedding_dims(conn) == config.EMBED_DIMENSIONS == 1024


def test_stored_rows_keep_the_chunk_fields_and_order(conn):
    parsed = make_doc(2)
    expected = chunk.chunk_document(parsed, STRATEGY)
    load.load_document(conn, parsed, STRATEGY, FakeEmbedder())
    stored = db.sample_chunks(conn, STRATEGY, "demo", limit=len(expected))
    assert [row["chunk_index"] for row in stored] == list(range(len(expected)))
    for row, source in zip(stored, expected, strict=True):
        for field in ("page", "page_label", "section", "kind", "text"):
            assert row[field] == source[field]


def test_keyword_columns_are_filled(conn):
    load.load_document(conn, make_doc(1), STRATEGY, FakeEmbedder())
    row = db.sample_chunks(conn, STRATEGY, "demo", limit=1)[0]
    # 'english' stems words and drops stop words; 'simple' keeps every word as it is.
    assert "'run'" in row["tsv_english"] and "'running'" not in row["tsv_english"]
    assert "'running'" in row["tsv_simple"] and "'the'" in row["tsv_simple"]
    assert "'gpio16'" in row["tsv_english"] and "'gpio16'" in row["tsv_simple"]


def test_second_load_changes_nothing_and_embeds_nothing(conn):
    parsed = make_doc(3)
    first = FakeEmbedder()
    load.load_document(conn, parsed, STRATEGY, first)
    count = db.count_chunks(conn, STRATEGY)
    second = FakeEmbedder()
    result = load.load_document(conn, parsed, STRATEGY, second)
    assert first.texts and second.texts == []
    assert result["embedded"] == 0
    assert db.count_chunks(conn, STRATEGY) == count
    assert len(db.list_documents(conn)) == 1


def test_a_text_shared_by_two_strategies_is_embedded_once(conn):
    parsed = make_doc(2)
    load.load_document(conn, parsed, "fixed_400", FakeEmbedder())
    again = FakeEmbedder()
    load.load_document(conn, parsed, "structure_aware", again)
    shared = {c["text"] for c in chunk.chunk_document(parsed, "fixed_400")}
    assert not shared & set(again.texts)


def test_repeated_text_inside_one_load_is_embedded_once(conn):
    parsed = make_doc(1)
    parsed["pages"].append({**parsed["pages"][0], "page": 2})
    embedder = FakeEmbedder()
    result = load.load_document(conn, parsed, STRATEGY, embedder)
    assert len(embedder.texts) == len(set(embedder.texts))
    assert result["chunks"] > result["embedded"]


def test_plan_counts_only_texts_without_a_vector(conn, monkeypatch):
    parsed = make_doc(2)
    monkeypatch.setattr(load.parse, "load_parsed", lambda name: parsed)
    before = load.plan_load(conn, ["demo"], [STRATEGY])
    assert before["chunks"] == len(chunk.chunk_document(parsed, STRATEGY))
    assert before["to_embed"] == before["unique_texts"] > 0
    assert before["est_tokens"] > 0 and before["est_cost_usd"] > 0
    load.load_document(conn, parsed, STRATEGY, FakeEmbedder())
    after = load.plan_load(conn, ["demo"], [STRATEGY])
    assert (after["to_embed"], after["est_tokens"], after["est_cost_usd"]) == (0, 0, 0)


def test_a_shorter_document_removes_the_extra_rows(conn):
    load.load_document(conn, make_doc(3), STRATEGY, FakeEmbedder())
    shorter = make_doc(1)
    load.load_document(conn, shorter, STRATEGY, FakeEmbedder())
    assert db.count_chunks(conn, STRATEGY) == len(chunk.chunk_document(shorter, STRATEGY))


def test_loads_of_other_documents_and_strategies_are_kept(conn):
    one, two = make_doc(1, "one"), make_doc(2, "two")
    load.load_document(conn, one, STRATEGY, FakeEmbedder())
    load.load_document(conn, two, STRATEGY, FakeEmbedder())
    load.load_document(conn, one, "page", FakeEmbedder())
    load.load_document(conn, one, STRATEGY, FakeEmbedder())
    assert db.strategy_counts(conn) == {
        ("one", STRATEGY): len(chunk.chunk_document(one, STRATEGY)),
        ("two", STRATEGY): len(chunk.chunk_document(two, STRATEGY)),
        ("one", "page"): 1,
    }


def test_wrong_length_vector_is_refused_and_the_old_rows_stay(conn):
    parsed = make_doc(2)
    load.load_document(conn, parsed, STRATEGY, FakeEmbedder())
    count = db.count_chunks(conn, STRATEGY)
    chunks = chunk.chunk_document(parsed, STRATEGY)[:2]
    document_id = db.list_documents(conn)[0]["id"]
    good = [0.5] * config.EMBED_DIMENSIONS
    with pytest.raises(psycopg.Error):
        db.replace_chunks(conn, document_id, STRATEGY, chunks, [good, [0.1, 0.2, 0.3]])
    assert db.count_chunks(conn, STRATEGY) == count


def test_chunks_and_vectors_must_pair_up(conn):
    with pytest.raises(ValueError):
        db.replace_chunks(conn, 1, STRATEGY, chunk.chunk_document(make_doc(1), STRATEGY), [])


def test_document_details_are_updated_not_duplicated(conn):
    first = db.upsert_document(conn, "demo", "demo.pdf", "PART-1", "Rev 1")
    second = db.upsert_document(conn, "demo", "demo.pdf", "PART-1", "Rev 2")
    assert first == second
    assert [d["revision"] for d in db.list_documents(conn)] == ["Rev 2"]
    # A detail that is not given keeps its stored value.
    assert db.upsert_document(conn, "demo") == first
    assert db.list_documents(conn)[0] == {"id": first, "name": "demo", "file": "demo.pdf", "part": "PART-1",
                                          "revision": "Rev 2"}


def test_vector_text_round_trip(conn):
    vector = [0.25, -1.5] + [0.0] * (config.EMBED_DIMENSIONS - 2)
    db.store_embeddings(conn, "model-a", {"abc": vector})
    assert db.cached_hashes(conn, ["abc", "missing"], "model-a") == {"abc"}
    assert db.cached_hashes(conn, ["abc"], "model-b") == set()
    stored = db.cached_embeddings(conn, ["abc"], "model-a")["abc"]
    assert stored.startswith("[0.25,-1.5,0,")
    assert db.vector_text(stored) == stored


def test_only_a_test_schema_can_be_dropped(conn):
    with pytest.raises(ValueError):
        db.drop_test_schema(conn, "public")
