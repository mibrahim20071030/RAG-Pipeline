# Project 2: Datasheet RAG on AWS

Question answering over component datasheets (ESP32, STM32, sensors) with page-level citations.
Built in two cycles. Cycle 1 builds the pipeline and the evaluation locally. Cycle 2 deploys to AWS, adds the web UI, and writes the report.

This file is the build plan. Read it fully before writing code. Work one layer at a time. A layer is finished only when its test passes.

## Current status (update as layers finish)

- Day 1 (learning and planning) is complete. All design decisions below are settled.
- Layer 0, database part: DONE. Postgres with pgvector runs in Docker as container `rag-pg` from image `pgvector/pgvector:pg16`, port 5432, volume `rag_pgdata`. `CREATE EXTENSION vector` worked and a `<=>` query returned a distance. The container name and volume are fixed, so reuse them.
- Layer 0, Python environment: the human created the folder `datasheet-rag` (inside `P2 - RAG Pipeline`), made `.venv` with Python 3.14.6, and ran `pip install pdfplumber "psycopg[binary]" boto3 pytest numpy`. Prebuilt Python 3.14 wheels were available for every package (numpy 2.5.4, psycopg-binary 3.3.6, Pillow, cryptography, and others), so no downgrade is needed locally. The pasted install log ended partway through, so first confirm it finished: activate `.venv`, run `pip list`, and run `python -c "import pdfplumber, psycopg, boto3, numpy, pytest"`. If anything is missing, rerun the pip command.
- Layer 0, done on 2026-10-11: install confirmed (all five packages import). Git repo initialised at `P2 - RAG Pipeline` (branch `main`, no commits yet), with `.gitignore` at that root; code lives in `datasheet-rag/`. The four datasheets were moved to `datasheet-rag/data/pdfs/` (gitignored, as is `Project2_Study_Guide.pdf`). Folder layout, `rag/config.py`, `rag/costlog.py`, `rag/embed.py`, `scripts/test_embed.py`, `.env.example` and a `.env` with an empty `PGPASSWORD` exist. `rag-pg` restarts with `docker start rag-pg`.
- Layer 0: DONE on 2026-10-11. `scripts/test_embed.py` printed 1024 and added one row to `runs/cost_log.csv`. psycopg connects to `rag-pg` with the password from `datasheet-rag\.env`. The AWS key is in `~\.aws\credentials`. Since 2026-10-12 its IAM user has only an inline Bedrock policy (InvokeModel on the four project models, Rerank, ListFoundationModels), so it cannot create Cycle 2 resources; the rerank action names are still to VERIFY at Layer 5. `list_foundation_models` in us-east-1 lists `amazon.titan-embed-text-v2:0`, `amazon.nova-lite-v1:0`, `amazon.nova-pro-v1:0` and `cohere.rerank-v3-5:0`; only Titan has been invoked. The first InvokeModel call returned "Your account is currently being verified" and succeeded on a retry some minutes later.
- Still open from Layer 0: the Titan V2 price in `rag/config.py` is not yet verified (confirm before Layer 3).
- Layer 1, 2026-10-12: `rag/parse.py`, `tests/test_parse.py` (38 pass), `conftest.py` and `scripts/show_parsed.py` exist. `python -m rag.parse` writes `runs/parsed/<doc>.json` for the four datasheets (311 pages, 263 tables, none scanned). Each page has `page`, `page_label`, `text` (whole page), `text_outside_tables` and `tables` (`caption`, `rows`; a `null` cell is a merged cell). Not finished: the manual review in `runs/parse_review.md` has 4 of 12 pages checked (by Claude, against rendered images); the human checks the other 8.
- Next: the human opens and merges the PR with Layer 1 and the hooks; then Claude adds the progress-log hook; then finish the Layer 1 table review; then Layer 2 (chunking), in plan mode, when the human says so. Read "Known limits" in `runs/parse_review.md` before writing the table chunker.
- The four datasheets are the final set. The human does all git commits and pushes.
- Done by the human already: four datasheet PDFs gathered, AWS Budget alerts set, AWS account created in early October 2026 on the Free plan.

## Environment notes

