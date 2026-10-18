"""Cycle 2 step 3 tests: copying `embedding_cache` between two databases, here two throwaway schemas of the local
Postgres. No AWS call. Skipped without Postgres."""
import uuid

import pytest

from rag import config, db
from rag.copy_cache import copy_embeddings

MODEL = "fake-model"


def vector(seed: int) -> list[float]:
    return [float(seed)] + [0.0] * (config.EMBED_DIMENSIONS - 1)


@pytest.fixture
def dest(database):
    """A second throwaway schema with the tables, next to the one of the `conn` fixture."""
    schema = f"{db.TEST_SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    with db.connect() as admin:
        db.create_test_schema(admin, schema)
        try:
            with db.connect(schema) as connection:
                db.create_schema(connection)
                yield connection
        finally:
            db.drop_test_schema(admin, schema)


def stored(conn, model=MODEL) -> dict[str, list[float]]:
    rows = [row for row in db.embedding_rows(conn, limit=1000) if row[1] == model]
    return {h: [float(x) for x in v.strip("[]").split(",")] for h, _, v in rows}


def test_embedding_rows_are_in_key_order_and_page_after_a_key(conn):
    db.store_embeddings(conn, "b-model", {"h1": vector(1)})
    db.store_embeddings(conn, "a-model", {"h2": vector(2), "h1": vector(3)})
    rows = db.embedding_rows(conn)
    assert [row[:2] for row in rows] == [("h1", "a-model"), ("h1", "b-model"), ("h2", "a-model")]
    assert [row[:2] for row in db.embedding_rows(conn, after=("h1", "a-model"), limit=1)] == [("h1", "b-model")]
    assert db.embedding_rows(conn, after=("h2", "a-model")) == []
    assert rows[0][2].startswith("[3")


def test_embedding_counts_per_model(conn):
    db.store_embeddings(conn, "a-model", {"h1": vector(1), "h2": vector(2)})
    db.store_embeddings(conn, "b-model", {"h1": vector(1)})
    assert db.embedding_counts(conn) == {"a-model": 2, "b-model": 1}


def test_database_size_is_positive(conn):
    assert db.database_size(conn) > 0


def test_only_missing_rows_are_copied_and_existing_ones_are_kept(conn, dest):
    db.store_embeddings(conn, MODEL, {"h1": vector(1), "h2": vector(2), "h3": vector(3)})
    db.store_embeddings(dest, MODEL, {"h2": vector(99)})
    assert copy_embeddings(conn, dest) == {"read": 3, "copied": 2}
    assert stored(dest) == {"h1": vector(1), "h2": vector(99), "h3": vector(3)}


def test_a_batch_smaller_than_the_table_copies_everything(conn, dest):
    db.store_embeddings(conn, MODEL, {f"h{i}": vector(i) for i in range(7)})
    db.store_embeddings(conn, "other-model", {"h0": vector(50)})
    assert copy_embeddings(conn, dest, batch=2) == {"read": 8, "copied": 8}
    assert stored(dest) == {f"h{i}": vector(i) for i in range(7)}
    assert stored(dest, "other-model") == {"h0": vector(50)}


def test_a_second_copy_copies_nothing(conn, dest):
    db.store_embeddings(conn, MODEL, {"h1": vector(1), "h2": vector(2)})
    copy_embeddings(conn, dest)
    assert copy_embeddings(conn, dest) == {"read": 2, "copied": 0}


def test_an_empty_source_copies_nothing(conn, dest):
    assert copy_embeddings(conn, dest) == {"read": 0, "copied": 0}
    assert db.embedding_counts(dest) == {}
