# Cycle 2 step 9a: PDF upload on both pipelines

Started 2026-10-24. Plan approved by the human the same day. Limits: 10 MB, 100 pages, 300 chunks, 5 uploads a day,
kept 24 hours. The KB side uses a second data source, `uploads`, in the same Managed KB.

## 9a-1 Verified in the AWS docs (2026-10-24)

- **Managed KB filters:** `equals`, `notEquals`, `in`, `notIn` and the range operators, combined with `andAll` or
  `orAll` (up to 5). `startsWith` and `stringContains` are not supported.
  - Reserved metadata keys of a managed KB start with `_`, for example `_data_source_id` and `_source_uri`.
  - A filter on `_data_source_id` is not shown in the docs, so the probe below tests it.
  - Source: "Configure and customize queries for managed knowledge bases" in the Bedrock user guide.
- **Managed KB S3 data source:**
  - inclusion and exclusion prefixes and patterns;
  - a `<file>.metadata.json` sidecar (at most 10 KB) next to each file;
  - incremental syncs for added, updated and deleted files.
  - Source: the "Amazon S3" page for managed KB data sources.
- **Ingestion jobs:**
  - `StartIngestionJob` takes the KB ID and the data source ID, and answers with `ingestionJob.ingestionJobId`,
    `status` and `statistics` (`numberOfDocumentsFailed` and others).
  - It answers `ConflictException` (409) when it cannot start; the code waits 20 s and tries again.
  - Statuses (read with `GetIngestionJob`): STARTING, IN_PROGRESS, COMPLETE, FAILED, STOPPING, STOPPED.
- **S3 presigned POST:**
  - The policy can hold `content-length-range` and a fixed `Content-Type`; a field that is in a condition must also
    be in the form's fields.
  - S3 refuses an upload that breaks any condition.
  - A browser POST to the bucket needs a CORS rule on the bucket.
- **Lambda:** pdfplumber's Linux wheels (pypdfium2, Pillow) exist for CPython 3.14. `build\ingest.zip` is 46.5 MB,
  under the console's 50 MB limit for a direct upload.

## Console steps for the human

All in us-east-1. First run `python scripts\build_lambda.py` from `datasheet-rag` (both zips).

1. **Upload bucket.** S3 → Create bucket.
   - General purpose; name `datasheet-rag-uploads-<something unique>`. Block all public access on; SSE-S3.
   - Permissions → CORS → Edit, and paste the JSON below. Put `SITE_URL` from `.env` in place of `<SITE_URL>`,
     with `https://` and no slash at the end:
     ```json
     [{"AllowedOrigins": ["<SITE_URL>"], "AllowedMethods": ["POST"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 600}]
     ```
   - Management → Create lifecycle rule:
     - name `delete-after-a-day`; scope "Apply to all objects";
     - action "Expire current versions of objects", after **1** day.
2. **KB data source.** Amazon Bedrock AgentCore → Knowledge Base → `datasheet-rag-kb` → Data sources → Add.
   - Name `uploads`, type Amazon S3, this account, the new bucket's URI.
   - S3 prefix filter patterns: include prefix `kb/`.
   - Parser, chunking and media settings the same as `datasheets` (managed parser, default chunking, media off).
   - Deletion safeguard off.
   - Do **not** sync it now.
   - Note the data source IDs of `uploads` and of `datasheets`. Both are shown on each data source's page, or in
     CloudShell: `aws bedrock-agent list-data-sources --knowledge-base-id <KB_ID>`.
3. **Ingest role.** IAM → Roles → Create role → AWS service, Lambda.
   - Attach `AWSLambdaBasicExecutionRole`. Name it `datasheet-rag-ingest`.
   - Add an inline policy `datasheet-rag-ingest-runtime` (JSON). Take `<account-id>` and `<cluster-resource-id>` from
     the policy `datasheet-rag-runtime`, `<TABLE_ARN>` from `rag-usage-table`, and `<KB_ARN>` from `rag-kb-query`:
     ```json
     {"Version": "2012-10-17", "Statement": [
       {"Effect": "Allow", "Action": "bedrock:InvokeModel",
        "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"},
       {"Effect": "Allow", "Action": "rds-db:connect",
        "Resource": "arn:aws:rds-db:us-east-1:<account-id>:dbuser:<cluster-resource-id>/rag_app"},
       {"Effect": "Allow", "Action": ["s3:GetObject", "s3:DeleteObject"],
        "Resource": "arn:aws:s3:::<UPLOAD_BUCKET>/incoming/*"},
       {"Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::<UPLOAD_BUCKET>/kb/*"},
       {"Effect": "Allow", "Action": "bedrock:StartIngestionJob", "Resource": "<KB_ARN>"},
       {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem",
                                      "dynamodb:DeleteItem"], "Resource": "<TABLE_ARN>"}]}
     ```
