"""The Knowledge Base pipeline (Cycle 2 step 8): a Bedrock Managed KB finds the passages, our generate() answers.

AWS parses, chunks, embeds, searches (hybrid) and reranks; one Retrieve call gives the top passages. They are turned
into the same hit shape as our search, so `generate.generate` writes and checks the answer exactly as it does for
our pipeline, and the comparison measures retrieval only. Nothing here touches a database.

Run from datasheet-rag:  python -m rag.kb "What is the supply voltage range of the LM35?"  [--document lm35]

A question costs one Retrieve ($0.001) and one Nova Lite answer (about $0.0003). It does not ask first: what it spent
is printed after the answer, and every call is in runs/cost_log.csv.
"""
import argparse
import sys
import time
import traceback
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from rag import config, costlog, generate, pipeline

KB_MODEL = "bedrock-managed-kb"  # the model_id column of the cost log for Retrieve calls
_client = None
_agent_client = None
# Statuses of an ingestion job (GetIngestionJob) after which it does not change any more.
JOB_DONE, JOB_FAILED = ("COMPLETE",), ("FAILED", "STOPPED")


def _bedrock():
    global _client
    if _client is None:
        # Retrieve is on the agent runtime endpoint, like Rerank.
        _client = boto3.client("bedrock-agent-runtime", region_name=config.AWS_REGION,
                               config=Config(retries={"max_attempts": config.BEDROCK_MAX_ATTEMPTS, "mode": "adaptive"},
                                             connect_timeout=config.BEDROCK_CONNECT_TIMEOUT_S,
                                             read_timeout=config.BEDROCK_READ_TIMEOUT_S))
    return _client


def _agent():
    """The build-time endpoint (bedrock-agent), where ingestion jobs are started and read."""
    global _agent_client
    if _agent_client is None:
        _agent_client = boto3.client("bedrock-agent", region_name=config.AWS_REGION,
                                     config=Config(retries={"max_attempts": config.BEDROCK_MAX_ATTEMPTS,
                                                            "mode": "adaptive"},
                                                   connect_timeout=config.BEDROCK_CONNECT_TIMEOUT_S,
                                                   read_timeout=config.BEDROCK_READ_TIMEOUT_S))
    return _agent_client


def search_config(document: str | None, k: int) -> dict:
    """The retrievalConfiguration of a Managed KB: k results after AWS's own reranker, one datasheet or all.

    `document` is a datasheet or an upload (upload-<id>), the name our .metadata.json sidecar put on every chunk of
    that PDF. With none, the search is kept to the curated data source (step 9a), so no visitor's upload is in it."""
    managed = {"numberOfResults": k, "rerankingModelType": "MANAGED"}
    if document is not None:
        managed["filter"] = {"equals": {"key": config.KB_DOCUMENT_KEY, "value": document}}
        if config.KB_CURATED_DATA_SOURCE_ID and not document.startswith(config.UPLOAD_PREFIX):
            # A reviewer's note (step 9b) names its datasheet too: only the curated data source is a datasheet.
            managed["filter"] = {"andAll": [
                managed["filter"],
                {"equals": {"key": config.KB_DATA_SOURCE_KEY, "value": config.KB_CURATED_DATA_SOURCE_ID}}]}
    elif config.KB_UPLOAD_DATA_SOURCE_ID and not config.KB_CURATED_DATA_SOURCE_ID:
        # With an uploads data source, the curated filter is all that keeps visitors' PDFs out of this search.
        raise RuntimeError("KB_CURATED_DATA_SOURCE_ID is not set, so a search of all datasheets would also read "
                           "visitors' uploads. Set it to the datasheets data source's ID.")
    elif config.KB_CURATED_DATA_SOURCE_ID:
        managed["filter"] = {"equals": {"key": config.KB_DATA_SOURCE_KEY, "value": config.KB_CURATED_DATA_SOURCE_ID}}
    return {"managedSearchConfiguration": managed}


def start_ingestion(upload_id: str, client=None) -> str:
    """Start a sync of the uploads data source, which indexes the files added under its prefix (and drops the ones
    deleted). Returns the job's id. Free apart from the KB's storage."""
    response = (client or _agent()).start_ingestion_job(
        knowledgeBaseId=config.KB_ID, dataSourceId=config.KB_UPLOAD_DATA_SOURCE_ID,
        description=f"upload {upload_id}"[:200])
    return response["ingestionJob"]["ingestionJobId"]


def ingestion_status(job_id: str, client=None) -> tuple[str, int]:
    """The job's status (STARTING, IN_PROGRESS, COMPLETE, FAILED, STOPPING, STOPPED) and its count of failed
    documents."""
    job = (client or _agent()).get_ingestion_job(
        knowledgeBaseId=config.KB_ID, dataSourceId=config.KB_UPLOAD_DATA_SOURCE_ID, ingestionJobId=job_id,
    )["ingestionJob"]
    return job.get("status", ""), int((job.get("statistics") or {}).get("numberOfDocumentsFailed") or 0)


