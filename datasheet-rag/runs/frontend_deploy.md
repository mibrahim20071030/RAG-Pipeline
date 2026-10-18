# Cycle 2 step 6: Frontend on CloudFront and S3

The page `web/index.html` is served from a **private S3 bucket through CloudFront**, on an HTTPS address `https://<id>.cloudfront.net`. The same CloudFront distribution passes `/api/*` on to the HTTP API `datasheet-rag-api`, so the page and the API share one origin:

```
browser ── https://<id>.cloudfront.net ──┬── /         → S3 bucket (private, read only by this distribution)
                                         └── /api/*    → API Gateway datasheet-rag-api → Lambda → Aurora
```

## Why these choices

Each point was checked in the AWS docs on 2026-10-18.

- **CloudFront, not the S3 website endpoint.**
  - An S3 website endpoint serves HTTP only, and every file on it must be public.
  - With CloudFront and **Origin Access Control (OAC)**, the bucket keeps "Block all public access" on. A bucket policy then lets only this distribution read it.
- **Same origin for the API.**
  - The page keeps its relative `fetch("/api/...")` calls. It is the same file locally (`python -m rag.server`) and on AWS, and no API address is written into it.
  - CORS is no longer needed, so the API's CORS setting is removed. Without CORS, a browser on any other site cannot call the API with a JSON body.
  - API Gateway's throttling and the daily cap still apply to every request, because CloudFront only passes requests on.
- **No PDFs in S3.** Two of the vendors forbid republishing:
  - TI (LM35): "Other reproduction and display of these resources is prohibited."
  - Toshiba: "may not be reproduced without prior written permission".

  So the "Open page" links go to each **vendor's own copy**, with `#page=N`, taken from `config.DOCUMENTS[...]["url"]`.
  - ESP32 and LM35: the vendor files are byte for byte the same as ours (same SHA-256, checked 2026-10-18).
  - ST and Toshiba refuse scripted downloads (403 and a timeout), so those two are checked by you in a browser (step 0 below).
- **Price.** The create wizard offered no plan choice, so the distribution is on **pay-as-you-go** (human, 2026-10-18).
  - Free tier, as shown in the console: 1 TB of data out, 10,000,000 HTTP/HTTPS requests and 2,000,000 CloudFront Function invocations a month, always free.
  - After that (US): $0.085 per GB, $0.0100 per 10,000 HTTPS requests ($0.0075 for HTTP).
  - The page is one file of about 30 KB, so the demo stays far inside the free tier. S3 storage for it is negligible.
  - **WAF is off.** The wizard priced it at about $14 a month. The paid route `/api/*` is already limited by API Gateway's throttle and the daily cap.
  - The flat-rate Free plan ($0, no overages) can still be chosen later under CloudFront → Pricing plans, if it is offered there.

## Console steps (the human, us-east-1)

### 0. Check the two vendor PDFs (browser, 2 minutes)

Open each link and look at the footer of page 1 (or the last page):

| Link | Must show |
|---|---|
| <https://www.st.com/resource/en/datasheet/stm32f205rb.pdf> | `DS6329 Rev 18`, 181 pages |
| <https://toshiba.semicon-storage.com/info/docget.jsp?did=149583&prodName=TCTH022BE> | `Rev. 2.0.A`, 14 pages (the date beside it is a print stamp and may differ) |

**Done 2026-10-18:** the human confirmed that both links serve the same documents as our `data/pdfs` copies, so all four vendor URLs stay.

Toshiba's product page lists this document as "Apr, 2023, PDF 1046KB", which matches our file (1,071,599 bytes = 1,046 KB).

Tell Claude what each shows. If one is a different revision, Claude sets its `url` to `None`. Its source cards then have no link, which is better than a link to the wrong page.

### 1. Lambda: new code

Lambda → `datasheet-rag` → **Code** → Upload from → .zip file → `datasheet-rag\build\lambda.zip`. Click **Save**, then check that the code size is about 22.7 MB (22,686,989 bytes). The new code returns the vendor links.

### 2. S3 bucket for the page

S3 → **Create bucket**:

1. Bucket type **General purpose**, region **us-east-1**.
2. Bucket namespace: **Global namespace**. Name: `datasheet-rag-web-<something unique>`, for example with your initials and a number. (The newer "account regional namespace", added March 2026, puts the 12-digit account ID into the bucket name. That name then appears in the CloudFront origin and the bucket policy, and it gains nothing for one private bucket.)
3. Object Ownership: **ACLs disabled** (the default).
4. **Block all public access: ON** (the default). Leave it on.
5. Versioning off, encryption SSE-S3 (the defaults). Click **Create bucket**.
6. Open the bucket → **Upload** → add `datasheet-rag\web\index.html` → Upload. Upload nothing else. Do **not** turn on "Static website hosting".

### 3. CloudFront distribution