- The human works on Windows with PowerShell. Use PowerShell-compatible commands. Activate the virtual environment with `.venv\Scripts\Activate.ps1`.
- The project path contains spaces. Quote paths in commands.
- Python 3.14.6 is installed and works for the Layer 0 packages. Before Cycle 2, check which Python versions Lambda supports (VERIFY). If Lambda does not support 3.14, build the Lambda package with a supported version, and keep the local code compatible with it.
- Never write the database password or AWS keys into any file in the repo, into `CLAUDE.md`, or into a chat message. Read the local database password from an environment variable or a `.env` file listed in `.gitignore`.
- Never create IAM users, access keys, or other credentials on the human's behalf. Tell the human what to create in the console, and what permissions it needs.

## Rules for working in this repo

- Do not start building a layer until the human says to build it. Each layer starts in plan mode; build only after the human approves the plan. Finishing one layer is not permission to start the next.
- When a build runs into an issue, explain it in simple language: what the issue is, why it is happening, and how to fix it.
- The human does all git commits, pushes, PRs and merges. Work goes to GitHub through a pull request. Claude says when work is ready and lists the changed files.
- Hooks (added 2026-10-12):
  - `.githooks/pre-commit` runs `tools/checks.py --staged` before every commit: secret scan, `ruff`, `pytest`. A failure stops the commit. Enabled per clone with `git config core.hooksPath .githooks`. Needs `ruff` in the venv (`pip install ruff`).
  - `.claude/settings.json` has a Stop hook (`tools/review_gate.py`). When code has changed it runs the same checks and sends Claude back to fix failures. When they pass and the layer has not been reviewed, it asks for the code review.
  - Code review rule (set by the human on 2026-10-12): one review per layer, with the built-in code-review skill, when the layer is finished and before the PR. Record it with `python tools/review_gate.py --reviewed`. After fixing review findings, re-run the checks only. Run a second review only if a fix rewrote logic substantially, and never more than two per layer. The hook counts a layer as everything since the branch left `main`; `python tools/review_gate.py --status` shows the count.
  - `.github/workflows/tests.yml` runs ruff and pytest on every pull request into `main`. GitHub has no AWS credentials, database or datasheet PDFs, so a test that needs one must skip itself there. `pytest.ini` limits collection to `tests/`; never put a paid call in a file pytest can collect.
  - Still to add, after the hooks PR is merged: a hook that logs each session, with its date, into `progress.md`.
- Build one layer at a time, in order. Do not start a layer until the previous layer's test passes.
- Keep the pipeline steps as separate small functions in separate modules. The entry point (local server in Cycle 1, Lambda handler in Cycle 2) only calls them in order.
- Keep all SQL in `rag/db.py`. Nothing else writes SQL strings.
- Never put secrets in code, in the repo, or in the frontend. No AWS keys in Git. Add `.env` and `.aws/` to `.gitignore` first.
- Every claim about an AWS price, limit, API field, or model ID that is marked VERIFY must be checked against the current AWS documentation or console before it is relied on.
- Keep `progress.md` (repo root) up to date. At the end of every session, add a dated entry at the top of the log: what was done, decisions, problems, what is still open. Update the human's checklist in the same file.
- Keep a cost log. Every Bedrock call records its token or query count to `runs/cost_log.csv`.
- Do not run any evaluation loop that calls a paid model until the number of calls and the estimated cost are printed and confirmed.

## Settled design decisions

