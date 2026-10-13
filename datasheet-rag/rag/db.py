"""All SQL of the project. Cycle 1 backend: psycopg against the local Postgres with pgvector.

Every function takes a connection from `connect()`. A vector goes in as the text `[0.1,0.2,...]` cast to
`vector`, and comes back out as the same text, so no extra package is needed.
"""
import psycopg
from psycopg import sql

from rag import config

TEST_SCHEMA_PREFIX = "test_"

_TABLES = """
CREATE TABLE IF NOT EXISTS documents (
    id       SERIAL PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    file     TEXT,
    part     TEXT,
    revision TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id          SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    strategy    TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page        INTEGER NOT NULL,
    page_label  TEXT,
    section     TEXT,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    embedding   vector({dims}) NOT NULL,
    tsv_english tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    tsv_simple  tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    UNIQUE (document_id, strategy, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_strategy_idx ON chunks (strategy);
CREATE INDEX IF NOT EXISTS chunks_tsv_english_idx ON chunks USING GIN (tsv_english);
CREATE INDEX IF NOT EXISTS chunks_tsv_simple_idx ON chunks USING GIN (tsv_simple);

CREATE TABLE IF NOT EXISTS embedding_cache (
    text_sha256 TEXT NOT NULL,
    model       TEXT NOT NULL,
    embedding   vector({dims}) NOT NULL,
    PRIMARY KEY (text_sha256, model)
);
"""


def connect(schema: str | None = None) -> psycopg.Connection:
    """A connection to the local database. Each statement commits on its own unless inside `conn.transaction()`.

    `schema` puts that schema first on the search path, so the tables are created and read there (used by tests).
    """
    conn = psycopg.connect(
        host=config.PG_HOST, port=config.PG_PORT, user=config.PG_USER, dbname=config.PG_DATABASE,
        password=config.PG_PASSWORD, connect_timeout=5, autocommit=True,
    )
    if schema is not None:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    return conn


def create_schema(conn: psycopg.Connection) -> None:
    """Create the extension, the tables and the indexes. Safe to run again."""
    with conn.transaction():
        # In public whatever the search path, so that dropping a test schema cannot take the extension with it.
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        conn.execute(sql.SQL(_TABLES).format(dims=sql.Literal(int(config.EMBED_DIMENSIONS))))


def vector_text(vector) -> str:
    """The text form pgvector reads. A vector that is already text is passed through."""
    if isinstance(vector, str):
        return vector
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def upsert_document(conn: psycopg.Connection, name: str, file: str | None = None, part: str | None = None,
                    revision: str | None = None) -> int:
    """Insert the document or update its details. A detail given as None keeps its stored value. Returns its id."""
    row = conn.execute(
        """
        INSERT INTO documents (name, file, part, revision) VALUES (%s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            file = COALESCE(EXCLUDED.file, documents.file),
            part = COALESCE(EXCLUDED.part, documents.part),
            revision = COALESCE(EXCLUDED.revision, documents.revision)
        RETURNING id
        """,
        (name, file, part, revision),
    ).fetchone()
    return row[0]


def replace_chunks(conn: psycopg.Connection, document_id: int, strategy: str, chunks: list[dict],
                   vectors: list) -> int:
    """Replace every chunk of one document and strategy, in one transaction. Returns the number of rows written."""
    if len(chunks) != len(vectors):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    rows = [
        (document_id, strategy, index, chunk["page"], chunk["page_label"], chunk["section"], chunk["kind"],
         chunk["text"], vector_text(vector))
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    with conn.transaction():
        conn.execute("DELETE FROM chunks WHERE document_id = %s AND strategy = %s", (document_id, strategy))
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (document_id, strategy, chunk_index, page, page_label, section, kind, text, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                """,
                rows,
            )
    return len(rows)


def cached_hashes(conn: psycopg.Connection, hashes: list[str], model: str) -> set[str]:
    """Which of these text hashes already have a stored vector for this model."""
    rows = conn.execute(
        "SELECT text_sha256 FROM embedding_cache WHERE model = %s AND text_sha256 = ANY(%s)", (model, list(hashes))
    ).fetchall()
    return {row[0] for row in rows}


def cached_embeddings(conn: psycopg.Connection, hashes: list[str], model: str) -> dict[str, str]:
    """Stored vectors (as text) for these text hashes and this model. A hash with no vector is left out."""
    rows = conn.execute(
        "SELECT text_sha256, embedding::text FROM embedding_cache WHERE model = %s AND text_sha256 = ANY(%s)",
        (model, list(hashes)),
    ).fetchall()
    return dict(rows)


def store_embeddings(conn: psycopg.Connection, model: str, vectors: dict) -> None:
    """Keep vectors that were paid for, keyed by the hash of the text that was embedded."""
    rows = [(text_sha256, model, vector_text(vector)) for text_sha256, vector in vectors.items()]
    if not rows:
        return
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO embedding_cache (text_sha256, model, embedding) VALUES (%s, %s, %s::vector)
            ON CONFLICT (text_sha256, model) DO UPDATE SET embedding = EXCLUDED.embedding
            """,
            rows,
        )


def count_chunks(conn: psycopg.Connection, strategy: str | None = None, document: str | None = None) -> int:
    """Number of stored chunks, optionally for one strategy and/or one document name."""
    row = conn.execute(
        """
        SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE (%(strategy)s::text IS NULL OR c.strategy = %(strategy)s)
          AND (%(document)s::text IS NULL OR d.name = %(document)s)
        """,
        {"strategy": strategy, "document": document},
    ).fetchone()
    return row[0]


def strategy_counts(conn: psycopg.Connection) -> dict[tuple[str, str], int]:
    """(document name, strategy) -> number of stored chunks."""
    rows = conn.execute(
        """
        SELECT d.name, c.strategy, count(*) FROM chunks c JOIN documents d ON d.id = c.document_id
        GROUP BY d.name, c.strategy
        """
    ).fetchall()
    return {(name, strategy): count for name, strategy, count in rows}


def embedding_dims(conn: psycopg.Connection) -> int | None:
    """Length of a stored vector, or None when no chunk is stored."""
    row = conn.execute("SELECT vector_dims(embedding) FROM chunks LIMIT 1").fetchone()
    return row[0] if row else None


def list_documents(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, name, file, part, revision FROM documents ORDER BY name").fetchall()
    return [dict(zip(("id", "name", "file", "part", "revision"), row, strict=True)) for row in rows]


def sample_chunks(conn: psycopg.Connection, strategy: str, document: str, limit: int = 3) -> list[dict]:
    """The first stored chunks of one document and strategy, with both keyword columns as text."""
    columns = ("chunk_index", "page", "page_label", "section", "kind", "text", "tsv_english", "tsv_simple")
    rows = conn.execute(
        """
        SELECT c.chunk_index, c.page, c.page_label, c.section, c.kind, c.text, c.tsv_english::text, c.tsv_simple::text
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.strategy = %s AND d.name = %s
        ORDER BY c.chunk_index LIMIT %s
        """,
        (strategy, document, limit),
    ).fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _test_schema(name: str) -> sql.Identifier:
    if not name.startswith(TEST_SCHEMA_PREFIX):
        raise ValueError(f"a test schema name must start with {TEST_SCHEMA_PREFIX!r}, got {name!r}")
    return sql.Identifier(name)


def create_test_schema(conn: psycopg.Connection, name: str) -> None:
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(_test_schema(name)))


def drop_test_schema(conn: psycopg.Connection, name: str) -> None:
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(_test_schema(name)))