4. **Ingest function.** Lambda → Create function `datasheet-rag-ingest`.
   - Settings: python3.14, x86_64, existing role `datasheet-rag-ingest`.
   - Code: upload `build\ingest.zip` and click **Save**. Check that the code size is about 46.5 MB.
   - Runtime settings: handler `handler.ingest_handler`.
   - Configuration → General: memory **1024 MB**, timeout **15 min**.
   - Configuration → Asynchronous invocation: retry attempts **0**.
   - Environment variables:
     - copy these from `datasheet-rag` as they are: `DB_TARGET`, `AURORA_HOST`, `AURORA_DATABASE`, `AURORA_USER`,
       `RAG_RUNS_DIR`, `USAGE_TABLE`, `KB_ID`;
     - add `UPLOAD_BUCKET` = the bucket name, `KB_UPLOAD_DATA_SOURCE_ID` = the `uploads` ID and
       `KB_CURATED_DATA_SOURCE_ID` = the `datasheets` ID. Without the curated ID, no upload is sent to the KB:
       its filter is what keeps uploads out of everyone else's searches.
   - Leave out `AURORA_CONNECT_ATTEMPTS` and `AURORA_CONNECT_TIMEOUT_S`: with no API Gateway in front, the defaults
     (3 tries of 60 s) wait out a paused Aurora.
5. **S3 event.** The upload bucket → Properties → Event notifications → Create.
   - Name `ingest`; prefix `incoming/`; suffix `.pdf`; event "All object create events".
   - Destination: Lambda function `datasheet-rag-ingest`.
6. **API function.**
   - **Role:** `datasheet-rag-lambda` → add an inline policy `rag-uploads` (JSON):
     ```json
     {"Version": "2012-10-17", "Statement": [
       {"Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::<UPLOAD_BUCKET>/incoming/*"},
       {"Effect": "Allow", "Action": "bedrock:GetIngestionJob", "Resource": "<KB_ARN>"},
       {"Effect": "Allow", "Action": ["dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:GetItem",
                                      "dynamodb:UpdateItem"], "Resource": "<TABLE_ARN>"}]}
     ```
   - **Code:** upload `build\lambda.zip` and click Save. Check that the code size is about 22.8 MB.
   - **Environment variables:** add `UPLOAD_BUCKET`, `UPLOAD_DAILY_LIMIT` = `5`, `KB_UPLOAD_DATA_SOURCE_ID` and
     `KB_CURATED_DATA_SOURCE_ID` (the `datasheets` ID).
7. **API Gateway.** `datasheet-rag-api` → Routes → Create:
   - `POST /api/uploads` and `GET /api/uploads/{id}`, each attached to the `datasheet-rag` integration.
   - Then, in CloudShell, set their throttles as **one line**. Put the API ID in place of `<API-ID>`; the line keeps
     the `/api/ask` setting:
     ```
     aws apigatewayv2 update-stage --region us-east-1 --api-id <API-ID> --stage-name '$default' --route-settings '{"POST /api/ask":{"ThrottlingBurstLimit":3,"ThrottlingRateLimit":0.05},"POST /api/uploads":{"ThrottlingBurstLimit":2,"ThrottlingRateLimit":0.05},"GET /api/uploads/{id}":{"ThrottlingBurstLimit":5,"ThrottlingRateLimit":1}}'
     ```
8. **Page.** Upload `web\index.html` to the page bucket (replace). Then CloudFront → `datasheet-rag-web` →
   Invalidations → `/index.html`.
9. **`.env`.** Add `UPLOAD_BUCKET`, `KB_UPLOAD_DATA_SOURCE_ID` and `KB_CURATED_DATA_SOURCE_ID`.
10. Tell Claude.

If a step is refused or a screen looks different from this list, copy the message or describe the screen to Claude.

## Claude's steps after the console work

1. Add the upload columns to Aurora once: `create_schema` with `DB_TARGET=aurora`. Free, but it wakes Aurora.
2. Probe the curated filter: one `Retrieve` with the `_data_source_id` filter ($0.001). It must return the same
   passages as without the filter.
3. Free checks through `SITE_URL`:
   - `call_api.py --base site limits`;
   - an S3 POST of more than 10 MB is refused;
   - `DEMO_ENABLED=false` gives 503 on the upload routes.