| Decision | Choice | Reason |
|---|---|---|
| Region | us-east-1 (N. Virginia) | In-region for Titan Embeddings V2, Cohere Rerank 3.5, Nova Lite. Data location does not matter. |
| Embedding model | Amazon Titan Text Embeddings V2, 1024 dimensions, same model in both cycles | Cheapest option found, 8,192 token input, one `vector(1024)` column, no re-embedding when moving to AWS. |
| Reranker | Cohere Rerank 3.5 on Bedrock | Available in us-east-1. About $2 per 1,000 queries; one query covers up to 100 chunks. |
| Answer LLM | Amazon Nova Lite (in-region, no cross-Region profile needed) | Low cost, works inside the Free plan limits. Claude Haiku 5.5 is an optional comparison only if the account allows cross-Region inference profiles (VERIFY). |
| RAGAS judge LLM | Amazon Nova Pro (in-region in us-east-1) as the default. Check price first. | A stronger model than the generator. VERIFY price before any RAGAS run. |
| Database, Cycle 1 | Postgres with pgvector in Docker on the local machine | Free. |
| Database, Cycle 2 | Aurora PostgreSQL Serverless v2, minimum capacity 0 ACU (auto-pause), accessed through the RDS Data API | No compute cost when paused. Lambda does not need to sit in a VPC, so no NAT gateway or VPC endpoint charges. Fallback: RDS PostgreSQL db.t4g.micro (always on, about $12 per month). |
| Page number stored | PDF page position (1-based index in the file) as `page`. Printed label stored separately as `page_label` when parsable. | A user can open the PDF at that page. Citations use `page`. |
| Section headings | Stored best effort in `section`. Prepending document name and section to the embedded text is an experiment (on vs off). | Gives a chunk context. Measured, not assumed. |
| Keyword search | Postgres full-text search with an OR-joined tsquery built by our own function. Test the `english` and `simple` configurations. | `plainto_tsquery` requires every word to match, so long questions return nothing. English stemming may damage part numbers. |
| Datasheet filter | No automatic detection in the baseline. Cycle 2 UI has a datasheet dropdown (default: all). `detect_part` is an experiment. | A wrong filter hides the right answer. |
| Vector index | None in Cycle 1 (exact search). HNSW is an experiment that measures index recall. Cycle 2 uses exact search. | A few thousand chunks do not need an index. |
| recall@k definition | Hit rate: for each question, 1 if at least one correct chunk is in the top k, else 0, averaged over questions. A question may list several acceptable (doc, page) pairs. | Defined once, used everywhere. MRR is reported alongside. |
| Candidates and k | Hybrid search returns N = 30. Reranker keeps k = 5. Both are tunable (k in 3, 5, 10). | |
| Frontend | One plain HTML page with `fetch`, hosted as a static website in S3 | No framework needed. |
| Public demo | Throttled API Gateway plus Lambda concurrency cap. Torn down after publishing. A recorded demo and the repo stay as the portfolio artifact. | A public URL is the main spending risk. |

## Architecture

Ingest (run once per datasheet set, ahead of time):

1. Datasheet PDFs are read page by page. Text and tables are extracted.
2. Text is cleaned and split into chunks. Each chunk keeps document, page, section, and kind (text or table).
3. Each chunk is embedded with Titan V2 (1024 numbers).
4. Chunks, metadata, and vectors are stored in Postgres.

Query (every question):

1. The question is embedded with the same model.
2. Vector search and keyword search each return the top N chunks.
3. Reciprocal Rank Fusion merges the two lists.
4. The reranker scores each (question, chunk) pair. The top k are kept.
5. A prompt is built: rules, numbered passages, question. Nova Lite answers using only the passages and cites `[n]`.
6. Our code maps `[n]` back to document and page from stored data, and flags any invalid label.
7. The response (answer and sources) is returned.

Cycle 1 runs everything locally behind a script and a small local server. Cycle 2 moves the same functions behind API Gateway and Lambda, with Aurora as the database and S3 as the host for the page and the PDFs.

## Repo layout

```
rag/
  config.py        # region, model IDs, N, k, chunking settings
  db.py            # all SQL; one interface, two backends (psycopg local, Data API in Cycle 2)
  parse.py         # PDF -> pages, text, tables
  chunk.py         # chunking strategies
  embed.py         # Titan V2 calls
  search.py        # vector_search, keyword_search, rrf, optional filter
  rerank.py        # Cohere Rerank 3.5 call
  generate.py      # build_prompt, call_llm, resolve_citations
  pipeline.py      # answer_question(question) calling the steps in order
  eval/
    metrics.py     # recall_at_k, mrr
    run_eval.py    # runs a strategy over the question set, logs metrics, latency, cost
    ragas_run.py   # RAGAS on shortlisted strategies only
data/
  pdfs/            # datasheets (4 or 5)
  questions.jsonl  # ~40 hand-built questions with answers and sources
runs/              # eval outputs and cost_log.csv
tests/
handler.py         # Cycle 2 Lambda entry point
web/index.html     # Cycle 2 frontend
```

