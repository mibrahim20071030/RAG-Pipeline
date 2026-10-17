"""All SQL of the project, run with psycopg against Postgres with pgvector.

Every function takes a connection from `connect()`, which reaches the local Docker Postgres or Aurora
(`config.DB_TARGET`). The SQL is the same for both. A vector goes in as the text `[0.1,0.2,...]` cast to
`vector`, and comes back out as the same text, so no extra package is needed.
"""
import functools
import logging
import time
from pathlib import Path

import psycopg
from psycopg import sql

from rag import config

_log = logging.getLogger(__name__)

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

CREATE TABLE IF NOT EXISTS rerank_cache (
    question_sha256 TEXT NOT NULL,
    text_sha256     TEXT NOT NULL,
    model           TEXT NOT NULL,
    score           DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (question_sha256, text_sha256, model)
);
"""


def connect(schema: str | None = None) -> psycopg.Connection:
    """A connection to the database named by `config.DB_TARGET` ("local" or "aurora").

    Each statement commits on its own unless inside `conn.transaction()`. `schema` puts that schema first on the
    search path, so the tables are created and read there (used by tests).
    """
    if config.DB_TARGET == "local":
        conn = psycopg.connect(
            host=config.PG_HOST, port=config.PG_PORT, user=config.PG_USER, dbname=config.PG_DATABASE,
            password=config.PG_PASSWORD, connect_timeout=5, autocommit=True,
        )
    elif config.DB_TARGET == "aurora":
        conn = _connect_aurora()
    else:
        raise ValueError(f"DB_TARGET must be 'local' or 'aurora', not {config.DB_TARGET!r}")
    if schema is not None:
        try:
            use_schema(conn, schema)
        except BaseException:
            conn.close()  # an open session would keep Aurora from pausing
            raise
    return conn


@functools.cache
def _rds_client():
    import boto3

    return boto3.client("rds", region_name=config.AWS_REGION)


def _ca_bundle() -> str:
    if config.AURORA_CA_BUNDLE:
        return config.AURORA_CA_BUNDLE
    import botocore

    return str(Path(botocore.__file__).parent / "cacert.pem")


def _connect_aurora() -> psycopg.Connection:
    """A connection through the internet access gateway, logged in with a fresh IAM token.

    The token (15 minutes) is needed only to log in; an open connection outlives it. The certificate is checked
    (`verify-full`), so a fake server cannot collect the token. A paused cluster takes about 18 s to answer, which
    the timeout covers; a failed attempt is logged and retried with a new token after a short pause.
    """
    if not config.AURORA_HOST:
        raise ValueError("DB_TARGET is 'aurora' but AURORA_HOST is empty (see datasheet-rag/.env.example)")
    rds = _rds_client()
    start = time.perf_counter()
    for attempt in range(1, config.AURORA_CONNECT_ATTEMPTS + 1):
        # Signed locally from the AWS credentials; no AWS call.
        token = rds.generate_db_auth_token(DBHostname=config.AURORA_HOST, Port=config.AURORA_PORT,
                                           DBUsername=config.AURORA_USER, Region=config.AWS_REGION)
        try:
            return psycopg.connect(
                host=config.AURORA_HOST, port=config.AURORA_PORT, user=config.AURORA_USER,
                dbname=config.AURORA_DATABASE, password=token, sslmode="verify-full", sslrootcert=_ca_bundle(),
                connect_timeout=config.AURORA_CONNECT_TIMEOUT_S, autocommit=True,
            )
        except psycopg.OperationalError as error:
            _log.warning("Aurora connect attempt %d failed after %.1f s: %s", attempt,
                         time.perf_counter() - start, error)
            if attempt == config.AURORA_CONNECT_ATTEMPTS:
                raise
            time.sleep(config.AURORA_RETRY_PAUSE_S)
    raise AssertionError("unreachable")


def use_schema(conn: psycopg.Connection, schema: str) -> None:
    """Put `schema` first on the search path of this connection, so the tables are created and read there."""
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))


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


def cached_rerank_scores(conn: psycopg.Connection, question_sha256: str, hashes: list[str],
                         model: str) -> dict[str, float]:
    """Stored rerank scores of one question for these text hashes and this model. A hash with no score is left out."""
    rows = conn.execute(
        """
        SELECT text_sha256, score FROM rerank_cache
        WHERE question_sha256 = %s AND model = %s AND text_sha256 = ANY(%s)
        """,
        (question_sha256, model, list(hashes)),
    ).fetchall()
    return dict(rows)


def store_rerank_scores(conn: psycopg.Connection, question_sha256: str, model: str, scores: dict[str, float]) -> None:
    """Keep rerank scores that were paid for, keyed by the hashes of the question and of the text that was sent."""
    rows = [(question_sha256, text_sha256, model, float(score)) for text_sha256, score in scores.items()]
    if not rows:
        return
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rerank_cache (question_sha256, text_sha256, model, score) VALUES (%s, %s, %s, %s)
            ON CONFLICT (question_sha256, text_sha256, model) DO UPDATE SET score = EXCLUDED.score
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


def mean_chunk_tokens(conn: psycopg.Connection, strategy: str, chars_per_token: int) -> float:
    """Mean approximate tokens of the strategy's stored chunk texts, each rounded up as `approx_tokens` does;
    0 when none is stored."""
    row = conn.execute(
        "SELECT coalesce(avg(ceil(length(text)::numeric / %s)), 0) FROM chunks WHERE strategy = %s",
        (chars_per_token, strategy),
    ).fetchone()
    return float(row[0])


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


_HIT_KEYS = ("id", "document", "page", "page_label", "section", "kind", "text", "score")

# Keyword search configuration -> the generated column built with it (tsv_english, tsv_simple in `_TABLES`).
_TSV_COLUMNS = {name: f"tsv_{name}" for name in config.KEYWORD_CONFIGS}

# The raw `<=>` stands alone in ORDER BY so that an index can serve it; the score is computed in SELECT only.
_NEAREST_SQL = """
SELECT c.id, d.name, c.page, c.page_label, c.section, c.kind, c.text,
       1 - (c.embedding <=> %(vector)s::vector) AS score
