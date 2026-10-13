# Progress log

One entry per working session, newest first. Each entry records the date, what was done, what was decided, and what is still open. The build plan and current status live in `CLAUDE.md`; this file is the history.

## Your checklist

Next session:

- [ ] Optional spot check of one table page: from `datasheet-rag` with the venv active, run `python scripts\show_parsed.py lm35 4` and compare it with page 4 of the LM35 PDF (this page had a defect that is now fixed).
- [ ] Tell Claude when to start Layer 2. It starts in plan mode.

Needed later:

- [ ] Layer 3: start Docker Desktop and run `docker start rag-pg` (the container does not restart on its own).
- [ ] Layer 5: Cohere Rerank 3.5 access is still unconfirmed (the console check on 2026-10-13 was inconclusive). The first rerank call is the test. If it is refused, the human may need to add Marketplace subscribe permissions to the dev user for a short time, or make the first call from an admin identity.
- [ ] Layer 7: write about 40 questions in `data/questions.jsonl`, together with Claude, checking each answer against the datasheets.

Done:

- [x] Four datasheet PDFs gathered.
- [x] AWS account created and Budget alerts set.
- [x] IAM user created and access key saved in `~\.aws\credentials`.
- [x] Database password added to `datasheet-rag\.env`.
- [x] 2026-10-12: the four datasheets confirmed as the final set; first commit pushed to the public repo; Free plan end date confirmed; Budget alerts set up.
- [x] 2026-10-12: PR #1 (Layer 1, hooks, CI workflow) merged into `main`; branch `layer-1-parsing` deleted on GitHub.
- [x] 2026-10-12: the human added themselves to the bypass list of the `main` ruleset, so Claude can push small changes straight to `main`.
- [x] 2026-10-12: table review finished by Claude, 12 of 12 pages: 9 match, 3 have defects.
- [x] 2026-10-12: administrator access removed from the dev IAM user; it now has an inline Bedrock-only policy (InvokeModel on the four project models, Rerank, ListFoundationModels). Titan still embeds; IAM and S3 calls return AccessDenied.

## What is left

Full detail for every item is in `CLAUDE.md`. A layer is finished only when its test passes.

Start of next session:

1. Check on GitHub that the workflow passed for the pushes to `main` (first runs of the push trigger, `ubuntu-24.04` and the v6 actions).
2. Optional: spot-check one table page (checklist above).
3. Layer 2 (chunking) starts only when the human says so, in plan mode. Read "Defects found" and "Known limits" in `runs/parse_review.md` first: those two defects, merged cells, two-row headers and figure tables all land on the chunker.

Carried over from Layer 0:

- [x] Titan V2 price verified on 2026-10-13: $0.02 per 1M input tokens, the same as the value already in `rag/config.py`.

Cycle 1 (local pipeline and evaluation):

- [x] Layer 0: setup and guardrails.
- [x] Layer 1: PDF parsing. `tests/test_parse.py` passes (46 tests) and the manual table review is done: 12 of 12 pages match after the two defects found in it were fixed.
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

## 2026-10-13 — Session 3: checks before Layer 2

Status so far: no layer work yet. Layer 2 waits for the human's go-ahead.

What we did:

- Confirmed on GitHub that the workflow passed for the three pushes to `main` that triggered it (first runs of the push trigger).
- Verified the Titan V2 price from the Bedrock pricing page, pasted by the human: $0.02 per 1M input tokens on-demand, $0.01 batch. It matches `EMBED_USD_PER_1K_TOKENS = 0.00002`; only the comment in `rag/config.py` changed.
- Region: the console opened in Ohio (us-east-2). The console region is only a view; the code sets `us-east-1` in `rag/config.py` and there is no `~\.aws\config` to override it. No regional AWS resource exists yet.

Decisions:

- The 40 evaluation questions will be written together at Layer 7, not by the human alone.

Problems hit:

- The Cost and usage box on the console home showed access denied. Why: the human was signed in as the dev IAM user, which has had a Bedrock-only policy since 2026-10-12. Fix: browse the console as root or an admin; leave the dev user narrow.
- `python scripts\show_parsed.py lm35` crashed with `IndexError`. Why: the page number was left off. Fix: `python scripts\show_parsed.py lm35 4`. The script gives no usage message for this case.
- Cohere Rerank 3.5: as root, "Open in playground" and the three-dot menu were greyed out in the Bedrock console. This does not show whether access is granted. Left for the first call at Layer 5.

Still open:

- Cohere Rerank 3.5 access and the rerank IAM action names (Layer 5).

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
- After PR #1 was merged, added the progress-log hook: `tools/progress_gate.py` as a second Stop hook. It sends Claude back to write or update today's entry in this file when files have changed and the log has no entry for today or is older than the latest change. Tested for each case. It was not given a code review: it is a small tooling change, not a layer.
- The human started the table review and found a split header on LM35 page 6 ("TEST CONDI" | "TIONS"). What: one header cell came out as two. Why: the header spans two columns, but the table was detected with a border through it, so the word was cut there. Fix: `rejoin_split_cells` in `rag/parse.py` reads two cells as one when a word crosses their border. It repaired the same fault on LM35 pages 4, 7, 8, 9 and 10 and dropped four graph grids that had been kept as tables (259 tables now, 39 tests). `scripts/show_parsed.py` now prints an aligned grid with a key (`^` merged, `-` empty).
- Rules added by the human: PRs are for layer builds only and the human handles those; small changes are committed and pushed straight to `main` by Claude; the code review runs once at the end of a layer, called by Claude, never for small changes.
- Noticed, not fixed: LM35 pages 7 and 10 lost their inherited caption after the "top of page" rule (an unnumbered heading sits above the table); two-line vertical labels in the STM32 pin tables read in reverse order ("type Pin" for "Pin type"). Both go into the Layer 2 plan.
- Workflow: added a run on pushes to `main` (skipped when only Markdown changed), pinned the runner to `ubuntu-24.04`, and moved to `actions/checkout@v6` and `actions/setup-python@v6` to clear GitHub's Node 20 warning. Not yet run on GitHub.
- Git rule changed by the human: Claude commits and pushes small changes straight to `main`; the human keeps branches, PRs and merges for layer builds. The first push was rejected ("push declined due to repository rule violations") because `main` has a ruleset that requires a PR; the human added themselves to its bypass list and the push then went through.
- Claude finished the table review at the human's request: rendered the 8 remaining pages as images and compared them with the parsed tables. Match: STM32 71 and 90, ESP32 55, TCTH 2 and 4. Defects: LM35 4 and 6 (a value loses its MIN/TYP/MAX column where the PDF draws no line between those columns, because the parser finds columns from ruled lines) and ESP32 20 (`GPIO16` with footnote 2 reads `GPIO162`, because the raised digit has no space before it). Neither is fixed; both are written up in `runs/parse_review.md` with a fix idea.
- `show_parsed.py` shows an empty cell as `(blank)`, so it cannot be mistaken for a dash printed in the PDF.

- Fixed both table defects at the human's request, the same day. (1) Values with no column lines: `split_values_by_column` gives each number the column it sits under (102 LM35 rows on pages 4, 6, 7, 9, 10). (2) Glued footnote markers: a raised number is written in brackets, `GPIO16[2]`. The first version of each fix broke something else, caught by comparing every changed row before and after: centred values in truly merged cells were moved to one column (STM32 features table, TCTH page 2), and I2C became `I[2]C`. Both were corrected (a centred single number is left alone; a raised number followed directly by a letter is part of a name) and each has a test. The three defect pages were re-checked against the page images and now match. 46 tests pass.
- Made a reusable project template at `..\project-template` (next to this project, not in this repo): the hooks, the checks, the two Claude Code Stop hooks, the GitHub workflow, lint and test settings, a `CLAUDE.md` holding the working rules with a marked place for the blueprint, an empty `progress.md` and a `SETUP.md`. Everything specific to this project was removed. Tested on a fresh copy: checks, secret scan, both gates before and after a first commit.

Spend this session: 19 single-sentence Titan calls (361 tokens, about $0.000007). One was the intended policy test; 18 came from the pytest leak described above.

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