## Cycle 1: build the pipeline and the evaluation locally (full detail)

### Layer 0: setup and guardrails

Do:
- Create a Python virtual environment. Install: `pdfplumber`, `psycopg[binary]`, `boto3`, `pytest`, `numpy`, plus RAGAS later (check current install name and version).
- Postgres with pgvector in Docker is already running (see Current status). Command used, for reference or to recreate it, with the password set locally and never committed: `docker run -d --name rag-pg -e POSTGRES_PASSWORD=<local password> -p 5432:5432 -v rag_pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16`. Use `pg16` to stay close to Aurora's Postgres 16 line. Do not switch to a pg18 tag without checking the data path and the Aurora version.
- Set an AWS Budget with email alerts at small amounts before any Bedrock call.
- Create one IAM user for local development with only the permissions needed to call the specific Bedrock models (VERIFY action names, such as invoking models and reranking). Keep its access key in the local AWS credentials file, never in the repo. Delete it at teardown. (The new account experience may not support IAM Identity Center. VERIFY.)
- In the Bedrock console, request access to the models if required (VERIFY).

Test:
- `psql` connects to the local database and `CREATE EXTENSION IF NOT EXISTS vector;` succeeds.
- A script embeds one sentence with Titan V2 and prints `len(vector)`. It must print 1024.
- The cost log file receives one row.

### Layer 1: PDF parsing

Do:
- Extract text per page and tables per page with pdfplumber. Keep the PDF page position with everything extracted.
- Strip repeated headers and footers (text that repeats on most pages).
- Save the parsed result to `runs/parsed/<doc>.json` so later layers do not re-parse.

Test:
- `pytest tests/test_parse.py` checks: page count equals the PDF's page count; every extracted item has a page number; no page is empty unless it is blank in the PDF.
- Manual check, recorded in `runs/parse_review.md`: for each datasheet, open at least 3 table pages beside the parsed output. Confirm column headers stay with their values and that symbols (Ω, µ, ±, subscripts) are correct.
- If a datasheet is scanned (text cannot be selected), stop and decide on OCR. Do not continue silently.

### Layer 2: chunking

Do:
- Implement strategies as separate functions with the same output format: a list of chunks, each with `document`, `page`, `section` (optional), `kind` (text or table), `text`.
  - `page_based`
  - `fixed(size, overlap)` for 256 and 400 tokens, with and without overlap
  - `structure_aware` (split at headings and paragraphs)
  - `fixed_1024` as the large-chunk comparison
- Tables: keep a table whole in one chunk if it fits. If it must be split, repeat the header row in every piece. Convert rows to row-as-sentence text ("Parameter: VDD, Min: 3.0, Typ: 3.3, Max: 3.6, Unit: V"). Store the table caption with it.
- Count size in an approximate token measure (for example characters divided by 4, or words times 1.3). Record which measure is used.
- Chunks sent to the reranker must stay at or below about 400 tokens including the question. Longer text is truncated for reranking only, or the strategy is marked as rerank-limited.

Test:
- `pytest tests/test_chunk.py` checks: every chunk has document and page; no chunk exceeds its size limit; overlap chunks share the expected text; every table chunk starts with or contains its header labels; chunk page numbers match the source page.
- Print chunk counts per strategy per document and keep them in `runs/chunk_counts.csv`.

### Layer 3: embeddings and storage

Do:
- Create the tables `documents` and `chunks` in `rag/db.py` (see Schema). One `strategy` column on `chunks` lets several chunking strategies live in the same table for comparison.
- Embed chunks in batches with Titan V2 (1024 dimensions; check the `normalize` setting in the API docs). Log tokens to the cost log.
- Make the load idempotent: re-running for the same document and strategy replaces the old rows.

Test:
- `SELECT count(*) FROM chunks WHERE strategy = '<s>'` equals the number of chunks produced by Layer 2.
- `SELECT vector_dims(embedding) FROM chunks LIMIT 1` returns 1024.
- Re-run the load. The count must not change.
- The cost log shows the token total, and the estimated cost is printed.

### Layer 4: retrieval