FROM chunks c JOIN documents d ON d.id = c.document_id
WHERE c.strategy = %(strategy)s AND (%(document)s::text IS NULL OR d.name = %(document)s)
ORDER BY c.embedding <=> %(vector)s::vector
LIMIT %(n)s
"""

# One tsquery per term, OR-joined by summing. Each term is weighted by how rare it is in the searched set
# (N chunks, df of them matching), times ts_rank normalised by document length (the 1).
_KEYWORD_SQL = """
WITH terms AS (
    SELECT DISTINCT ON (q::text) q
    FROM (SELECT plainto_tsquery(%(cfg)s::regconfig, t) AS q FROM unnest(%(terms)s::text[]) AS t) x
    WHERE numnode(q) > 0
), total AS (
    SELECT count(*)::float AS n FROM chunks c JOIN documents d ON d.id = c.document_id
    WHERE c.strategy = %(strategy)s AND (%(document)s::text IS NULL OR d.name = %(document)s)
), hits AS (
    SELECT c.id, ts_rank(c.{col}, t.q, 1) AS r, count(*) OVER (PARTITION BY t.q::text) AS df
    FROM chunks c JOIN documents d ON d.id = c.document_id JOIN terms t ON c.{col} @@ t.q
    WHERE c.strategy = %(strategy)s AND (%(document)s::text IS NULL OR d.name = %(document)s)
)
SELECT c.id, d.name, c.page, c.page_label, c.section, c.kind, c.text,
       sum(ln(1 + (total.n - h.df + 0.5) / (h.df + 0.5)) * h.r) AS score