4. Paid, after the human's yes (about $0.02):
   - one upload of a datasheet that is not one of the four, until it is ready on both pipelines;
   - 3 questions on each pipeline;
   - the 49 curated questions through KB `Retrieve` with the new filter, compared with the step 8 rows ($0.049).

## Results

### Console work (the human, 2026-10-24)

All 10 steps done. Two snags:
- `KB_ID` in `.env` held the KB's ARN, which `Retrieve` accepts but the ingestion job calls do not. Changed to the
  10-character ID.
- The step 7 `update-stage` line was refused (`NotFoundException ... GET /api/documents`): the stage still had the
  throttle of the route removed in step 7b. Fixed with `aws apigatewayv2 delete-route-settings ... --route-key
  'GET /api/documents'`, then the line ran.

### Claude's checks (2026-10-24)

1. **Aurora migration:** `create_schema` on Aurora (connect 18.5 s, Aurora was paused). `documents` has `source`
   (default `'curated'`), `title` and `expires_at`; the 4 datasheets read `curated`; still 797 chunks.
2. **Curated filter probe** (3 `Retrieve`, $0.003), "What is the supply voltage range of the LM35?":
   - with the `_data_source_id` = `datasheets` filter: the same 10 passages in the same order as without a filter;
   - filtered on the `uploads` source (empty then): 0 passages, so the filter is applied, not ignored.
3. **Free deployed checks** through `SITE_URL`:
   - `limits`: all 15 refused as expected (404 for unknown routes and the removed `/api/documents`; 400 for an empty
     question, an unknown datasheet or pipeline, 501 characters, a `.txt` name; 413 for a 20 KB body and an 11 MB
     size; 415 for a body that is not JSON; 404 for an unknown or malformed upload id, also when asked about).
   - `page`: 200 with the title; `http://` gives 301 to `https://`.
   - S3 size condition: a real presigned POST with an 11 MB file was refused by S3 with 400 `EntityTooLarge`
     (`MaxSizeAllowed` 10485760). The record stayed `waiting`; it used 1 of the day's 5 upload places.
   - `DEMO_ENABLED=false` on the upload routes: not run (needs the human to change the Lambda variable); the code
     path is covered by `tests/test_handler.py`.
4. **The 49 curated questions through KB `Retrieve` with the filter** (49 calls, $0.049; retrieval only, no answers;
   rows kept outside the repo so `runs/eval/kb_managed.csv` stays the step 8 record), against the step 8 lists:
   - 45 of 49 identical (same 10 `document:page` in the same order).
   - The other 4 (q20, q24, q34, q42) differ only at ranks 5 to 10, always among the four datasheets. Asked again
     with and without the filter ($0.008): unfiltered they also differ from step 8 (3 of 4), and q24 now matches
     step 8 exactly, so this is run-to-run variation of AWS's managed search, not the filter.
   - The first correct passage is at the same rank as in step 8 for all 4, so R@k and MRR are unchanged on all 49.
5. **One upload end to end** through `SITE_URL` (`call_api.py upload`): `agentic-ai-foundations.pdf` (AWS
   Prescriptive Guidance "Foundations of agentic AI on AWS", 37 pages, 1.2 MB, 59 passages; not a datasheet, chosen by
   the human). Times are UTC.
   - 17:00:46 place given (2.8 s); S3 POST 204 in 0.8 s.
   - 17:01:00 ingest started; embedded in 3 batches of 20, about 30 s apart (the 40-a-minute pacing).
   - 17:02:38 **ours ready** (under 2 minutes); KB sync started.
   - 17:06:06 **KB ready** (sync about 3.5 minutes).
6. **Questions about the upload** (`upload_id`), both pipelines:

   | Question | Custom pipeline | AWS managed |
   |---|---|---|
   | Who introduced the actor model, and in what year? (page 8) | "Carl Hewitt ... 1973", pages 8 and 13 | "Carl Hewitt ... 1973", page 8 |
   | What did Oliver Selfridge introduce in 1959? (page 18) | demons, page 18 | autonomous software entities, pages 18 and 3 (contents) |
   | What is the supply voltage range of the LM35? (not in the upload) | refused | refused |

   The LM35 question is refused although the LM35 datasheet is in the same index: a question about an upload searches
   only that upload. About $0.0022 a question on ours, $0.0012 on the KB; 1.0–2.0 s each.
7. **Isolation the other way:** the actor-model question **without** `upload_id` was refused on both pipelines (ours
   in its own words, "Sorry, I cannot find a relevant answer in the given passages.", which `is_not_contained`
   detects), so a visitor's upload does not reach the curated search.

Spend of the checks: about $0.08 (Retrieve $0.060, 8 asks $0.014, the upload's embedding about $0.0004).
