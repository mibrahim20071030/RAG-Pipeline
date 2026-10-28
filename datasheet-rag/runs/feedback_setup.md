# Step 9b: feedback loop, setup

Plan: `C:\Users\muham\.claude\plans\plan-to-build-9b-effervescent-dream.md`.

## Checked before the code (2026-10-24)

- **HTTP API JWT authorizer** (API Gateway docs):
  - It checks the token's signature against the issuer's keys, plus `iss`, `exp`, `nbf` and `iat`.
  - It checks `aud`, or `client_id` when there is no `aud`. A Cognito *access* token has `client_id` and no
    `aud`, so the audience is the app client's ID.
  - Claims reach Lambda in `requestContext.authorizer.jwt.claims`.
  - A request without a valid token is refused by API Gateway before Lambda runs.
  - How a list claim such as `cognito:groups` arrives was not documented. `review.groups` reads both a list and
    the text `"[a b]"`; to confirm on the first real call.
- **Cognito:**
  - Lite and Essentials have 10,000 free monthly active users (direct sign-in), and this free tier does not end
    after 12 months (pricing page).
  - The `/oauth2/token` endpoint answers browsers from any origin (CORS `*`), so a single-page app can use the
    authorization code flow with PKCE and no client secret (developer guide).
- **Knowledge Base:**
  - `.txt` (UTF-8) is a supported data source format.
  - Sidecar keys can be `NUMBER`.
  - **Finding:** the `datasheets` data source has no inclusion prefix, so it reads the whole KB bucket
    (`kb_setup.md`). Notes under `corrections/` in that bucket would end up in the curated search. So the notes get
    **their own bucket** and their own data source.

## Console steps (the human)

> **Changed on 2026-10-28.** Both pipelines now match notes with the same Titan similarity, and the AWS managed side
> reads the notes from DynamoDB (`feedback_eval.md`, "Decision"). So the following are no longer needed and were
> removed that day: step 5 (the notes bucket and the KB data source `corrections`), the S3 and ingestion-job
> statements of `rag-feedback` in step 6 (only `dynamodb:Scan` stays), and the variables `KB_CORRECTION_DATA_SOURCE_ID`
> and `CORRECTION_BUCKET` in steps 7 and 10. Add `CORRECTION_MIN_SIMILARITY=0.7` to the Lambda instead (or leave it
> unset: 0.70 is the default). The steps below are kept as they were run on 2026-10-24.

`<SITE_URL>` is the value in `datasheet-rag\.env`. Keep the IDs the console shows: Claude needs them in step 8.

1. **Cognito → User pools → Create user pool.**
   - Application type: **Single-page application (SPA)**. Name: `datasheet-rag-review`.
   - Sign-in: **Email**. Leave **self-registration off**.
   - Return URL: `<SITE_URL>/review.html`. Create.
   - The pool's **feature plan** (Settings → Feature plans): keep **Essentials** (10,000 free monthly active users;
     this account offers no Lite). Never pick **Plus**, which has no free tier.
2. **The app client** (App clients → `datasheet-rag-review` → Login pages → Edit):
   - Allowed callback URLs: `<SITE_URL>/review.html`.
   - Allowed sign-out URLs: `<SITE_URL>/review.html`.
   - OAuth grant type: **Authorization code grant** only. Scopes: `openid`, `email`.
   - Check that it has **no client secret** (a public client).
3. **Group and user.**
   - Groups → Create `reviewers`.
   - Users → Create user with your email (send an invitation or set a password).
   - Open the user → Add to group → `reviewers`.
4. **Copy the login values:**
   - the **User pool ID**;
   - the **Client ID**;
   - the **domain** (Branding → Domain, `https://<prefix>.auth.us-east-1.amazoncognito.com`).
5. **The notes bucket and data source.**
   - S3 → Create bucket `datasheet-rag-notes-<something unique>`, us-east-1. Block all public access (the default),
     SSE-S3. Leave it empty.
   - Bedrock → Knowledge Bases → `datasheet-rag-kb` → Data sources → Add:
     - Amazon S3, name `corrections`, bucket `datasheet-rag-notes-...`, inclusion prefix `corrections/`;
     - default parsing and chunking.
   - Copy the **data source ID**.
6. **Lambda role** `datasheet-rag-lambda` → Add permissions → Create inline policy `rag-feedback` (JSON). Put
   in your account ID, the KB ID, the table name and the bucket name:
   ```json
   {"Version": "2012-10-17", "Statement": [
     {"Effect": "Allow", "Action": "dynamodb:Scan",
      "Resource": "arn:aws:dynamodb:us-east-1:<ACCOUNT>:table/datasheet-rag-usage"},
     {"Effect": "Allow", "Action": ["s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::datasheet-rag-notes-<...>/corrections/*"},
     {"Effect": "Allow", "Action": ["bedrock:StartIngestionJob", "bedrock:GetIngestionJob"],
      "Resource": "arn:aws:bedrock:us-east-1:<ACCOUNT>:knowledge-base/<KB_ID>"}
   ]}
   ```
7. **Lambda `datasheet-rag`.**
   - **Code:** upload `build\lambda.zip` (Claude rebuilds it) and click Save. The code size should be about
     22.8 MB.
   - Also upload `build\ingest.zip` to `datasheet-rag-ingest` (about 46.5 MB), so both functions run the same
     `rag` code.
   - **Environment variables:** add
     - `KB_CORRECTION_DATA_SOURCE_ID` (step 5);
     - `CORRECTION_BUCKET` (the bucket name);
     - `COGNITO_DOMAIN` (step 4, with `https://`);
     - `COGNITO_CLIENT_ID` (step 4).

     Do not add `REVIEW_AUTH` to either function: unset, the login check is on.