CloudFront → **Create distribution**. The wizard may differ a little from these words. Keep to the choices in bold.

1. **Plan**: if it asks, choose **Free** ($0/month). If it offers only pay-as-you-go, stop and tell Claude before creating.
2. **Name**: `datasheet-rag-web`. Distribution type: single website or app. No custom domain.
3. **Origin**: origin type **Amazon S3**, then choose the bucket from step 2. If the list offers a `...s3-website...` address, do not use it; use the plain `<bucket>.s3.us-east-1.amazonaws.com`.
   - Origin access: **Allow private S3 bucket access to CloudFront** (Origin access control). If asked, create a new OAC with **Sign requests (recommended)**.
   - If it offers to **update the bucket policy** for you, accept.
4. **Cache settings**: the defaults for S3 are fine: viewer protocol **Redirect HTTP to HTTPS**, methods GET and HEAD, cache policy **CachingOptimized**.
5. **Security / WAF**: take what the Free plan includes by default. If the wizard asks to choose, take the option with no extra charge, and note what you chose.
6. Create. Deploying takes a few minutes ("Last modified" changes from "Deploying" to a date).
7. Distribution → **General** → Settings → Edit → **Default root object**: `index.html` (if it is not already set). Save.

### 4. Check the bucket policy

S3 → the bucket → **Permissions** → Bucket policy. It should look like this, with your bucket and distribution:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AllowCloudFrontServicePrincipalReadOnly",
    "Effect": "Allow",
    "Principal": {"Service": "cloudfront.amazonaws.com"},
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::<bucket>/*",
    "Condition": {"StringEquals": {"AWS:SourceArn": "arn:aws:cloudfront::<account-id>:distribution/<distribution-id>"}}
  }]
}
```

If the policy is empty, CloudFront → the distribution → Origins → the S3 origin → Edit shows a **Copy policy** button. Paste that policy here and save. Do not paste your account ID anywhere in the repo.

### 5. Second origin: the API

CloudFront → the distribution → **Origins** → **Create origin**:

1. **Origin domain**: the host part of `API_URL` from `datasheet-rag\.env`, `<api-id>.execute-api.us-east-1.amazonaws.com` (no `https://`, no path). If the list offers API Gateway, choose `datasheet-rag-api`.
2. **Protocol**: **HTTPS only**, port 443, minimum TLS 1.2.
3. **Origin path**: empty (the stage is `$default`).
4. Name: `datasheet-rag-api`. Leave the timeouts at their defaults (response timeout 30 s; API Gateway stops at 30 s anyway). Create.

### 6. Behavior for `/api/*`

CloudFront → the distribution → **Behaviors** → **Create behavior**:

| Setting | Value |
|---|---|
| Path pattern | `/api/*` |
| Origin | `datasheet-rag-api` |
| Viewer protocol policy | **HTTPS only** |
| Allowed HTTP methods | **GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE** (the only choice that includes POST) |
| Cache key and origin requests | Cache policy and origin request policy (recommended) |
| Cache policy | **CachingDisabled** |
| Origin request policy | **AllViewerExceptHostHeader** |
| Response headers policy | none |

Why the origin request policy matters: CloudFront must pass on the `Content-Type` header, the body and the query string. It must **not** pass on the `Host` header, because API Gateway answers a request for a host that is not its own with a 403.

Save. The list then has two behaviors: `/api/*` and `Default (*)`.

### 7. Remove the API's CORS setting

API Gateway → `datasheet-rag-api` → **Develop → CORS** → **Clear** (or remove `http://localhost:8000` and save with no origins). The page and the local server both call the API from their own origin, so CORS is not needed.

### 8. Hand over

1. CloudFront → the distribution → copy the **Distribution domain name** (`d1234abcd.cloudfront.net`).
2. Add it to `datasheet-rag\.env` as `SITE_URL=https://d1234abcd.cloudfront.net` (no trailing slash). Like `API_URL`, it stays out of the repo.
3. Tell Claude, with the results of step 0.

## Updating the page later

Upload the new `index.html` to the bucket (it replaces the old one). Then CloudFront → the distribution → **Invalidations** → Create → `/index.html` and `/`. Otherwise the cached copy can stay for up to a day. Invalidations are free up to 1,000 paths a month.

## Tests, 2026-10-18 (Claude, with `scripts\call_api.py --base site`, unless marked)

| Test | Expected | Result |
|---|---|---|
| `page` | 200, HTML, title; http redirected | 200 in 0.31 s, `text/html`, title found (CloudFront cache miss on the first call). `http://` → **301** to `https://`. |
| `documents` after Aurora paused | 200 within 30 s, vendor links | 200 in **18.70 s** (Aurora resume). 4 datasheets, each `pdf_url` the vendor's https address, so the new zip is live. |
| `limits` | API Gateway 404s; our 400/413/415 | Unknown path and `GET /api/ask`: 404 `{"message":"Not Found"}`. 501 characters: 400. 20 KB body: 413. Form body: 415. Our messages pass through CloudFront unchanged. |
| `cors https://evil.example` and `cors http://localhost:8000` (API URL directly) | no allow-origin | Preflight **404** and no allow-origin header on the GET, for both. CORS is cleared, so a browser on any other site cannot send the JSON request. |
| `throttle --route ask --count 8` through CloudFront | some 429 | 7 × 400, 1 × 429 `{"message":"Too Many Requests"}`. API Gateway still counts requests that come through CloudFront. |
| `ask` "What is the absolute maximum supply voltage of the LM35?" (paid, approved) | 200, LM35 page 4 | 200 in **0.91 s**: "35 V [5]", source LM35 page 4, `pdf_url` `https://www.ti.com/lit/ds/symlink/lm35.pdf#page=4`. Search 46 ms, rerank 2 ms (cached on Aurora), answer 471 ms. **$0.0003.** |
| Browser (the human) | ask, badge, "Open page" | Asked from the page, clicked "Open page": the vendor's PDF opened at the cited page. |

### Findings

- **The console shows the CloudFront domain without `https://`.** `SITE_URL` was first saved that way. `call_api.py` now adds the scheme when it is missing.
- **The distribution is on pay-as-you-go.** The create wizard offered no plan choice, and its Billing line said "Pay-as-you-go ($0/month)". The free tier covers far more than this demo needs.
- **Not tested:** fetching the S3 object URL directly, which should be refused. The bucket's name is not in `.env`, and Block Public Access is on, so S3 refuses any public read in any case.
- Step 6 spend: **$0.0003**.

## Code review (1 of 2)

5 of 8 findings fixed:

1. **Pointless "Try again".** The page offered to resend a request that API Gateway or our API had refused with a 4xx (400, 413, …), which would fail the same way. Now only a throttling 429 (not the daily one) and 5xx answers get the button.
2. **Local copy chosen by chance.** `api._pdf_url` picked `/pdfs/<file>` whenever the PDF happened to be on disk. Deployed (`config.DEPLOYED`), it now always gives the vendor link, so a future zip that contains PDFs still neither republishes them nor sends links to S3 (new test).
3. **`page` could crash.** A plain-HTTP check that gets no answer now prints the error instead of a traceback, and `page` refuses to run without `--base site`.
4. **"The same revision" overstated.** The docstring and the `config.py` comment now say that the vendor URLs serve the newest revision and that nothing checks it.
5. **Two `config.py` lines over 130 characters.** Wrapped.

Not changed (reported to the human):

- **Vendor revision drift.** If ST, Toshiba, TI or Espressif publish a new revision, a cited page can open on the wrong page. This is a known limit, documented in `config.py`. Re-check the links before the demo is recorded.
- **Toshiba's `docget.jsp` link might download the PDF instead of opening it**, which would lose `#page=N`. **Checked by the human on 2026-10-18:** `docget.jsp?...#page=5` opened the datasheet in the browser at page 5, so the link works.
- **Every page load wakes Aurora.** The page fetches `/api/documents` on load, uncached, so any visitor or bot resumes the paused database (about 19 s, billed ACU time). Fix options: write the 4 datasheets into the page, or let CloudFront cache that one route. Left for the human to decide.
- **`base` is a module-level variable in `call_api.py`.** Style only.

722 tests pass (207 skipped: no Docker). `build/lambda.zip` rebuilt (22,687,072 bytes) and `web/index.html` changed, so both need uploading again.

## After the review: the datasheet list is written into the page (2026-10-18, the human chose option A)

**Problem.** The page fetched `/api/documents` as soon as it loaded, so every visit went through Lambda to Aurora. A visit while Aurora was paused woke it (17–19 s measured), and Aurora then ran, and was billed, until it had been idle for 5 minutes. Link previews, crawlers or an uptime bot could keep it from ever pausing: about $20 over two weeks at 0.5 ACU and about $0.12 per ACU-hour (a third-party figure, not checked on AWS's pricing page).

**Fix.**
- The four `<option>`s (values are `config.DOCUMENTS` names, labels their parts) are now written into `web/index.html`, so the page makes no request at load. Aurora wakes only when someone asks a question.
- The dropdown appears at once. The "Loading datasheets…" option is gone.
- `tests/test_page.py` (3 tests) keeps the page's list equal to `config.DOCUMENTS` and checks that the page never calls `/api/documents`.
- The route `/api/documents` stays in the API, for `call_api.py documents` and the local server.

No Lambda change. Upload the new `index.html` to the bucket and invalidate `/index.html` and `/`.

Deployed on 2026-10-18. The first re-upload had not reached S3: the object was still from 14:39:38 UTC, before this change. After a second upload (14:55:50 UTC) and an invalidation, CloudFront serves `/` and `/index.html` with the same MD5 as the local file, and the list is in the page.
