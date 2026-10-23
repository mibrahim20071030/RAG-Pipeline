# Cycle 2 step 7b: closing the Aurora cost risk

Started 2026-10-23. Plan approved by the human the same day (fixes 1 to 5, cap 50 a day, off switch as a Lambda variable).

## The problem

Anyone could keep Aurora awake (about $0.12 per ACU-hour), and the daily cap did not stop it:

- `handler.py` connected to Aurora **before** it checked the request, so even an empty question woke it.
- `GET /api/documents`, no longer used by the page, connected on every call, through `SITE_URL` or the `execute-api` address.
- The daily cap was counted in Aurora (`api_usage`), so a request over the cap woke Aurora to find that out.

## What changed in the code

A `POST /api/ask` in Lambda now goes in this order, and only the last step touches Aurora:

1. Off switch: `DEMO_ENABLED=false` answers 503 `{"error": "The demo is switched off right now. ...", "off": true}`.
2. Route and method: only `POST /api/ask`; `/api/documents` is a 404.
3. JSON body checks (type, size, valid JSON).
4. Question checks (`api.check_ask`): present, at most 500 characters, a datasheet from `config.DOCUMENTS`.
5. Daily cap in DynamoDB (`rag/usage.py`): one conditional `UpdateItem` per question. Over the cap: 429 `"limit": "daily"`.
6. Connect to Aurora and answer. The place goes back only when nothing was paid for: Aurora did not answer, the
   datasheet is not stored there, or AWS throttled a model. A database lost partway through an answer keeps it.
   The day is fixed once per question, so a place given back after midnight goes to the right day.

The Lambda's log line has `connect_ms` only when Aurora was touched, which is how the checks below see fix 1.

## Facts checked on 2026-10-23