Do:
- `vector_search(question_vec, n)` using `ORDER BY embedding <=> %s::vector LIMIT n`. Keep the raw `<=>` in `ORDER BY`.
- `keyword_search(question, n)` using a generated `tsv` column, a GIN index, and an OR-joined tsquery built by our own function from the question's words. Keep exact tokens such as part numbers and values. Compare `english` and `simple` configurations.
- `rrf(ranked_lists, c=60)` over chunk ids.
- Optional `detect_part` and metadata filter, switchable by config (experiment, default off).

Test:
- `pytest tests/test_search.py` with a tiny fixture set: RRF on known lists returns the known order; a question with an exact part number returns the chunk containing it in the keyword top 3; a long natural question returns at least one keyword result.
- `EXPLAIN` on the vector query shows the cosine distance operator in the sort.
- Print vector-only, keyword-only, and hybrid top 5 for five hand-picked questions and review by eye.

### Layer 5: reranking

Do:
- `rerank(question, candidates, k)` calling Cohere Rerank 3.5 on Bedrock (VERIFY the API name and request fields). Send at most 100 documents per call. Log query units to the cost log.
- Make the reranker switchable on and off by config.

Test:
- A fixture with two chunks: "Each GPIO pin can source at most 40 mA." and "The total current for all GPIO pins together must not exceed 1,200 mA." For the question "What is the maximum current per GPIO pin?", the per-pin chunk must score higher. (Use the real numbers from a real datasheet once available.)
- A call with 101 candidates is split into two calls.
- Output has exactly k items, sorted by score.

### Layer 6: generation and citations

Do:
- `build_context(chunks)` numbers passages from 1 and labels each with document and page.
- `build_prompt` / system prompt rules: answer only from the numbered passages; cite `[n]` after each claim; if the passages do not contain the answer, say the provided datasheets do not contain it; do not use outside knowledge; if passages conflict, say so.
- `call_llm` calls Nova Lite through the Bedrock Converse API with the rules in the system field (VERIFY request format). Set a maximum output length. Log tokens.
- `resolve_citations(answer, chunks)` maps `[n]` to `chunks[n - 1]` and returns the sources plus any invalid labels.

Test:
- `pytest tests/test_generate.py`: labels start at 1; `resolve_citations` maps `[2]` to the second chunk; `[250]` is reported invalid; duplicate labels produce one source.
- Run 3 answerable and 3 unanswerable questions. Unanswerable ones must produce a "not contained" answer. Record the results in `runs/generation_smoke.md`.

### Layer 7: evaluation

Do:
- Build `data/questions.jsonl` by hand, about 40 questions. Each has: `question`, `expected_answer`, `sources` (a list of acceptable `[document, page]` pairs), and `type` (single value, table lookup, comparison, rephrased wording, no answer exists). Read the datasheets yourself so the answers are known to be right. Include exact-value questions, table lookups, questions phrased differently from the datasheet, and about 5 unanswerable questions.
- `metrics.py`: `recall_at_k(results, truths, k)` and `mrr(results, truths)`.
- `run_eval.py <strategy-config>`: runs all questions through one strategy and writes per-question results and a summary row: recall@k, MRR, latency by stage (embed, search, rerank, generate), and cost per query.
- Strategies to compare (change one thing at a time, same questions): chunking (page, fixed 256, fixed 400 with and without overlap, structure-aware, fixed 1024), retrieval (vector only, keyword only, hybrid), reranker (on or off), k (3, 5, 10), keyword configuration (`english` vs `simple`), header prefix (on or off), datasheet filter (on or off), HNSW index (on or off; measure index recall against exact search).
- Retrieval metrics need no LLM and run on every strategy. Run RAGAS (faithfulness and response relevancy, plus context metrics if useful) only on the best 3 or 4 strategies. Check the current RAGAS documentation for metric names and judge-model setup. Spot-check a sample of judge scores by hand.
- Do not tune chunking to individual test questions.

Test:
- `pytest tests/test_metrics.py` on toy data with known answers. Include this case: two strategies with the same recall@5 and different MRR (correct chunk at rank 1 versus rank 5).
- A dry run prints the number of paid calls and the estimated cost before any real run.
- The baseline run produces `runs/results.csv` with one row per strategy and all required columns.

