"""Call the deployed Lambda function with an API Gateway (HTTP API 2.0) event and print what came back.

Run from datasheet-rag:  python scripts\\invoke_lambda.py check
                         python scripts\\invoke_lambda.py ask "What is the maximum supply voltage of the LM35?"
                         python scripts\\invoke_lambda.py ask "..." --document lm35 --yes

`check` is free: it sends an empty question, which the function refuses with a 400 before it touches the
database (the log line has no connect_ms). `ask` makes paid Bedrock calls from the function: about $0.0023 for a new
question, $0.0003 for one whose vector and rerank scores are already cached. Without --yes it prints that and stops.
The function's cost log is in its /tmp, so the spend is read from the answer (`spent_usd`) and the CloudWatch line.
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from rag import config

FUNCTION = os.environ.get("LAMBDA_FUNCTION", "datasheet-rag")


def event(method: str, path: str, body: dict | None = None) -> dict:
    return {"version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method, "path": path}},
            "headers": {"content-type": "application/json"}, "isBase64Encoded": False,
            **({"body": json.dumps(body)} if body is not None else {})}


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Invoke the deployed Lambda function once.")
    parser.add_argument("route", choices=["check", "ask"])
    parser.add_argument("question", nargs="?")
    parser.add_argument("--document", choices=list(config.DOCUMENTS))
    parser.add_argument("--yes", action="store_true", help="make the paid calls of `ask` without stopping first")
    args = parser.parse_args(argv)

    if args.route == "ask":
        if not args.question:
            parser.error("ask needs a question")
        if not args.yes:
            sys.exit("This makes paid Bedrock calls: about $0.0023 for a new question, $0.0003 for a cached one. "
                     "Run again with --yes.")
        request = event("POST", "/api/ask", {"question": args.question, "document": args.document})
    else:
        request = event("POST", "/api/ask", {"question": ""})

    client = boto3.client("lambda", region_name=config.AWS_REGION)
    start = time.perf_counter()
    response = client.invoke(FunctionName=FUNCTION, Payload=json.dumps(request).encode(), LogType="Tail")
    elapsed = time.perf_counter() - start

    result = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        print(f"Function error ({response['FunctionError']}): {json.dumps(result, indent=2)}")
    else:
        print(f"Status {result['statusCode']}")
        print(json.dumps(json.loads(result["body"]), indent=2, ensure_ascii=False))
    log = base64.b64decode(response.get("LogResult", "")).decode("utf-8", "replace")
    print("\nLog (tail):")
    print("\n".join(line for line in log.splitlines() if line.startswith(("{", "REPORT", "[ERROR]", "Traceback"))))
    print(f"\nRound trip from this machine: {elapsed:.2f} s")


if __name__ == "__main__":
    main(sys.argv[1:])