- DynamoDB on-demand, us-east-1, Standard: $0.625 per million write request units, $0.125 per million read request units, $0.25 per GB-month ([pricing](https://aws.amazon.com/dynamodb/pricing/on-demand/)). The always-free tier (25 GB, 25 WCU, 25 RCU) covers provisioned capacity only, not on-demand requests.
  - What that means here: at most 50 counted questions a day plus refused ones. Even 1 request a second all day (86,400 writes) is about $0.05 a day, inside the Free plan credits.
- TTL deletes use no write capacity and cost nothing on a table with no replicas; they happen within a few days of expiry ([TTL docs](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html)).
- DynamoDB is on the Free plan's list of supported services ([list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html)).
- IAM actions used: `dynamodb:UpdateItem` (take and release a place) and `dynamodb:GetItem` (read the count).

## Console steps for the human

All in us-east-1.

1. **DynamoDB table.** DynamoDB → Tables → Create table.
   - Name `datasheet-rag-usage`; partition key `pk`, type String; no sort key.
   - Table settings: Customize → capacity mode **On-demand**; table class Standard. Create.
   - After it is Active: Additional settings → Time to Live → Turn on, attribute name `expires_at`.
2. **Lambda role.** IAM → Roles → `datasheet-rag-lambda` → Add permissions → Create inline policy (JSON), named `rag-usage-table`. Put the table's ARN (shown on the table's General information) in place of `<TABLE_ARN>`:
   ```json
   {"Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": ["dynamodb:UpdateItem", "dynamodb:GetItem"],
                   "Resource": "<TABLE_ARN>"}]}
   ```
3. **Dev user** (for `call_api.py usage` and the deployed eval plan): IAM → Users → the dev user → Create inline policy `rag-usage-read` with `dynamodb:GetItem` only, on the same ARN.
4. **Lambda.** Lambda → `datasheet-rag`:
   - Code → Upload from → .zip file → `datasheet-rag\build\lambda.zip` → **Save**. Check that the code size shows about 22.7 MB (22,743,889 bytes).
   - Configuration → Environment variables → Edit. Add `USAGE_TABLE` = `datasheet-rag-usage` and `DEMO_ENABLED` = `true`. Change `API_DAILY_ASK_LIMIT` from `300` to `50`. Save.
5. **API Gateway.** `datasheet-rag-api` → Routes → `GET /api/documents` → Delete. Then check in CloudShell that its throttle setting went with it:
   ```
   aws apigatewayv2 get-stage --api-id <API_ID> --stage-name '$default' --query RouteSettings
   ```
   If `GET /api/documents` is still listed, tell Claude; it gives you the one-line removal.
6. Put `USAGE_TABLE=datasheet-rag-usage` in `datasheet-rag\.env` (the scripts read it).
7. **Page.** Upload the new `datasheet-rag\web\index.html` to the S3 bucket and invalidate `/index.html` in
   CloudFront, as in step 6. The only change: a switched-off demo shows no Retry button.

To switch the demo off later: Lambda → Configuration → Environment variables → `DEMO_ENABLED` = `false`. Set it back to `true` to switch it on again.

## Checks after the console work

Claude runs these. All are free except the last, and Aurora should be paused before they start (more than 5 minutes idle).

1. `python scripts\call_api.py --base site limits`: unknown route 404, `GET /api/documents` 404, empty question 400, unknown datasheet 400, too long 400, big body 413, not JSON 415.
2. `python scripts\invoke_lambda.py check` → 400.
3. CloudWatch: those log lines have no `connect_ms`; the human sees `ServerlessDatabaseCapacity` stay at 0.
4. Cap: the human sets today's item (`pk` = `asks#<today UTC>`, `n` = 50) in the DynamoDB console; one ask → 429 `"limit": "daily"` with no `connect_ms`; the human deletes the item.
5. Off switch: `DEMO_ENABLED=false` → 503 with the "switched off" message; back to `true`.
6. Paid (about $0.0003, a cached question, asked first): one ask → 200 with the right page; `call_api.py usage` shows 1 of 50.

## Results

Console work done by the human on 2026-10-23 (all seven steps; CloudShell's `RouteSettings` no longer lists
`GET /api/documents`). Checks run 22:52–23:05 UTC the same day, with Aurora paused at the start. All passed.

| # | Check | Result |
|---|---|---|
| 1 | `call_api.py --base site limits` | unknown route 404, `GET /api/documents` 404, wrong method 404 (API Gateway, no Lambda), empty question 400, unknown datasheet 400, 501 characters 400, 20 KB body 413, not JSON 415; each 0.13–0.30 s (1.78 s for a Lambda cold start) |
| 2 | `invoke_lambda.py check` | 400 in 1.55 ms in the function |
| 3 | No Aurora wake-up | none of the 6 request lines that reached the Lambda has `connect_ms`; the human saw `ServerlessDatabaseCapacity` at 0 throughout |
| 4 | Cap (item `asks#2026-10-23`, `n` = 50, set by the human) | 429 `"limit": "daily"` in 2.42 s, no `connect_ms`, count stayed 50 (a refused ask is not counted); item then deleted |
| 5 | Off switch (`DEMO_ENABLED=false`) | 503 `"off": true` through `SITE_URL` and on the `execute-api` address; switched back on |
| 6 | Paid ask (q01, cached) | 200 in 20.6 s (Aurora resuming): "35 V [5]", citing LM35 page 4; $0.0003; count 0 → 1 of 50 |

Spend for step 7b: $0.0003.

## Code review (1 of 2), 2026-10-23

10 findings, 9 fixed:

- A place was given back on any 503, even after paid calls. Now only when nothing was paid for.
- A place given back after midnight went to the new day. The day is now fixed once per question.
- A missing `USAGE_TABLE` gave an unclear message, and a 500 in Lambda. The message is clearer, the Lambda logs it
  and answers 503, and `USAGE_TABLE` is in `.env.example`.
- The switched-off demo showed a Retry button, and the deployed eval retried it 3 times. Both now stop.
- A datasheet in `config.DOCUMENTS` but not loaded passed the check. It is checked again after connecting: 400,
  and the place goes back.
- The cap of 50 is below a full deployed eval (98 asks). Documented: raise `API_DAILY_ASK_LIMIT` and
  `DEPLOYED_DAILY_LIMIT` for that day, then set them back.
- Tidy-ups: an unreachable error branch removed from `rag/server.py`; the handler's connection helper moved to
  module level (`_open_conn`).
- Kept as is: any other unexpected error after the place was taken keeps it. It may come after paid calls, so
  counting it is the safe side for spend.

## What stays open

- API Gateway's throttle is still loose (best effort). The cap is now the exact guard for both Bedrock spend and Aurora's awake time.
- Within the cap, 50 asks spaced 5 minutes apart could still keep Aurora awake about 4 hours a day (about $0.25 to $0.50).
- The old `api_usage` table on Aurora is unused and left in place; it goes with the cluster at teardown.
