"""Fixtures shared by the tests that need the local Postgres. They skip themselves without it."""
import uuid

import psycopg
import pytest

from rag import config, db


@pytest.fixture(scope="session")
def database():
    """Skips the tests that need Postgres when it cannot be reached. Asked once, and only when one is run.

    The database is the one `DB_TARGET` names, so `DB_TARGET=aurora` runs these tests on Aurora (opt-in, costs
    Aurora time). Without credentials or settings for it, the tests skip.
    """
    import botocore.exceptions

    try:
        db.connect().close()
    except (psycopg.OperationalError, ValueError, botocore.exceptions.BotoCoreError) as error:
        if config.DB_TARGET == "local":
            pytest.skip("no local Postgres (start it with: docker start rag-pg)")
        pytest.skip(f"database {config.DB_TARGET!r} not reachable: {error}")


@pytest.fixture
def conn(database):
    """A connection to a throwaway schema with the tables created. The real rows are never touched."""
    schema = f"{db.TEST_SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    with db.connect() as admin:
        db.create_test_schema(admin, schema)
        try:
            with db.connect(schema) as connection:
                db.create_schema(connection)
                yield connection
        finally:
            db.drop_test_schema(admin, schema)
