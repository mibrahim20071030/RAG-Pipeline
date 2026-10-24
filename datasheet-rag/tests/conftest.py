"""Fixtures shared by the tests that need the local Postgres (they skip themselves without it), and `make_pdf`, a small
real PDF built in memory for the upload tests (step 9a)."""
import uuid

import psycopg
import pytest

from rag import config, db


@pytest.fixture(autouse=True)
def no_upload_resources(monkeypatch):
    """The step 9a resources start unset in every test, whatever datasheet-rag\\.env holds; a test that needs one
    sets it."""
    for name in ("KB_CURATED_DATA_SOURCE_ID", "KB_UPLOAD_DATA_SOURCE_ID", "UPLOAD_BUCKET"):
        monkeypatch.setattr(config, name, "")


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


def _pdf_text(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    """A PDF with one page per string, each line of it set in Helvetica: enough for pdfplumber to read real text."""
    count = len(pages)
    page_ids = [4 + 2 * i for i in range(count)]
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {count} >>".encode(),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for page_id, text in zip(page_ids, pages, strict=True):
        stream = "BT /F1 11 Tf 14 TL 72 720 Td " + " ".join(f"({_pdf_text(line)}) Tj T*" for line in text.split("\n"))
        stream += " ET"
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {page_id + 1} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode())
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out
