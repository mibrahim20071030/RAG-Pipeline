"""Cycle 2 step 1: the Aurora smoke test. No Bedrock calls; the only cost is Aurora capacity time.

Run from datasheet-rag, with the AURORA_* keys in .env (see .env.example):
    python scripts\\aurora_smoke.py direct                    # psycopg + IAM token, runs the real rag/db.py SQL
    python scripts\\aurora_smoke.py data-api                  # RDS Data API with the Secrets Manager secret
    python scripts\\aurora_smoke.py resume --path direct      # after 10+ idle minutes: time to the first answer
"""
import argparse
import functools
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
import botocore
import psycopg

from rag import config, db

CONNECT_ATTEMPTS = 3
DATA_API_RETRY_S = 5
DATA_API_WAIT_S = 60
STRATEGY = "smoke"
# The gateway's certificate chains to Amazon's public root CAs. On Windows, libpq's "system" store is OpenSSL's
# and is empty, so the server is checked against the CA bundle that ships with botocore instead.
CA_BUNDLE = str(Path(botocore.__file__).parent / "cacert.pem")


def need(*names: str) -> None:
    missing = [name for name in names if not getattr(config, name)]
    if missing:
        sys.exit(f"Missing in datasheet-rag\\.env: {', '.join(missing)} (see .env.example)")


def unit_vector(position: int) -> list[float]:
    """A 1024-number vector with a single 1 at `position`, so the expected nearest chunk is known."""
    vector = [0.0] * config.EMBED_DIMENSIONS
    vector[position] = 1.0
    return vector


def connect_direct() -> tuple[psycopg.Connection, float]:
    """A connection through the internet access gateway with a fresh IAM token, retried as AWS advises."""
    need("AURORA_HOST", "AURORA_USER")
    rds = boto3.client("rds", region_name=config.AWS_REGION)
    start = time.perf_counter()
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        # Signed locally from the AWS credentials; no AWS call. Valid for 15 minutes.
        token = rds.generate_db_auth_token(DBHostname=config.AURORA_HOST, Port=config.AURORA_PORT,
                                           DBUsername=config.AURORA_USER, Region=config.AWS_REGION)
        try:
            conn = psycopg.connect(
                host=config.AURORA_HOST, port=config.AURORA_PORT, user=config.AURORA_USER,
                dbname=config.AURORA_DATABASE, password=token, sslmode="verify-full", sslrootcert=CA_BUNDLE,
                connect_timeout=config.AURORA_CONNECT_TIMEOUT_S, autocommit=True,
            )
            return conn, time.perf_counter() - start
        except psycopg.OperationalError as error:
            print(f"  connect attempt {attempt} failed after {time.perf_counter() - start:.1f} s: {error}")
            if attempt == CONNECT_ATTEMPTS:
                raise
    raise AssertionError("unreachable")


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
    conn, seconds = connect_direct()
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


@functools.cache
def data_api_client():
    need("AURORA_CLUSTER_ARN", "AURORA_SECRET_ARN")
    return boto3.client("rds-data", region_name=config.AWS_REGION)


def data_api(statement: str, **params: str) -> tuple[dict, float]:
    """One Data API call, retried while a paused cluster resumes. Returns the answer and the seconds taken."""
    client = data_api_client()
    parameters = [{"name": name, "value": {"stringValue": value}} for name, value in params.items()]
    start = time.perf_counter()
    while True:
        try:
            answer = client.execute_statement(
                resourceArn=config.AURORA_CLUSTER_ARN, secretArn=config.AURORA_SECRET_ARN,
                database=config.AURORA_DATABASE, sql=statement, parameters=parameters,
            )
            return answer, time.perf_counter() - start
        except client.exceptions.DatabaseResumingException:
            waited = time.perf_counter() - start
            if waited > DATA_API_WAIT_S:
                raise
            print(f"  cluster resuming ({waited:.0f} s), retrying in {DATA_API_RETRY_S} s")
            time.sleep(DATA_API_RETRY_S)


def field_value(field: dict):
    """One Data API field, whatever its type key (stringValue, doubleValue, ...); None for a NULL."""
    return None if field.get("isNull") else next(iter(field.values()))


def first_value(answer: dict):
    """The first column of the first row."""
    return field_value(answer["records"][0][0])


def run_data_api() -> bool:
    print("data-api: RDS Data API with the Secrets Manager secret")
    data_api_client()  # built before the timed calls
    answer, seconds = data_api(db.DATA_API_SMOKE["server_info"])
    version, pgvector = (field_value(field) for field in answer["records"][0])
    print(f"  server: {version}  ({seconds:.1f} s)")
    print(f"  pgvector: {pgvector}")
    if not check("pgvector installed", pgvector is not None):
        return False

    ok = True
    v, w = db.vector_text(unit_vector(0)), db.vector_text(unit_vector(1))
    answer, seconds = data_api(db.DATA_API_SMOKE["distance"], v=v, w=w)
    distance = float(first_value(answer))
    ok &= check("1024-number distance", abs(distance - 1.0) < 1e-6, f"{distance:.3f}, {seconds:.2f} s")

    answer, seconds = data_api(db.DATA_API_SMOKE["round_trip"], v=v)
    back = [float(x) for x in first_value(answer).strip("[]").split(",")]
    ok &= check("vector text round trip", back == unit_vector(0), f"{seconds:.2f} s")
    return ok


def run_resume(path: str) -> bool:
    print(f"resume ({path}): run only after 10+ minutes with nothing connected")
    print("  check first: RDS > datasheet-rag instance > Monitoring > ServerlessDatabaseCapacity is 0")
    start = time.perf_counter()
    if path == "direct":
        conn, _ = connect_direct()
        with conn:
            db.server_info(conn)
    else:
        data_api(db.DATA_API_SMOKE["server_info"])
    print(f"  first answer after {time.perf_counter() - start:.1f} s")
    return True


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("direct")
    sub.add_parser("data-api")
    resume = sub.add_parser("resume")
    resume.add_argument("--path", choices=("direct", "data-api"), required=True)
    args = parser.parse_args()
    if args.command == "direct":
        ok = run_direct()
    elif args.command == "data-api":
        ok = run_data_api()
    else:
        ok = run_resume(args.path)
    print("OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