### Layer 8: Cycle 1 wrap-up

Do:
- Pick the best strategy per the results. Record the decision and the numbers in `runs/cycle1_summary.md`.
- Freeze the question set and the chosen configuration in `rag/config.py`.

Test:
- `python -m rag.pipeline "<question>"` prints an answer with document and page citations for three real questions.

## Schema (draft)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id       SERIAL PRIMARY KEY,
    name     TEXT NOT NULL,
    part     TEXT,
    revision TEXT
);

CREATE TABLE chunks (
    id          SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id),
    strategy    TEXT NOT NULL,           -- which chunking strategy produced it
    page        INTEGER NOT NULL,        -- PDF page position, used for citations
    page_label  TEXT,                    -- printed page label, if known
    section     TEXT,
    kind        TEXT,                    -- 'text' or 'table'
    text        TEXT NOT NULL,
    embedding   vector(1024),
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv);
-- Experiment only: CREATE INDEX chunks_emb_idx ON chunks USING hnsw (embedding vector_cosine_ops);
```

## Cycle 2: deploy, UI, report (outline)

Order of work, each with a test written when the step starts:

1. **Aurora smoke test (first step).** Create an Aurora PostgreSQL Serverless v2 cluster on the Free plan with minimum capacity 0 ACU. Enable the Data API. Run `CREATE EXTENSION vector`. If pgvector, minimum 0 ACU, or the Data API do not work on this account, switch to the RDS db.t4g.micro fallback and note the cost.
2. **Data access layer.** Add the Data API backend to `rag/db.py`. The same query tests from Layer 4 must pass against both backends. Check the Data API response size limit and parameter handling for vectors (VERIFY).
3. **Load the data.** Load chunks and vectors into Aurora, using batch statements. Confirm row counts and vector dimensions.
4. **Lambda.** Package the pipeline and `handler.py`. Use an IAM role for Bedrock, the Data API, and S3 read. No access keys. Set a memory size, timeout, and a reserved concurrency cap. Add retry logic for the first query after Aurora resumes from a pause. Cold-start the function and record the time.
5. **API Gateway.** Create the route, throttling (requests per second and a daily cap), a maximum question length, and a maximum output token count. Configure CORS for the S3 page origin. Test with `curl` and then from the page.
6. **Frontend.** One HTML page: text box, button, answer area, sources listed as document and page, optional datasheet dropdown. Host as a static website in S3 (VERIFY the current public-hosting setup). The page contains no secrets.
7. **Deployed evaluation.** Run the final strategy against the deployed endpoint. Record latency (including cold starts and Aurora resume) and cost per query.
8. **Report.** Write the evaluation report: setup, the 40-question set, results table (recall@k, MRR, RAGAS faithfulness and relevancy, latency, cost), failure analysis by question type, and what was learned. Add a recorded demo.
9. **Teardown.** After publishing, run the teardown checklist and confirm in Billing that spend has stopped.

### Teardown checklist

- Delete the API Gateway API and the Lambda function and its role.
- Empty and delete the S3 buckets (page and PDFs).
- Delete the Aurora cluster. Skip the final snapshot unless one is wanted. Delete any manual snapshots and the Secrets Manager secret.
- Delete the IAM user and its access keys.
- Check CloudWatch log groups, and delete them.
- Check Billing and Cost Management the next day and a week later. Confirm no new charges.
- Remember the Free plan lasts 6 months from sign-up (about early April 2027; confirm the exact date in Billing). Projects 3 and 5 must be planned inside that window or the account must be upgraded.

## Gotchas collected from the lessons

Models and embeddings
- LLMs produce likely text, not true text. Grounding rules help but do not guarantee faithfulness. Evaluate.
- Use the same embedding model for chunks and questions. Changing the model means changing `vector(n)` if needed and re-embedding everything.
- Embeddings are weak on exact strings (part numbers, values). Keep keyword search in the pipeline.
- The reranker takes text, not vectors. It reads about 512 tokens per document including the question. Longer chunks are split and cost more.

Parsing and chunking
- Check parsed tables by eye. Column headers must stay with values.
- Symbols (Ω, µ, ±) and subscripts can parse wrongly and change a value.
- Printed page numbers can differ from PDF page positions. Citations use PDF position.
- Tables stay whole where possible. Repeat headers when splitting.
- Overlap costs more chunks and can return duplicate passages.

Postgres and pgvector
- Postgres is a server that must be running. psycopg is only a client.
- `vector(n)` must match the model output. A wrong length gives an error.
- Keep the raw `<=>` alone in `ORDER BY` so an index can be used. Compute `1 - distance` only in `SELECT`.
- Build an index with the matching operator class (`vector_cosine_ops` for `<=>`).
- With an HNSW index, a `WHERE` filter is usually applied after the nearest vectors are found, so a filter can return fewer than k rows. Exact search applies filters correctly.
- `plainto_tsquery` requires every word to match. Use an OR-joined query.
- Always pass values through placeholders. Never build SQL by string joining.
- Run `CREATE EXTENSION vector` before creating tables that use it.
- Keep the vector and keyword results as ranked lists of ids and merge them with RRF, not by adding scores.

Generation and citations
- We create the `[n]` labels and the label-to-chunk mapping. The model only repeats labels. A label can still be wrong or invalid. Validate every label.
- Include unanswerable questions in the test set.

Evaluation
- Retrieval metrics and answer metrics are separate. Fix retrieval first.
- Index recall (approximate vs exact search) and recall@k (against hand-written ground truth) are different measurements.
- Change one thing at a time. Keep the question set fixed.
- LLM judges are LLMs and can be wrong, and each judged answer costs several calls.

AWS and cost
- Keep everything in one region, us-east-1. Check the region selector in the console.
- Roles, not secret keys, for Lambda. Local development uses a narrowly scoped IAM key kept out of Git.
- A Lambda inside a VPC cannot reach Bedrock without a NAT gateway or VPC endpoint, which bill hourly. Use the Data API so Lambda stays outside the VPC.
- Aurora resumes slowly after a pause. Add retry logic.
- Always-on databases are the main credit risk. Pause or delete.
- A public API URL can be called by anyone. Use API Gateway throttling, a Lambda concurrency cap, a question length limit, and an output token limit. Budget alerts only warn, they do not stop spending.
- CORS blocks are enforced by the browser. The Lambda can run fine and the page can still show an error.
- Never put Bedrock or database credentials in the frontend.
- Package Lambda dependencies for Linux, or use only boto3 and the standard library.
- The Free plan expires after 6 months or when credits are used. Plan around it.

## Test commands (created as each layer is built)

```
pytest -q tests/test_parse.py
pytest -q tests/test_chunk.py
pytest -q tests/test_search.py
pytest -q tests/test_generate.py
pytest -q tests/test_metrics.py
pytest -q                                   # everything
python -m rag.eval.run_eval --config configs/baseline.yaml --dry-run   # prints call count and estimated cost
python -m rag.eval.run_eval --config configs/baseline.yaml             # real run
python -m rag.pipeline "What is the maximum supply voltage?"
```

## Definition of done

Cycle 1 is done when:
- All layer tests pass (`pytest -q` is green).
- 4 or 5 datasheets are parsed, chunked, embedded, and stored, and the parsed tables were reviewed by eye.
- `data/questions.jsonl` has about 40 hand-checked questions, including unanswerable ones.
- `runs/results.csv` compares chunking, retrieval, reranker, and k strategies on recall@k, MRR, latency, and cost per query, and RAGAS faithfulness and relevancy for the shortlisted strategies.
- The best configuration is chosen with numbers and frozen in `rag/config.py`.
- `python -m rag.pipeline` answers real questions with document and page citations, and says "not contained" for unanswerable ones.
- Total cloud spend so far is recorded in the cost log.

Cycle 2 is done when:
- The pipeline runs behind API Gateway and Lambda, with Aurora as the database and the page hosted in S3, and the same query tests pass against the deployed backend.
- The web page answers a question end to end with page-level citations.
- The throttling, concurrency cap, and input limits are in place and tested.
- The deployed evaluation records latency and cost per query.
- The evaluation report and a recorded demo are in the repo.
- The teardown checklist is complete and Billing confirms spending has stopped.
