# Cycle 2 step 8a: Managed Knowledge Base setup

Started 2026-10-23. Plan approved by the human the same day: Bedrock Managed KB, managed embedder and reranker,
default chunking, managed parser. The KB finds the passages; our `generate()` writes the answer.

## Console steps for the human

All in us-east-1.

1. **Files.** From `datasheet-rag` with the venv active: `python scripts\kb_prepare.py`. It fills
   `datasheet-rag\build\kb\` with the 4 PDFs and 4 `.pdf.metadata.json` files (6.4 MB).
2. **S3 bucket.** S3 → Create bucket.
   - General purpose; name `datasheet-rag-kb-<something unique>`.
   - Block all public access: on (the default). Encryption: SSE-S3 (the default). Create.
   - Open it → Upload → add the 8 files from `build\kb\` → Upload. They sit at the bucket root.
3. **Knowledge Base.** Amazon Bedrock AgentCore → Built-in tools → Knowledge Base → **Create Managed Knowledge Base**.
   - Name `datasheet-rag-kb`.
   - Additional configurations: embedding model **Managed**; IAM: **Create and use a new service role**; encryption:
     AWS managed key.
   - Data source: name `datasheets`, type **Amazon S3**, this account, the bucket's URI (`s3://datasheet-rag-kb-.../`).
     Leave the metadata files prefix empty (the sidecars sit next to their PDFs).
   - Content parsing and chunking: parser **Managed parser**, chunking **Default chunking**.
   - Advanced configurations: leave visual content, audio and video **off**.
   - Log delivery: none. Create, and wait for it to be ready (2–5 minutes).
   - Open the data source → **Sync**. Wait for the sync to finish, and note the time it took and the counts it shows.
4. **Dev user permission.** IAM → Users → the dev user → Add permissions → Create inline policy (JSON), named
   `rag-kb-query`. Put the KB's ARN (shown on the KB's page) in place of `<KB_ARN>`:
   ```json
   {"Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "bedrock:Retrieve", "Resource": "<KB_ARN>"}]}
   ```
5. Put `KB_ID=<the KB's ID>` in `datasheet-rag\.env`.
6. Tell Claude. Claude runs one probe (`python scripts\kb_probe.py "..."`, $0.001).

If a step is refused or a screen looks different from this list, copy the message or describe the screen to Claude.

## Step 8d console steps (deploy)

All in us-east-1. API Gateway needs no change: the KB uses the same `POST /api/ask` route and throttle.

1. **Lambda role.** IAM → Roles → `datasheet-rag-lambda` → Add permissions → Create inline policy (JSON), named
   `rag-kb-query`, with the same JSON as the dev user's (`bedrock:Retrieve` on the KB's ARN).
2. **Lambda code.** Lambda → `datasheet-rag` → Code → Upload from → .zip file →
   `datasheet-rag\build\lambda.zip` → **Save**. Check that the code size shows about 22.7 MB (22,747,984 bytes).
3. **Lambda variables.** Configuration → Environment variables → Edit → add `KB_ID` = the KB's ID and
   `KB_DAILY_ASK_LIMIT` = `50`. Save.
4. **Page.** S3 → the page bucket (`datasheet-rag-web-...`) → upload `datasheet-rag\web\index.html` (replace).
   Then CloudFront → `datasheet-rag-web` → Invalidations → Create → `/index.html`.
5. Tell Claude. Claude runs the free checks, then one paid KB question through the site.

## Results (2026-10-23)

- Console work done by the human.
  - The files sit under the prefix `kb/` in the bucket. That doesn't matter: the KB scans the whole bucket.
  - The deletion safeguard is off. A later re-sync that replaces files must be free to delete the old ones.
- Sync: 20:57 to 21:01 (UTC-4), about 4 minutes. 4 scanned, 4 added, 0 failed, 4 metadata files.
- KB ID and ARN: found in CloudShell (`aws bedrock-agent list-knowledge-bases`, then `get-knowledge-base`). The
  AgentCore console page does not show the ARN.
- Probe, "What is the supply voltage range of the LM35?", top 5 ($0.001). Every result has:
  - `content.text`;
  - `location.s3Location.uri` (an `https://<bucket>.s3.amazonaws.com/kb/lm35.pdf` address, not `s3://`);
  - a `score` (0.34–0.55);
  - `metadata` with AWS keys (`_source_uri`, `_document_title`, `_chunk_id`, `_excerpt_page_number`, `_file_type`,
    `_data_source_id`, `_created_at`, ...) and our sidecar's `document` and `part`.
  - Table chunks also carry `_table_id`, `_table_sequence_number` and `_table_section_title`. The managed parser
    finds tables.
- **Page numbers: `_excerpt_page_number`**, a float (`19.0`), the 1-based PDF page position, the same as our `page`.
  - Checked against `runs/parsed/lm35.json`: the "single power supplies / self-heating" text the KB gave as pages
    1 and 13 is on exactly pages 1 and 13 there, and "self-heating" is also on page 19.
  - So the 4 whole PDFs stay; the one-PDF-per-page fallback is not needed.
  - Known limit: for a chunk that crosses pages it is presumably the page the excerpt starts on. A citation then
    points at that page.
- The answer page (LM35 page 4, recommended operating conditions) was not in this top 5. That is a first sign of
  the retrieval difference, to be measured in 8c.