FROM hits h JOIN chunks c ON c.id = h.id JOIN documents d ON d.id = c.document_id, total
GROUP BY c.id, d.name
ORDER BY score DESC, c.id
LIMIT %(n)s
"""


def _hits(rows: list[tuple]) -> list[dict]:
    """Rows of a search query as hits, with the score as a Python float."""
    return [dict(zip(_HIT_KEYS, (*row[:-1], float(row[-1])), strict=True)) for row in rows]


def _nearest_params(vector, strategy: str, n: int, document: str | None) -> dict:
    return {"vector": vector_text(vector), "strategy": strategy, "n": n, "document": document}


def nearest_chunks(conn: psycopg.Connection, vector, strategy: str, n: int,
                   document: str | None = None) -> list[dict]:
    """The n chunks of one strategy closest to the vector by cosine distance, best first. Score = 1 - distance."""
    rows = conn.execute(_NEAREST_SQL, _nearest_params(vector, strategy, n, document)).fetchall()
    # Chunks with the same text have the same distance. The id settles their order here, not in ORDER BY.
    return sorted(_hits(rows), key=lambda hit: (-hit["score"], hit["id"]))


def explain_nearest(conn: psycopg.Connection, vector, strategy: str, n: int) -> str:
    """The plan Postgres picks for the `nearest_chunks` query over all documents."""
    rows = conn.execute("EXPLAIN " + _NEAREST_SQL, _nearest_params(vector, strategy, n, None)).fetchall()
    return "\n".join(row[0] for row in rows)


def _with_hnsw(conn: psycopg.Connection, query: str, params: dict) -> list[tuple]:
    """Run a query with a throwaway HNSW index on `chunks`: the transaction rolls back, so the index never stays.

    Sequential scans and sorts are switched off so that the planner uses the index: otherwise it reads the
    strategy's rows through `chunks_strategy_idx` and sorts them, which is exact search. The iterative scan
    carries on past `ef_search` until enough rows pass the strategy and document filter.
    """
    with conn.transaction(force_rollback=True):
        conn.execute("CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops)")
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        conn.execute("SET LOCAL enable_seqscan = off")
        conn.execute("SET LOCAL enable_sort = off")
        return conn.execute(query, params).fetchall()


def hnsw_nearest(conn: psycopg.Connection, vector, strategy: str, n: int,
                 document: str | None = None) -> list[dict]:
    """`nearest_chunks` answered by an approximate HNSW index instead of exact search (the index experiment)."""
    rows = _with_hnsw(conn, _NEAREST_SQL, _nearest_params(vector, strategy, n, document))
    # The relaxed order can return rows slightly out of order; sorted as in `nearest_chunks`.
    return sorted(_hits(rows), key=lambda hit: (-hit["score"], hit["id"]))


def explain_hnsw(conn: psycopg.Connection, vector, strategy: str, n: int) -> str:
    """The plan Postgres picks for the `hnsw_nearest` query over all documents."""
    rows = _with_hnsw(conn, "EXPLAIN " + _NEAREST_SQL, _nearest_params(vector, strategy, n, None))
    return "\n".join(row[0] for row in rows)


def hnsw_index_count(conn: psycopg.Connection) -> int:
    """Number of HNSW indexes on `chunks` in the current schema. Should be 0 outside `hnsw_nearest`."""
    # Read from the catalogs, not pg_indexes: that view rebuilds the definition text of indexes in every schema,
    # which fails when another session drops its test schema at the same moment.
    row = conn.execute(
        """
        SELECT count(*)
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_class x ON x.oid = i.indexrelid
        JOIN pg_am a ON a.oid = x.relam
        WHERE t.relnamespace = current_schema()::regnamespace AND t.relname = 'chunks' AND a.amname = 'hnsw'
        """
    ).fetchone()
    return row[0]


def keyword_chunks(conn: psycopg.Connection, terms: list[str], strategy: str, n: int, ts_config: str = "english",
                   document: str | None = None) -> list[dict]:
    """The n chunks of one strategy that best match any of the terms, best first. No match gives an empty list."""
    if ts_config not in _TSV_COLUMNS:
        raise ValueError(f"ts_config must be one of {sorted(_TSV_COLUMNS)}, got {ts_config!r}")
    query = sql.SQL(_KEYWORD_SQL).format(col=sql.Identifier(_TSV_COLUMNS[ts_config]))
    rows = conn.execute(
        query, {"cfg": ts_config, "terms": list(terms), "strategy": strategy, "n": n, "document": document}
    ).fetchall()
    return _hits(rows)


def _test_schema(name: str) -> sql.Identifier:
    if not name.startswith(TEST_SCHEMA_PREFIX):
        raise ValueError(f"a test schema name must start with {TEST_SCHEMA_PREFIX!r}, got {name!r}")
    return sql.Identifier(name)


def create_test_schema(conn: psycopg.Connection, name: str) -> None:
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(_test_schema(name)))


def drop_test_schema(conn: psycopg.Connection, name: str) -> None:
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(_test_schema(name)))


def server_info(conn: psycopg.Connection) -> dict:
    """The server version string and the installed pgvector version (None when the extension is missing)."""
    row = conn.execute(
        "SELECT version(), (SELECT extversion FROM pg_extension WHERE extname = 'vector')"
    ).fetchone()
    return {"version": row[0], "pgvector": row[1]}
