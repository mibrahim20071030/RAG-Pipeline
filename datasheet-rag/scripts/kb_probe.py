"""Send one question to the Managed Knowledge Base and print what comes back (Cycle 2 step 8a).

Run from datasheet-rag:  python scripts\\kb_probe.py "What is the supply voltage range of the LM35?"

One paid Retrieve call ($0.001), logged to the cost log. It prints every result's metadata in full, so we can see
whether the KB gives a page number for a PDF chunk and under which key. That decides config.KB_PAGE_SOURCE.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from rag import config, costlog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--results", type=int, default=5)
    args = parser.parse_args()
    if not config.KB_ID:
        raise SystemExit("Set KB_ID in datasheet-rag\\.env first.")

    client = boto3.client("bedrock-agent-runtime", region_name=config.AWS_REGION)
    response = client.retrieve(
        knowledgeBaseId=config.KB_ID,
        retrievalQuery={"text": args.question},
        retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": args.results}},
    )
    costlog.log_cost("kb_retrieve", "bedrock-managed-kb", "query", 1, config.KB_USD_PER_RETRIEVE)

    results = response.get("retrievalResults", [])
    print(f"{len(results)} results (cost ${config.KB_USD_PER_RETRIEVE:.3f})\n")
    for rank, result in enumerate(results, start=1):
        text = result.get("content", {}).get("text", "")
        print(f"--- {rank}. score {result.get('score')}")
        print("location:", json.dumps(result.get("location"), default=str))
        print("metadata:", json.dumps(result.get("metadata"), indent=2, default=str))
        print("text:", text[:200].replace("\n", " "), "...\n")
    keys = sorted({key for result in results for key in (result.get("metadata") or {})})
    print("Metadata keys seen:", ", ".join(keys) or "(none)")


if __name__ == "__main__":
    main()
