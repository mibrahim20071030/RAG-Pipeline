# Cycle 2 step 4: Lambda

The query path runs as the Lambda function `datasheet-rag`. `handler.lambda_handler` puts `rag.api` behind an API Gateway event (HTTP API 2.0 or REST 1.0). Step 5 adds API Gateway in front of it.

## Package

- Built with `python scripts\build_lambda.py`, which writes `build/lambda.zip` (gitignored).
- The zip holds `handler.py`, the 11 query-path modules of `rag/`, and the Linux wheels: psycopg 3.3.6 with psycopg-binary (cp314, manylinux x86_64), and boto3 1.43.111 with botocore 1.43.114, the same versions as `.venv`.
- Size: 22.7 MB zipped, 44.3 MB unzipped. The limits are 50 MB for a console upload and 250 MB unzipped.
- Checked before any upload (free): the zip, unpacked, ran in `public.ecr.aws/lambda/python:3.14` against the local `rag-pg`.
  - `GET /api/documents` returned 200 with the 4 datasheets.
  - An empty question returned 400.
  - Init took 2.3 s inside Docker.
  - The per-request JSON log line was printed.

## Console steps (the human)

Do these in us-east-1. Nothing here is a secret: the function logs in to Aurora with a token made from its role.

### 1. Read the Lambda concurrency quota

Service Quotas → AWS Lambda → **Concurrent executions**. Note the applied value.

- If it is **10**, no reserved concurrency can be set, because Lambda must keep 10 unreserved. The account limit is then the cap, and step 5's API Gateway throttling is the real guard.
- If it is 1,000, set reserved concurrency to **2** in step 3 below.

### 2. Execution role `datasheet-rag-lambda`

IAM → Roles → Create role → Trusted entity: AWS service, use case **Lambda**.

1. Attach the managed policy **AWSLambdaBasicExecutionRole** (CloudWatch logs).
2. Name it `datasheet-rag-lambda` and create it.
3. Open the role → Add permissions → Create inline policy → JSON. Name it `datasheet-rag-runtime`.
4. Copy `<account-id>` and the `cluster-...` resource ID from the dev user's existing `rag-aurora-smoke` policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Models",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0",
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0",
        "arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"
      ]
    },
    {
      "Sid": "Rerank",
      "Effect": "Allow",
      "Action": "bedrock:Rerank",
      "Resource": "*"
    },
    {
      "Sid": "Aurora",
      "Effect": "Allow",
      "Action": "rds-db:connect",
      "Resource": "arn:aws:rds-db:us-east-1:<account-id>:dbuser:<cluster-resource-id>/rag_app"
    }
  ]
}
```

If the dev user's Bedrock policy names `bedrock:Rerank` with a narrower resource that works, use that instead of `*`.

### 3. Function `datasheet-rag`

Lambda → Create function → Author from scratch.

- Name: `datasheet-rag`.
- Runtime: **Python 3.14**.
- Architecture: **x86_64**.
- Execution role: use existing role `datasheet-rag-lambda`.

After it is created:

1. **Code** → Upload from → .zip file → `datasheet-rag\build\lambda.zip`.
2. **Runtime settings** → Edit → Handler: `handler.lambda_handler`.
3. **Configuration → General configuration** → Memory **512 MB**, Timeout **1 min 0 s**.
4. **Configuration → Environment variables**:

| Key | Value |
|---|---|
| `DB_TARGET` | `aurora` |
| `AURORA_HOST` | the same value as in `datasheet-rag\.env` |
| `AURORA_DATABASE` | `postgres` |
| `AURORA_USER` | `rag_app` |
| `RAG_RUNS_DIR` | `/tmp/runs` |
| `BEDROCK_MAX_ATTEMPTS` | `2` |
| `RERANK_MIN_INTERVAL_S` | `0` |
| `AURORA_CONNECT_ATTEMPTS` | `1` |
| `AURORA_CONNECT_TIMEOUT_S` | `22` |
| `BEDROCK_CONNECT_TIMEOUT_S` | `5` |
| `BEDROCK_READ_TIMEOUT_S` | `20` |

5. **Configuration → Concurrency**: reserved concurrency **2**, only if step 1 allows it.

### 4. Let the dev user invoke it

IAM → Users → the dev user → Add permissions → Create inline policy → JSON. Name it `rag-lambda-invoke`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["lambda:InvokeFunction", "lambda:GetFunctionConfiguration"],
      "Resource": "arn:aws:lambda:us-east-1:<account-id>:function:datasheet-rag"
    },
    {
      "Effect": "Allow",
      "Action": "logs:FilterLogEvents",
      "Resource": "arn:aws:logs:us-east-1:<account-id>:log-group:/aws/lambda/datasheet-rag:*"
    }
  ]
}
```

Then tell Claude it is done, and give the concurrency value from step 1.

## Deployed tests (2026-10-17)

Run with `scripts\invoke_lambda.py` from this machine. Spend for the whole step: **$0.0026**.

