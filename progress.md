# Progress log

One entry per working session, newest first. Each entry records the date, what was done, what was decided, and what is still open. The build plan and current status live in `CLAUDE.md`; this file is the history.

## Your checklist

Hooks (do first):

- [ ] In Claude Code, open `/hooks` once (or restart) so the new Stop hook is loaded.
- [ ] Create a branch, commit, open a PR and merge it. The pre-commit hook runs on the commit. Use `git add --chmod=+x .githooks/pre-commit` so the hook also runs on Linux and macOS clones.
- [ ] Tell Claude when the PR is merged, so the progress-log hook can be added.

To finish Layer 1:

- [ ] Check the 8 remaining table pages listed in `datasheet-rag\runs\parse_review.md` beside the PDF (2 per datasheet), using `python scripts\show_parsed.py <doc> <page>`, and tick them off in that file.

Needed later:

- [ ] Layer 3: start Docker Desktop and run `docker start rag-pg` (the container does not restart on its own).
- [ ] Layer 5: Cohere Rerank 3.5 is a third-party model and may ask for a use-case form or Marketplace subscription on first use. Check its page in the Bedrock console.
- [ ] Layer 7: write about 40 questions by hand in `data/questions.jsonl`, reading the datasheets yourself. This is the largest piece of human work in Cycle 1, so it can be started any time.

Done:

- [x] Four datasheet PDFs gathered.
- [x] AWS account created and Budget alerts set.
- [x] IAM user created and access key saved in `~\.aws\credentials`.
- [x] Database password added to `datasheet-rag\.env`.
- [x] 2026-10-12: the four datasheets confirmed as the final set; first commit pushed to the public repo; Free plan end date confirmed; Budget alerts set up.
- [x] 2026-10-12: administrator access removed from the dev IAM user; it now has an inline Bedrock-only policy (InvokeModel on the four project models, Rerank, ListFoundationModels). Titan still embeds; IAM and S3 calls return AccessDenied.

## What is left

Full detail for every item is in `CLAUDE.md`. A layer is finished only when its test passes.

Start of next session:

1. Add the progress-log hook once the hooks PR is merged.
2. Finish the Layer 1 table review (checklist above).
3. Layer 2 (chunking) starts only when the human says so, in plan mode. Read "Known limits" in `runs/parse_review.md` first: merged cells, two-row headers and figure tables all land on the chunker.

Carried over from Layer 0:

- [ ] Verify the Titan V2 price and update `EMBED_USD_PER_1K_TOKENS` in `rag/config.py` (before Layer 3).

Cycle 1 (local pipeline and evaluation):

- [x] Layer 0: setup and guardrails.
- [ ] Layer 1: PDF parsing. Code and `tests/test_parse.py` done (38 tests pass). Open: the human's share of the manual table review in `runs/parse_review.md`.
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

## 2026-10-12 — Session 2: pre-build checklist and Layer 1 (PDF parsing)

Status at the end: Layer 1 code and automated tests done. The manual table review is one third done (4 of 12 pages). Next is the rest of that review, then Layer 2.

What we did:

- Cleared the pre-build checklist: four datasheets confirmed final, first commit pushed by the human, Free plan date and Budget alerts confirmed.
- Replaced AdministratorAccess on the dev IAM user with an inline Bedrock-only policy. Titan still embeds; IAM and S3 calls return AccessDenied.
- Wrote `rag/parse.py`: text and tables per page, header and footer removal, printed page labels, table captions, output to `runs/parsed/<doc>.json` (each page has `text`, `text_outside_tables` and `tables`). All four files parse in about 30 seconds: 311 pages, 263 tables.
- Wrote `tests/test_parse.py` (38 tests, all pass), `conftest.py` so `import rag` works under pytest, and `scripts/show_parsed.py` for the table review.
- Checked one table page per datasheet against a rendered image of the PDF page. Every value, unit and symbol matched. Recorded in `runs/parse_review.md`.