def _source_document(uri: str | None) -> str | None:
    """The datasheet name from a source address (https://<bucket>.s3.amazonaws.com/kb/lm35.pdf -> lm35)."""
    if not uri:
        return None
    return PurePosixPath(unquote(urlparse(uri).path)).stem or None


def to_hit(result: dict) -> dict:
    """One Retrieve result in the hit shape of our search: document and PDF page, text, score.

    The datasheet comes from our sidecar, else from the file name; the page from AWS's page key. A result missing
    either is kept with None, never dropped: it can still be read, it just cannot be cited to a page."""
    metadata = result.get("metadata") or {}
    uri = metadata.get("_source_uri") or (result.get("location") or {}).get("s3Location", {}).get("uri")
    page = metadata.get(config.KB_PAGE_KEY)
    return {
        "id": metadata.get("_chunk_id"),
        "document": metadata.get(config.KB_DOCUMENT_KEY) or _source_document(uri),
        "page": int(page) if isinstance(page, (int, float)) else None,
        "page_label": None,
        "section": metadata.get("_table_section_title"),
        "kind": "table" if metadata.get("_table_id") else "text",
        "text": (result.get("content") or {}).get("text", ""),
        "score": result.get("score"),
    }


def retrieve(question: str, document: str | None = None, k: int | None = None, client=None) -> list[dict]:
    """The KB's top k passages for a question, best first, as hits. One paid Retrieve call, logged."""
    if not config.KB_ID:
        raise RuntimeError("KB_ID is not set: put the Knowledge Base's ID in datasheet-rag\\.env or the environment.")
    response = (client or _bedrock()).retrieve(
        knowledgeBaseId=config.KB_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration=search_config(document, k or config.TOP_K),
    )
    costlog.log_cost("kb_retrieve", KB_MODEL, "query", 1, config.KB_USD_PER_RETRIEVE)
    return [to_hit(result) for result in response.get("retrievalResults", [])]


def _ms_since(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def answer_question(question: str, document: str | None = None, retrieve_client=None, generate_client=None,
                    notes_fn=None) -> dict:
    """Answer one question from the KB's passages. The same result shape as `pipeline.answer_question`, with
    `timings_ms` {"retrieve", "generate"}, plus "notes" when notes are on.

    With notes on (step 9b), the published reviewers' notes that match the question go first: `notes_fn(question,
    document)` gives them (default `rag.corrections.kb_match`: one Titan embed of the question, only while a note
    that could fit is published)."""
    start = time.perf_counter()
    passages = retrieve(question, document=document, client=retrieve_client)
    timings = {"retrieve": _ms_since(start)}

    if config.CORRECTIONS_ENABLED:
        from rag import corrections

        start = time.perf_counter()
        try:
            notes = (notes_fn or corrections.kb_match)(question, document)
        except (ClientError, BotoCoreError, RuntimeError):
            # The list or the embed did not answer: the answer goes on without notes, as on our side.
            traceback.print_exc()
            notes = []
        passages = notes + passages
        timings["notes"] = _ms_since(start)

    start = time.perf_counter()
    result = generate.generate(question, passages, client=generate_client)
    timings["generate"] = _ms_since(start)

    return {"question": question, **result, "passages": passages, "timings_ms": timings}


def warnings(result: dict) -> list[str]:
    """As `pipeline.warnings`, for KB answers: no passages, passages without a page, invalid labels, a cut-off."""
    found = []
    if result.get("passages") == []:
        found.append("The Knowledge Base returned no passages. Check that its data source is synced.")
    unpaged = sum(1 for hit in result.get("passages", []) if hit["page"] is None or hit["document"] is None)
    if unpaged:
        found.append(f"{unpaged} passage(s) came back without a datasheet or page, so they cannot be cited to a page.")
    return found + pipeline.answer_warnings(result)


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Answer a question from the Knowledge Base, with page citations.")
    parser.add_argument("question", help="the question, in quotes")
    parser.add_argument("--document", choices=list(config.DOCUMENTS), help="search only this datasheet")
    args = parser.parse_args(argv)

    logged_before = costlog.row_count()
    try:
        result = answer_question(args.question, document=args.document)
    except (ClientError, BotoCoreError) as error:
        sys.exit(f"AWS refused a call: {error}\nThe dev user needs bedrock:Retrieve on the Knowledge Base "
                 "(inline policy rag-kb-query) and bedrock:InvokeModel on Nova Lite.")
    except RuntimeError as error:
        sys.exit(f"A step stopped without a usable result: {error}")

    print(pipeline.format_answer(result, warnings_fn=warnings))
    timings = result["timings_ms"]
    print(f"\nManaged Knowledge Base, top {config.TOP_K}, {config.GENERATE_MODEL_ID}. "
          f"Retrieve {timings['retrieve']:.0f} ms, answer {timings['generate']:.0f} ms.")
    spent = costlog.spent_since(logged_before)
    print(f"Spent: ${spent:.4f} (cost log {config.COST_LOG})" if spent is not None
          else f"Could not read the cost log. Check {config.COST_LOG} by hand.")


if __name__ == "__main__":
    main(sys.argv[1:])
