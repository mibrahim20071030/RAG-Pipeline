# Cycle 2 step 5: API Gateway

An HTTP API named `datasheet-rag-api` puts the Lambda function `datasheet-rag` on a public HTTPS URL. Anyone can call that URL, so the API has these guards:

- **Throttling in API Gateway.** `POST /api/ask` is limited to one request every 20 s, with a burst of 3. That matches the account's Cohere Rerank quota of 3 a minute.
- **A daily cap in our own code.** After 300 questions in a UTC day, the API answers 429 until midnight UTC.
- **Limits already in the code.** A question can have at most 500 characters, a request body at most 10 KB, and an answer at most 512 output tokens.
- **CORS.** Only the page's origin may call the API from a browser.

## Why these choices

Each point was checked in the AWS docs on 2026-10-17.

- **HTTP API, not REST API.** An HTTP API sends the payload 2.0 event, which `handler.py` already reads. It answers CORS preflights itself and costs less.
  - Throttling is set per stage and per route, with a token bucket. The rate is a decimal number, so 0.05 a second is allowed.
  - The integration timeout is at most **30 s** and cannot be raised.
- **The daily cap is in Aurora, not in API Gateway.**
  - HTTP APIs have no usage plans and no daily quotas.
  - REST usage-plan quotas need an API key, and AWS says not to rely on them to control costs.
  - So `/api/ask` counts each valid question in the table `api_usage`, using one atomic statement (`db.take_daily_ask`).
  - A request rejected for a bad question, an unknown datasheet or a bad body is never counted.
  - The limit is the Lambda environment variable `API_DAILY_ASK_LIMIT`. Locally it is unset, which means no cap.
- **Throttled requests cost nothing.** API Gateway refuses them before Lambda runs. They therefore appear only in the API's access log, not in the Lambda log.
- **Worst-case spend.** It is about 300 × $0.0023 = **$0.70 a day**. Without the cap, the throttle alone would allow about 4,300 questions a day (about $10).

## Console steps (the human, us-east-1)

### 1. Lambda: new code and the cap

Lambda → `datasheet-rag`:

1. **Code** → Upload from → .zip file → `datasheet-rag\build\lambda.zip`. Click **Save**, then check that the code size shown is about 22.7 MB. The first upload in step 4 did not save.
2. **Configuration → Environment variables** → Edit → Add `API_DAILY_ASK_LIMIT` = `300`. Save.

The `api_usage` table is already in Aurora. Claude created it with `db.create_schema`, which is free and safe to rerun.

### 2. Create the HTTP API

Check that the region selector at the top right says **N. Virginia (us-east-1)**. Open API Gateway → **Create API**. On the **HTTP API** card, click **Build**. Do not use a "quick create" path: AWS manages a quick-create `$default` stage, and it cannot be changed.

A four-page wizard opens.

**Page 1: Create and configure integrations**

1. Under **Integrations**, click **Add integration**.
2. In the first dropdown, choose **Lambda**.
3. **AWS Region**: `us-east-1`.
4. **Lambda function**: choose `datasheet-rag`. When you click the box, it lists the functions; you can also type the name.
5. **Version**: **2.0**. This is the payload format version. It is usually already 2.0; if the setting is hidden, it is the default.
6. **API name**: `datasheet-rag-api`.
7. **IP address type**, if shown: leave **IPv4**.
8. Click **Next**.

**Page 2: Configure routes**

The wizard suggests one route, usually **ANY** `/datasheet-rag`. Change it to the first route below, then add the second:

| Method | Resource path | Integration target |
|---|---|---|
| `POST` | `/api/ask` | `datasheet-rag` |
| `GET` | `/api/documents` | `datasheet-rag` |

1. In the suggested row, set **Method** to `POST`, **Resource path** to `/api/ask` and **Integration target** to `datasheet-rag`.
2. Click **Add route**. In the new row, set `GET`, `/api/documents` and `datasheet-rag`.
3. Check that there are exactly these two rows, with no `ANY` row left. Any other path then gets API Gateway's own 404, and Lambda never starts.
4. Click **Next**.

**Page 3: Define stages**

1. Leave the one stage as it is: **Stage name** `$default`, with **Auto-deploy** turned on. Auto-deploy means every later change (throttling, CORS) goes live without a separate deploy.
2. Click **Next**.

**Page 4: Review and create**