8. **API Gateway** `datasheet-rag-api`:
   - Authorization → Manage authorizers → Create:
     - type **JWT**, name `cognito-reviewers`;
     - identity source `$request.header.Authorization`;
     - issuer `https://cognito-idp.us-east-1.amazonaws.com/<User pool ID>`;
     - audience: the **Client ID**.
   - Routes → Create, each attached to the `datasheet-rag` integration:
     - `POST /api/feedback`, no authorizer;
     - `GET /api/review/config`, no authorizer;
     - `ANY /api/review/{proxy+}`, then attach the authorizer `cognito-reviewers` to it.
   - CloudShell, one line (it keeps the earlier settings):
     ```
     aws apigatewayv2 update-stage --region us-east-1 --api-id <API-ID> --stage-name '$default' --route-settings '{"POST /api/ask":{"ThrottlingBurstLimit":3,"ThrottlingRateLimit":0.05},"POST /api/uploads":{"ThrottlingBurstLimit":2,"ThrottlingRateLimit":0.05},"GET /api/uploads/{id}":{"ThrottlingBurstLimit":5,"ThrottlingRateLimit":1},"POST /api/feedback":{"ThrottlingBurstLimit":5,"ThrottlingRateLimit":0.2},"GET /api/review/config":{"ThrottlingBurstLimit":5,"ThrottlingRateLimit":1},"ANY /api/review/{proxy+}":{"ThrottlingBurstLimit":10,"ThrottlingRateLimit":2}}'
     ```
9. **Pages.**
   - Upload `web\index.html` and `web\review.html` to the page bucket.
   - CloudFront → `datasheet-rag-web` → Invalidations → `/index.html` and `/review.html`.
10. **`.env`.** Add `KB_CORRECTION_DATA_SOURCE_ID`, `CORRECTION_BUCKET`, `COGNITO_DOMAIN` and `COGNITO_CLIENT_ID`.
    Then tell Claude.

If a step is refused or a screen looks different from this list, copy the message or describe the screen to Claude.

## Claude's steps after the console work

1. Add the `corrections` table to Aurora: `create_schema` with `DB_TARGET=aurora`. Free, but it wakes Aurora.
2. Free checks through `SITE_URL`:
   - `/api/review/feedback` with no token gives 401, and no Lambda log line;
   - a token whose user is not in `reviewers` gives 403. This needs a second test user, or the group removed for a
     minute;
   - feedback: 400 for a bad body, 404 for an unknown answer;
   - no `connect_ms` on any feedback request.
3. Paid, after your yes (about $0.15):
   - you ask q08, q24 and q33 on the page and give each a thumbs-down;
   - you review them on `review.html`: diagnose, write the note against the page, publish;
   - Claude runs `correction_eval` before and after on both pipelines, and `similarities` for the thresholds;
   - retract one note, check it is gone from both pipelines, then publish it again.

## Results

### Console work (the human, 2026-10-24)

All 10 steps done.
- Cognito offered only **Essentials** and **Plus** on this account (no Lite). The pool stays on Essentials.
- `REVIEW_AUTH` is not set on either function.

### Login snags (2026-10-24), both fixed by the human in the console

- **Cognito error page "Something went wrong".** Sending the page's own `/oauth2/authorize` request showed
  `error=redirect_mismatch`: the app client's allowed callback URL was not exactly `<SITE_URL>/review.html`. The
  human pasted the page's address into the callback and sign-out URLs. The same request then went on to `/login`.
- **401 on every reviewer call after logging in.** The `www-authenticate` response header said "the token does not
  have a valid audience". The token was right (issuer `us-east-1_hcNSM79Nu`, `client_id` = the app client, groups
  `["reviewers"]`), but the authorizer's Audience did not hold that client ID. With it corrected, the queue
  loaded.
- How to find such a reason next time: DevTools → Network → the 401 request → Response Headers → `www-authenticate`.
  Cut the `authorization` request header out before sharing a request: it is a working token for an hour.

### Claude's checks (2026-10-24)

1. **Aurora:** `create_schema` with `DB_TARGET=aurora` (connect 18.8 s, Aurora was paused).
   - `corrections` has its 7 columns and 0 rows.
   - The 797 curated `fixed_400_prefix` chunks are unchanged.
2. **Lambda `datasheet-rag`:** code size 22,777,448 bytes, the same as the local build. It has the four new
   variables and no `REVIEW_AUTH`. The dev user cannot read `datasheet-rag-ingest`'s configuration (no
   permission, as in 9a), so its upload is taken from the human.
3. **Free checks through `SITE_URL`:**
   - `GET /api/review/config`: 200, `auth: true`, with the pool's domain and client ID.
   - **401 `{"message":"Unauthorized"}` from API Gateway** for each of these:
     - the queue with no token, through the site and through `execute-api`;
     - the queue with a junk token;
     - publish with no token.
   - `POST /api/feedback`: 404 for a bad id, 400 for a bad rating, 404 for an unknown answer. `GET /api/feedback`
     gets 404 from API Gateway (no such route).
   - `index.html` and `review.html` are served equal to the local files.
4. **CloudWatch:** only 4 Lambda lines came from these checks: the config call and the 3 ratings. None of the five
   401s reached the function, and no line has `connect_ms`, so ratings never touch Aurora.
