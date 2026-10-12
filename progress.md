# Progress log

One entry per working session, newest first. Each entry records the date, what was done, what was decided, and what is still open. The build plan and current status live in `CLAUDE.md`; this file is the history.

## Your checklist before Layer 1

Nothing here blocks Layer 1. The first group is worth doing before we start; the second group is needed at later layers.

Before we start building:

- [ ] Start Docker Desktop and run `docker start rag-pg` at the start of each session (the container does not restart on its own).
- [ ] Confirm the four datasheets in `datasheet-rag\data\pdfs\` are the final set, or add a fifth now. Adding one later means re-running parsing, chunking and embedding for it.
- [ ] Decide whether to replace the administrator access key with a Bedrock-only key. Recommended: a leaked admin key can create anything in the account. Ask Claude for the policy JSON.
- [ ] Make the first commit, or ask Claude to. Check `git status` first: `.env`, the PDFs and the study guide must not appear.
- [ ] Look up the exact Free plan end date in Billing and write it into `CLAUDE.md` (it currently says "about early April 2027").
- [ ] Confirm the AWS Budget alert emails are reaching your inbox (check for the subscription confirmation email).

Needed later:

- [ ] Layer 1: review at least 3 table pages per datasheet beside the parsed output (`runs/parse_review.md`).
- [ ] Layer 5: Cohere Rerank 3.5 is a third-party model and may ask for a use-case form or Marketplace subscription on first use. Check its page in the Bedrock console.
- [ ] Layer 7: write about 40 questions by hand in `data/questions.jsonl`, reading the datasheets yourself. This is the largest piece of human work in Cycle 1, so it can be started any time.

Done:

- [x] Four datasheet PDFs gathered.
- [x] AWS account created and Budget alerts set.
- [x] IAM user created and access key saved in `~\.aws\credentials`.
- [x] Database password added to `datasheet-rag\.env`.

## What is left

Full detail for every item is in `CLAUDE.md`. A layer is finished only when its test passes.

Start of next session:

1. Start Docker Desktop, then `docker start rag-pg`.
2. Go through the "Before we start building" checklist above.
3. Begin Layer 1.

Carried over from Layer 0:

- [ ] Verify the Titan V2 price and update `EMBED_USD_PER_1K_TOKENS` in `rag/config.py` (before Layer 3).
- [ ] First git commit.
- [ ] Optional: swap the administrator key for a Bedrock-only key.

Cycle 1 (local pipeline and evaluation):

- [x] Layer 0: setup and guardrails.
- [ ] Layer 1: PDF parsing. `rag/parse.py` extracts text and tables per page with pdfplumber, strips repeated headers and footers, and saves `runs/parsed/<doc>.json`. Test: `tests/test_parse.py`, plus the manual table review in `runs/parse_review.md`. Stop if any datasheet is scanned.
- [ ] Layer 2: chunking. `rag/chunk.py` with `page_based`, `fixed` (256 and 400 tokens, with and without overlap), `structure_aware` and `fixed_1024`; tables kept whole or split with repeated headers. Test: `tests/test_chunk.py`, and `runs/chunk_counts.csv`.
- [ ] Layer 3: embeddings and storage. Create `documents` and `chunks` in `rag/db.py`, embed in batches, make the load idempotent. Test: row counts match Layer 2, `vector_dims` is 1024, a re-run does not change the count.
- [ ] Layer 4: retrieval. `vector_search`, `keyword_search` (OR-joined tsquery, `english` vs `simple`) and `rrf` in `rag/search.py`. Test: `tests/test_search.py`, `EXPLAIN` on the vector query, and a by-eye review of five questions.
- [ ] Layer 5: reranking. `rag/rerank.py` calling Cohere Rerank 3.5, at most 100 documents per call, switchable by config. Verify the API name and request fields first.
- [ ] Layer 6: generation and citations. `build_context`, `build_prompt`, `call_llm` (Nova Lite through the Converse API) and `resolve_citations` in `rag/generate.py`. Test: `tests/test_generate.py`, and 3 answerable plus 3 unanswerable questions recorded in `runs/generation_smoke.md`.
- [ ] Layer 7: evaluation. Hand-built `data/questions.jsonl` (about 40 questions), `metrics.py`, `run_eval.py` with a dry run that prints call count and cost, the strategy comparisons, and RAGAS on the best 3 or 4 strategies. Verify the Nova Pro price before any RAGAS run.
- [ ] Layer 8: wrap-up. Pick the best strategy, write `runs/cycle1_summary.md`, freeze the configuration in `rag/config.py`. Test: `python -m rag.pipeline "<question>"` answers three real questions with citations.

Cycle 2 (deploy, UI, report):

- [ ] 1. Aurora smoke test (Serverless v2, minimum 0 ACU, Data API, `CREATE EXTENSION vector`). Fallback: RDS db.t4g.micro.
- [ ] 2. Data API backend in `rag/db.py`; the Layer 4 query tests pass against both backends.
- [ ] 3. Load chunks and vectors into Aurora.
- [ ] 4. Lambda: package the pipeline and `handler.py` with an IAM role, concurrency cap and Aurora-resume retry. Check which Python versions Lambda supports first.
- [ ] 5. API Gateway: route, throttling, daily cap, input and output limits, CORS.
- [ ] 6. Frontend: one HTML page hosted in S3.
- [ ] 7. Deployed evaluation: latency (including cold starts) and cost per query.
- [ ] 8. Evaluation report and recorded demo.
- [ ] 9. Teardown checklist, then confirm in Billing that spend has stopped.

## 2026-10-11 — Session 1: Layer 0 (setup and guardrails)

Status at the end: Layer 0 done. Next is Layer 1, PDF parsing.

What we did:

- Confirmed the Python environment: `.venv` on Python 3.14.6, with `pdfplumber`, `psycopg`, `boto3`, `numpy` and `pytest` importing.
- Created `.gitignore` at the `P2 - RAG Pipeline` root before any other file, then ran `git init` there (branch `main`, no commits yet). Ignored: `.env`, `.aws/`, `.venv/`, `__pycache__/`, `runs/`, `datasheet-rag/data/pdfs/`, `Project2_Study_Guide.pdf`.
- Created the folder layout under `datasheet-rag/` (`rag/`, `rag/eval/`, `data/pdfs/`, `runs/`, `tests/`, `scripts/`) and moved the four datasheets into `data/pdfs/`.
- Wrote `rag/config.py` (region, model ID, N and k, `.env` loader), `rag/costlog.py` (appends to `runs/cost_log.csv`), `rag/embed.py` (one Titan V2 call) and `scripts/test_embed.py`.
- Started the `rag-pg` container. pgvector 0.8.7 is installed and psycopg connects with the password from `.env`.
- Set up AWS credentials. Notepad had saved the file as `[default].txt`; it was renamed to `credentials`.
- Passed the Layer 0 test: `scripts/test_embed.py` printed `1024` and added one row to the cost log.

Decisions:

- The git repo root is `P2 - RAG Pipeline`, so `CLAUDE.md` and `progress.md` are tracked; the code lives in `datasheet-rag/`.
- The datasheet PDFs stay out of git (vendor copyright).

Checked against AWS:

- Titan Text Embeddings V2: model ID `amazon.titan-embed-text-v2:0`; request fields `inputText`, `dimensions`, `normalize`; response fields `embedding`, `inputTextTokenCount`. `normalize` defaults to true.
- Bedrock in us-east-1 lists `amazon.nova-lite-v1:0`, `amazon.nova-pro-v1:0` and `cohere.rerank-v3-5:0`. Only Titan has been invoked.
- Cohere Rerank 3.5 is $2.00 per 1,000 queries, one query covering up to 100 chunks.

Problems hit:

- The first `InvokeModel` call returned "Your account is currently being verified". It succeeded on a retry some minutes later with no change on our side.

Still open:

- The Titan V2 price in `rag/config.py` (`EMBED_USD_PER_1K_TOKENS`) is from memory, because the pricing page did not show the figure. Confirm before Layer 3.
- The access key has administrator access; `CLAUDE.md` calls for a key scoped to the Bedrock models.

Spend so far: one Titan embedding call of a single sentence (see `runs/cost_log.csv`).