1. Check the summary: one Lambda integration (`datasheet-rag`), the two routes, and the `$default` stage with auto-deploy.
2. Click **Create**.

The API's page then opens. Its **Invoke URL** is shown under **Stages** (step 7 below).

### 3. Check the invoke permission

Lambda → `datasheet-rag` → Configuration → **Permissions** → Resource-based policy statements. There should be a statement that allows `apigateway.amazonaws.com` to invoke the function, with a source ARN for this API. The console adds it when it creates the integration. If it is missing, the API answers 500 "Internal Server Error" and nothing appears in the Lambda log.

### 4. Throttling

1. API Gateway → `datasheet-rag-api` → **Protect → Throttling** → stage `$default` → **Default route throttling** → Edit: Burst limit **5**, Rate limit **1**. Save.
2. The two route limits cannot be created in the console. The page says "Use the CLI or an SDK to create custom route settings" (seen on 2026-10-17). Create them in **CloudShell**, the `>_` icon in the console. It runs as your console login; the dev key can only call Bedrock. Put the API ID from the invoke URL in place of `<API-ID>`:

   ```bash
   aws apigatewayv2 update-stage --region us-east-1 --api-id <API-ID> --stage-name '$default' --route-settings '{"POST /api/ask":{"ThrottlingBurstLimit":3,"ThrottlingRateLimit":0.05},"GET /api/documents":{"ThrottlingBurstLimit":5,"ThrottlingRateLimit":1}}'
   ```

   Paste it as **one line**. A version split over two lines with a trailing `\` failed when pasted into CloudShell ("Unknown options: {...}"): the line join broke, so the JSON was not read as the value of `--route-settings`.

3. Check: `aws apigatewayv2 get-stage --region us-east-1 --api-id <API-ID> --stage-name '$default' --query RouteSettings`. The output: `RouteSettings` should show `POST /api/ask` with burst 3 and rate 0.05, and `GET /api/documents` with 5 and 1. After a refresh, both routes are listed under "Route settings on stage" and can be edited there.

### 5. CORS

API Gateway → `datasheet-rag-api` → **Develop → CORS** → Configure:

| Setting | Value |
|---|---|
| Access-Control-Allow-Origin | `http://localhost:8000` (a placeholder: step 6 replaces it with the S3 page origin) |
| Access-Control-Allow-Methods | `GET`, `POST` |
| Access-Control-Allow-Headers | `content-type` |
| Access-Control-Expose-Headers | empty (the page reads only the status and the JSON body; `Content-Type` is always readable) |
| Access-Control-Max-Age | `300` |
| Allow credentials | off |

Save. With CORS set, API Gateway answers `OPTIONS` itself and ignores any CORS headers sent by Lambda.

### 6. Access logs

1. CloudWatch → Log groups → Create log group `datasheet-rag-api-access`, retention **1 week**.
2. API Gateway → `datasheet-rag-api` → **Monitor → Logging** → stage `$default` → Edit → turn on access logging:
   - Destination: the ARN of that log group, **without** the `:*` at the end.
   - Log format: **JSON**, using the default template the console offers.

### 7. Hand over

1. API Gateway → `datasheet-rag-api` → **Stages** → `$default`. Copy the **Invoke URL**, for example `https://abc123.execute-api.us-east-1.amazonaws.com`.
2. Add it to `datasheet-rag\.env` as `API_URL=https://...` (no trailing slash). The URL is not secret, but like the Aurora endpoint it stays out of the repo.
3. Tell Claude it is done.

## Tests (Claude, with `scripts\call_api.py`), 2026-10-17

