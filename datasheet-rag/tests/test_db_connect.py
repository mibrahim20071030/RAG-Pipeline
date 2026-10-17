"""Cycle 2 step 2 tests: `db.connect()` picks the local database or Aurora. No database and no AWS call.

psycopg.connect and boto3 are replaced by fakes that record what they were given.
"""
import sys
import types

import psycopg
import pytest

from rag import config, db


class FakeRds:
    def __init__(self):
        self.token_requests = []

    def generate_db_auth_token(self, **kwargs):
        self.token_requests.append(kwargs)
        return f"token-{len(self.token_requests)}"


class FakeConnection:
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def calls(monkeypatch):
    """Records every psycopg.connect call; `calls.fail` makes that many first calls raise OperationalError."""
    record = types.SimpleNamespace(connects=[], fail=0, rds=FakeRds(), sleeps=[], conns=[])

    def fake_connect(**kwargs):
        record.connects.append(kwargs)
        if len(record.connects) <= record.fail:
            raise psycopg.OperationalError("server is resuming")
        record.conns.append(FakeConnection())
        return record.conns[-1]

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)
    monkeypatch.setattr(db.time, "sleep", record.sleeps.append)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name, region_name: record.rds))
    db._rds_client.cache_clear()
    yield record
    db._rds_client.cache_clear()


@pytest.fixture
def aurora(monkeypatch):
    monkeypatch.setattr(config, "DB_TARGET", "aurora")
    monkeypatch.setattr(config, "AURORA_HOST", "example.cluster.us-east-1.rds.amazonaws.com")
    monkeypatch.setattr(config, "AURORA_PORT", 5432)
    monkeypatch.setattr(config, "AURORA_USER", "rag_app")
    monkeypatch.setattr(config, "AURORA_DATABASE", "postgres")
    monkeypatch.setattr(config, "AURORA_CA_BUNDLE", "")


def test_local_target_uses_the_pg_settings(monkeypatch, calls):
    monkeypatch.setattr(config, "DB_TARGET", "local")
    db.connect()
    [kwargs] = calls.connects
    assert kwargs == {"host": config.PG_HOST, "port": config.PG_PORT, "user": config.PG_USER,
                      "dbname": config.PG_DATABASE, "password": config.PG_PASSWORD, "connect_timeout": 5,
                      "autocommit": True}
    assert calls.rds.token_requests == []


def test_aurora_logs_in_with_a_token_and_checks_the_certificate(aurora, calls):
    db.connect()
    [kwargs] = calls.connects
    assert kwargs["host"] == config.AURORA_HOST
    assert kwargs["user"] == "rag_app"
    assert kwargs["dbname"] == "postgres"
    assert kwargs["password"] == "token-1"
    assert kwargs["sslmode"] == "verify-full"
    assert kwargs["sslrootcert"].endswith("cacert.pem")
    assert kwargs["connect_timeout"] == config.AURORA_CONNECT_TIMEOUT_S
    assert kwargs["autocommit"] is True
    [request] = calls.rds.token_requests
    assert request == {"DBHostname": config.AURORA_HOST, "Port": 5432, "DBUsername": "rag_app",
                       "Region": config.AWS_REGION}


def test_aurora_ca_bundle_can_be_set(aurora, calls, monkeypatch):
    monkeypatch.setattr(config, "AURORA_CA_BUNDLE", "/opt/certs/global.pem")
    db.connect()
    assert calls.connects[0]["sslrootcert"] == "/opt/certs/global.pem"


def test_a_failed_connect_is_retried_with_a_new_token_after_a_pause(aurora, calls):
    calls.fail = config.AURORA_CONNECT_ATTEMPTS - 1
    db.connect()
    assert [kwargs["password"] for kwargs in calls.connects] == [
        f"token-{n}" for n in range(1, config.AURORA_CONNECT_ATTEMPTS + 1)]
    assert calls.sleeps == [config.AURORA_RETRY_PAUSE_S] * (config.AURORA_CONNECT_ATTEMPTS - 1)


def test_the_rds_client_is_built_once(aurora, calls, monkeypatch):
    built = []
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(
        client=lambda name, region_name: built.append(name) or calls.rds))
    db.connect()
    db.connect()
    assert built == ["rds"]


def test_the_error_is_raised_after_the_last_attempt(aurora, calls):
    calls.fail = config.AURORA_CONNECT_ATTEMPTS
    with pytest.raises(psycopg.OperationalError):
        db.connect()
    assert len(calls.connects) == config.AURORA_CONNECT_ATTEMPTS
    assert len(calls.sleeps) == config.AURORA_CONNECT_ATTEMPTS - 1


def test_aurora_without_a_host_is_refused(aurora, calls, monkeypatch):
    monkeypatch.setattr(config, "AURORA_HOST", "")
    with pytest.raises(ValueError, match="AURORA_HOST"):
        db.connect()
    assert calls.connects == []


def test_an_unknown_target_is_refused(monkeypatch, calls):
    monkeypatch.setattr(config, "DB_TARGET", "Aurora")
    with pytest.raises(ValueError, match="DB_TARGET"):
        db.connect()
    assert calls.connects == []


def test_schema_goes_first_on_the_search_path(aurora, calls, monkeypatch):
    used = []
    monkeypatch.setattr(db, "use_schema", lambda conn, schema: used.append(schema))
    db.connect("test_abc")
    assert used == ["test_abc"]


def test_the_connection_is_closed_when_the_schema_cannot_be_set(aurora, calls, monkeypatch):
    def fail(conn, schema):
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(db, "use_schema", fail)
    with pytest.raises(psycopg.OperationalError):
        db.connect("test_abc")
    assert calls.conns[0].closed