Console outcome:
- The function `datasheet-rag` is on `python3.14`, x86_64, 512 MB, 60 s timeout, with the 9 environment variables above.
- The account's "Concurrent executions" quota is **10**, so no reserved concurrency could be set. The account limit is the cap for now, and step 5's API Gateway throttling has to be the real guard.
- The first upload did not save: the function held the console's 299-byte stub and failed with "No module named 'handler'". A second upload worked (22,684,909 bytes).

| Test | Result |
|---|---|
| `documents`, cold, with Aurora paused | 200. Init 0.96 s; connect **17.9 s** (Aurora resumed on the first attempt); handler 17.95 s; **19.5 s** round trip. Fits API Gateway's 30 s with 10 s to spare. |
| `documents`, warm | 200. 4.6 ms in the function, 0.21 s round trip. |
| q01, cached vector and rerank scores ($0.0003) | 200. "35 V", citing LM35 page 4 (correct). Search 982 ms (first query on a fresh connection), rerank 9 ms (cache), answer 512 ms; 1.5 s in the function. |
| New question, "What is the output impedance of the LM35?" ($0.0023) | 200, 1.05 s in the function: search 228 ms, rerank 408 ms (real call), answer 409 ms. Aurora caches grew by 1 vector (3,486 → 3,487) and 30 rerank scores (30 → 60), so writes from Lambda work. |
| Memory | 122 MB used of 512 MB. |

Answer quality, not a Lambda fault: the new question was answered "0.5 Ω for a 1-mA load", citing page 13. The datasheet gives 0.1 Ω for a 1-mA load (page 1, features) and a typical 0.5 Ω for the output stage (page 13). The model joined the value of one with the condition of the other. Noted for the deployed evaluation (step 7) and the report's failure analysis.

Lambda's REPORT line shows `Billed Duration` 18.9 s for the cold call, which includes init. Lambda costs are inside the Free plan.

## Code review (1 of 2)

7 of 10 findings fixed:

1. **An open connection kept Aurora awake.** Aurora Serverless v2 does not pause while a user connection is open (AWS docs), and the first handler kept one connection for as long as Lambda kept the container. The handler now opens a connection per request and always closes it. This costs one connect per request, measured after the redeploy.
2. **A named HTTP API stage** (for example `/prod/api/ask`) would have answered 404 to everything. The stage name is now stripped from the path.
3. **Any other failure** (a missing `AURORA_HOST`, no credentials, bad base64) escaped as API Gateway's bare 502 with no log line. It now gives a JSON 500 (400 for bad base64), and the log line is always printed.
4. **Bedrock's 60 s read timeout** was not bounded. New settings `BEDROCK_CONNECT_TIMEOUT_S` and `BEDROCK_READ_TIMEOUT_S` (Lambda: 5 and 20).
5. **Local-machine hints in deployed errors** ("~\.aws\credentials", "Start Docker Desktop"). `config.DEPLOYED` is true when Lambda's own `AWS_LAMBDA_FUNCTION_NAME` is set, and `rag/api.py` then gives neutral messages.
6. **The cost log rule.** When deployed, every cost row is also printed as a `{"cost": ...}` JSON line, so CloudWatch keeps the token and query counts.
7. **Package pins** are now read from `requirements.txt`, so they cannot drift. The Content-Type check is now case-insensitive.

Not changed:

- **Shared request checks with `rag/server.py`.** The two fronts read requests differently (a socket against an event), so the shared part is small.
- **The cost log re-read per request.** A few hundred rows in `/tmp` is negligible.
- **The stale reused connection.** This went away with fix 1.

The step 4 calls before the fix ($0.0026: 1 Titan embed, 1 rerank query, 2 Nova Lite answers) were not in any lasting cost log, so they are recorded here.

## Re-test after the review fixes (2026-10-17)

The zip was re-uploaded (22,685,603 bytes), and the two Bedrock timeout variables were added. Spend: $0.0003.

| Test | Result |
|---|---|
| `documents`, cold (Aurora awake) | Init 1.14 s, connect 3.1 s, 4.8 s round trip. |
| `documents`, warm, new connection per request | Connect 178 ms and 48 ms; 54–185 ms in the function, 0.22–0.41 s round trip. |
| **Aurora pauses** | After 7 minutes with no calls, `documents` took 18.7 s to connect (the resume time), so the cluster had paused and no connection kept it awake. Init 1.21 s, 20.9 s round trip. |
| q01 (cached, $0.0003) | "35 V", LM35 page 4. Connect 229 ms, search 1,169 ms, rerank 10 ms (cache), answer 586 ms; 2.0 s in the function. |
| Cost lines in CloudWatch | Two `{"cost": ...}` lines for the q01 answer: Nova Lite 4,846 input and 21 output tokens. |

Step 4 spend in total: **$0.0029** (one embed, one rerank query, three Nova Lite answers).

## For step 5

- The worst case seen is about 20 s, for a cold function plus an Aurora resume. A longer resume (AWS says 30 s or more after a day idle) would pass 30 s. The function then still finishes and the next call is warm, but the page gets a gateway timeout and must ask the user to retry.
- With the concurrency limit at 10 and no reserved concurrency, the throttle in API Gateway is the only cap on spend per second.
