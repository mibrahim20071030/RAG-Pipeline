"""The Aurora smoke test (Cycle 2 step 1). No Bedrock calls; the only cost is Aurora capacity time.

Run from datasheet-rag, with the AURORA_* keys in .env (see .env.example):
    python scripts\\aurora_smoke.py direct      # db.connect() with DB_TARGET=aurora, runs the real rag/db.py SQL
    python scripts\\aurora_smoke.py resume      # after 10+ idle minutes: time to the first answer
"""
import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from rag import config, db

STRATEGY = "smoke"


def need(*names: str) -> None:
    missing = [name for name in names if not getattr(config, name)]
    if missing:
        sys.exit(f"Missing in datasheet-rag\\.env: {', '.join(missing)} (see .env.example)")


def unit_vector(position: int) -> list[float]:
    """A 1024-number vector with a single 1 at `position`, so the expected nearest chunk is known."""
    vector = [0.0] * config.EMBED_DIMENSIONS
    vector[position] = 1.0
    return vector


def connect_aurora() -> tuple[psycopg.Connection, float]:
    """db.connect() pointed at Aurora, and the seconds it took."""
    need("AURORA_HOST", "AURORA_USER")
    config.DB_TARGET = "aurora"
    start = time.perf_counter()
    conn = db.connect()
    return conn, time.perf_counter() - start


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'pass' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    return ok


def db_checks(conn: psycopg.Connection) -> bool:
    """The real rag/db.py functions in a throwaway schema: tables, load, vector and keyword search, cache."""
    chunks = [
        {"page": 1, "page_label": "1", "section": None, "kind": "text",
         "text": "Each GPIO pin can source at most 40 mA."},
        {"page": 2, "page_label": "2", "section": None, "kind": "table",
         "text": "Absolute maximum ratings\nParameter: VDD, Max: 3.6, Unit: V"},
        {"page": 3, "page_label": "3", "section": None, "kind": "text",
         "text": "The TCTH022BE thermistor is a surface mount part."},
    ]
    vectors = [unit_vector(i) for i in range(len(chunks))]
    ok = True
    t = time.perf_counter()
    db.create_schema(conn)
    document_id = db.upsert_document(conn, "smoke", "smoke.pdf", "SMOKE", "1")
    written = db.replace_chunks(conn, document_id, STRATEGY, chunks, vectors)
    ok &= check("tables and load", written == 3, f"{written} rows, {time.perf_counter() - t:.2f} s")
    ok &= check("vector length", db.embedding_dims(conn) == config.EMBED_DIMENSIONS)

    t = time.perf_counter()
    top = db.nearest_chunks(conn, unit_vector(1), STRATEGY, 3)[0]
    ok &= check("nearest_chunks", top["page"] == 2 and abs(top["score"] - 1) < 1e-6,
                f"top page {top['page']}, score {top['score']:.3f}, {time.perf_counter() - t:.2f} s")
    explain = db.explain_nearest(conn, unit_vector(1), STRATEGY, 3)
    ok &= check("EXPLAIN shows <=>", "<=>" in explain)

    for ts_config in config.KEYWORD_CONFIGS:
        hits = db.keyword_chunks(conn, ["TCTH022BE", "thermistor"], STRATEGY, 3, ts_config)
        ok &= check(f"keyword_chunks {ts_config}", bool(hits) and hits[0]["page"] == 3)

    db.store_embeddings(conn, "smoke-model", {"abc": vectors[2]})
    stored = db.cached_embeddings(conn, ["abc"], "smoke-model").get("abc", "")
    ok &= check("embedding_cache round trip",
                [float(x) for x in stored.strip("[]").split(",")] == vectors[2])
    return ok


def run_direct() -> bool:
    print("direct: psycopg + IAM token through the internet access gateway")
    conn, seconds = connect_aurora()
    print(f"  connected in {seconds:.1f} s")
    schema = f"{db.TEST_SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    with conn:
        info = db.server_info(conn)
        print(f"  server: {info['version']}")
        print(f"  pgvector: {info['pgvector']}")
        if not check("pgvector installed", info["pgvector"] is not None):
            return False
        ok = True
        db.create_test_schema(conn, schema)
        try:
            db.use_schema(conn, schema)
            ok &= db_checks(conn)
        finally:
            db.drop_test_schema(conn, schema)
        print(f"  throwaway schema {schema} dropped")
    return ok


def run_resume() -> bool:
    print("resume: run only after 10+ minutes with nothing connected")
    print("  check first: RDS > datasheet-rag instance > Monitoring > ServerlessDatabaseCapacity is 0")
    start = time.perf_counter()
    conn, _ = connect_aurora()
    with conn:
        db.server_info(conn)
    print(f"  first answer after {time.perf_counter() - start:.1f} s")
    return True


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("direct")
    sub.add_parser("resume")
    args = parser.parse_args()
    if args.command == "direct":
        ok = run_direct()
    else:
        ok = run_resume()
    print("OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