| Test | Expected | Result |
|---|---|---|
| `documents` | 200, 4 datasheets | 200, 4 datasheets. `pdf_url` is null because the PDFs are not in the cloud yet (step 6). |
| `documents` after Aurora has paused | 200 in about 20 s, under the 30 s timeout | The first call of the session took **19.65 s** (Aurora resume): 200. |
| `limits`: unknown route, `GET /api/ask` | API Gateway 404, no Lambda log line | Both 404 `{"message":"Not Found"}` in 0.13–0.18 s, with no line in the Lambda log. |
| `limits`: 501-character question, 20 KB body, form body | 400, 413, 415 from Lambda | 400, 413 and 415 with our messages. |
| `cors http://localhost:8000` | allow-origin header present | First try: the preflight answered 204 with **no** CORS headers for any origin, because the values were typed but not added with **Add**, so the allow list was saved empty. After the fix: the preflight returns allow-origin `http://localhost:8000`, allow-methods `GET,POST`, allow-headers `content-type`, max-age 300, and the GET carries the allow-origin. |
| `cors https://evil.example` | no allow-origin header | None on the preflight or the GET. |
| `throttle --route ask` (empty questions, free) | about 3 × 400, then 429 | **Loose.** 8 requests in a row all got through (400). Over 60 s at one request every 2 s, 20 of 28 got through and 8 got 429; about 6 were expected. |
| `throttle --route documents --count 12` | some 429s | Three runs: 10 × 200 and 2 × 503; then 12 × 200; then 8 × 200 and 4 × 429. |
| Access log | shows the 429s | Checked by the human in `datasheet-rag-api-access` (5 log streams): the 429 lines are there. |
| `ask` (sent as "What is the maximum supply voltage of the LM35?") | 200, LM35 page 4 | 200 in 3.68 s: "30 V [1]", citing LM35 page 4. In the function: connect 177 ms, search 1,147 ms, rerank 430 ms, answer 539 ms. **$0.0023**, not the $0.0003 expected, because Claude left out "absolute" from the frozen q01, so the text was not cached. 30 V is the recommended operating maximum on that page (the absolute maximum is 35 V), so the answer fits the question as sent. The daily count went 0 → 1. |
| Daily cap: limit set to 1 with the count at 1, same question again | 429 "today's limit" with no Bedrock call | 429 with `DAILY_LIMIT` in 2.72 s. The count stayed at 1. The Lambda log line has `status` 429 and no `{"cost": ...}` lines (the ask before it had 4). The limit was then set back to 300. |

### Findings

- **Per-route limits need the CLI.** The HTTP API Throttling page says "Use the CLI or an SDK to create custom route settings". They were set with `update-stage` in CloudShell, and `get-stage` shows them stored as planned: `POST /api/ask` 3 / 0.05, `GET /api/documents` 5 / 1, default 5 / 1.
- **A rate below 1 a second is enforced only loosely.** API Gateway calls its throttles "targets rather than guaranteed request ceilings". A community answer on AWS re:Post explains that several API Gateway hosts count separately, so very low rates leak. Measured: about 20 requests a minute got through against a target of about 3.
  - Decision (the human, 2026-10-17): accept it as a best-effort guard; no exact per-minute limit in our code.
  - Questions past the reranker's 3 a minute get our 429 "wait a minute", and only the Titan question embed (about $0.000002) has been paid by then.
  - **The daily cap of 300 is the exact spend guard.**
- About 50 empty or over-long test questions left the daily count at 0, so a rejected request never uses up the cap.
- Step 5 spend: **$0.0023** (one new question: 1 Titan embed, 1 rerank query, 1 Nova Lite answer).
- **503 from API Gateway under a burst.** Two of 12 `documents` requests sent at once got 503, and the Lambda log has no 503 line from our code. So API Gateway answered without running the function, most likely because the account allows only 10 Lambda runs at a time. That is not confirmed, because the dev user cannot read Lambda's Throttles metric. The page must treat a gateway 503 as "busy, try again" (step 6).

## Code review (1 of 2)

4 of 8 findings fixed:

1. **A failed question used up a place in the cap.** When AWS throttles the reranker or the answer model, `api.ask` now gives the place back (`db.release_daily_ask`), because nothing but the question embed was paid for. Other failures keep their place, because paid calls may already have happened.
2. **The daily-limit 429 looked like the throttling 429.** Its body now also has `"limit": "daily"`, so the page can hide its retry button (step 6).
3. **SQL built with an f-string.** The date expression is now written out in each query, as the repo rule asks.
4. **`call_api.py throttle --count 0` crashed.** argparse now refuses a count below 1.

Not changed:

- **A missing `api_usage` table gives a 500.** Aurora has the table, the cap is off locally, and the handler's log shows the exact error.
- **Two database round trips per question.** A few ms when warm.
- **Lambdas in `call_api.py`.** Style only.
- **A non-number `API_DAILY_ASK_LIMIT` fails at import.** Every other env setting is read the same way.

924 tests pass. `build/lambda.zip` was rebuilt (22,686,478 bytes); **upload it to the Lambda and click Save** so the deployed code matches. After the upload, `call_api.py documents` is enough to check it.