- Added the hooks: `.githooks/pre-commit` and `tools/checks.py` (secret scan, ruff, pytest before each commit), and a Claude Code Stop hook (`.claude/settings.json`, `tools/review_gate.py`) that reruns the checks and asks for a code review when code has changed. Installed `ruff`, added `ruff.toml`, and fixed the 4 lint findings in the existing code.
- Ran the built-in code review on all of it (10 findings). Fixed 7: header removal now drops only the exact flagged lines; the pre-commit secret scan reads the staged copy of each file; the password pattern covers quoted values and `POSTGRES_PASSWORD` and no longer trips on an empty `KEY=` line; tables that only mention a figure are kept (this restored the revision-history tables: 257 to 263 tables); captions need `Table n.` or `Table n:`; a caption is inherited only by a table at the top of its page; image-only pages print a warning. Left as known limits: a footnote marker directly after a number can read as part of the value; lint and tests in the pre-commit hook run on the working files, not the staged copies; the review gate records a review as requested, not as done.

Decisions:

- Claude builds a layer only when told to, starting in plan mode and building after the plan is approved. Layer 1 was started without that go-ahead; this rule is the correction.
- Build issues are explained in simple language: what, why, how to fix.
- Work reaches GitHub through a PR that the human opens and merges.
- Code review: once per layer, before the PR. After fixing findings, re-run the checks only. A second review only if a fix rewrote logic substantially; never more than two per layer. The Stop hook was rewritten to follow this, and Claude records each review with `python tools/review_gate.py --reviewed`. This layer has both of its reviews recorded.
- The human does all git commits and pushes. `CLAUDE.md` and `progress.md` stay tracked in the public repo.
- `x_tolerance` 1.5 for all files; the pdfplumber default of 3 glued words together in the ESP32 and LM35 files.
- Subscripts are joined to their base character ("VDD", not "V" and "DD" on separate lines).
- µ and Ω are stored in one spelling each; Symbol-font glyphs in the STM32 file are mapped to real characters.
- A header or footer is a line that repeats at the same height in the page margin on at least 3 pages. Table captions and rows inside tables are protected.
- Merged table cells are stored as `null`; filling them down is left to the chunker.

Problems hit:

- None of the datasheets is scanned.
- The Read tool cannot render PDF pages here (no poppler), so pages were rendered with pdfplumber's `to_image` for the visual check.
- Some figures are still parsed as tables (STM32 ballout and timing diagrams, LM35 graph grids). Listed in `runs/parse_review.md`.

Still open:

- The human's 8 pages of the table review.
- Parser decisions from the second review, for the Layer 2 plan: the header rule removes a margin line that repeats on only 3 pages (CLAUDE.md says "most pages"); tables can carry a "Figure n." caption and LM35 graph grids are still kept as tables; the caption fallbacks accept some non-captions ("Sprocket Holes", "Voltage"); a table dropped as a figure grid is not counted anywhere; a scanned PDF with a text stamp on each page would not be detected.
- Hook limit: lint and tests in the pre-commit hook run on working files, not staged copies.
- Titan V2 price (before Layer 3).
- The rerank IAM action names in the new policy are from memory (verify at Layer 5). Cohere may need a one-time Marketplace enablement from the console.

- Added `.github/workflows/tests.yml`: on pull requests into `main` it installs the dependencies and runs ruff and pytest the way `tools/checks.py` does. On GitHub 14 tests run and the 6 datasheet tests skip (the PDFs are not in git).
- Found and fixed a paid-call leak: `pytest -q` was collecting `scripts/test_embed.py` because of its name and running it on import, so every test run (and every hook run) made a real Titan call. Added `pytest.ini` (`testpaths = tests`) and a `__main__` guard in the script.
- Second code review (10 findings, none on the workflow). Fixed 2 in `tools/checks.py`: a failed git command now fails the secret scan instead of passing with nothing scanned, and deleted files count as changes; the scan also catches a Postgres connection URL that has a password in it. The other 8 are parser heuristics and hook limits, left open and listed under "Still open".

Spend this session: 19 single-sentence Titan calls (361 tokens, about $0.000007). One was the intended policy test; 18 came from the leak above.

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
