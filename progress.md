# Progress log

One entry per working session, newest first. Each entry records the date, what was done, what was decided, and what is still open. The build plan and current status live in `CLAUDE.md`; this file is the history.

## Your checklist

Next session:

- [ ] Optional spot check of one table page: from `datasheet-rag` with the venv active, run `python scripts\show_parsed.py lm35 4` and compare it with page 4 of the LM35 PDF (this page had a defect that is now fixed).
- [ ] Optional look at the chunks: from `datasheet-rag`, `python scripts\show_chunks.py lm35 6 structure_aware --tables`.
- [ ] Layer 4 is built and reviewed (2026-10-13): make the branch, commit, open the PR and merge. Optional look first: from `datasheet-rag`, `python scripts\show_search.py` (free now, the vectors are cached) and `runs\search_review.md`.
- [ ] Tell Claude when to start Layer 5 (reranking, single agent). It starts in plan mode. After a restart of the PC, open Docker Desktop and run `docker start rag-pg`.

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
- [x] 2026-10-13: PR #2 (Layer 2, chunking) merged into `main`; the workflow passed on the PR and on the merge; both layer branches deleted on GitHub.
- [x] 2026-10-13: parallel builds kept for Layers 4 and 7.
- [x] 2026-10-13: PR #3 (Layer 3, embeddings and storage) merged into `main`; the branch deleted locally and on GitHub.
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
- [x] Layer 2: chunking. Built and reviewed on 2026-10-13; `tests/test_chunk.py` passes (283 tests) and `runs/chunk_counts.csv` is written. PR #2 merged the same day.
- [x] Layer 3: embeddings and storage. Built, loaded and reviewed on 2026-10-13: row counts match Layer 2 (5,501), `vector_dims` is 1024, a re-run changes nothing and costs nothing. PR #3 merged the same day.
- [x] Layer 4: retrieval. Built and reviewed on 2026-10-13; `tests/test_search.py` passes (168 tests), `EXPLAIN` shows `<=>` in the sort, and the by-eye review of five questions is in `runs/search_review.md`. Waiting for the PR and merge.
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

## 2026-10-13 — Session 5: Layer 4 (retrieval), parallel build in progress

Status at the end: Layer 4 is built, reviewed and ready for the human's branch, PR and merge. The full checks pass with 518 tests (350 existing plus 168 in `tests/test_search.py`). The by-eye review is in `runs/search_review.md`. Next is Layer 5, when the human says so.

What we did:

- Readiness check: `main` clean, `rag-pg` up, 5,501 chunks stored.
- Tried the search rules on the real chunks before writing the contract (read-only queries on `fixed_400` and `page`):
  - Plain `ts_rank` fails the part-number test. For "TCTH022BE supply voltage" the top hit was an STM32 table-of-contents chunk, because `ts_rank` counts matches and does not know that `TCTH022BE` is rare (2 chunks) and `supply` is common. Weighting each term by rarity puts a chunk with the part number first. It runs in 1 to 25 ms.
  - With the rarity weight, no stop-word list of our own is needed, so `english` vs `simple` stays a one-change comparison.
  - One term at a time through `plainto_tsquery` is safe for any input; an empty or all-stop-word question returns no rows.
  - `EXPLAIN` on the vector query shows `Sort Key: (c.embedding <=> ...)`; exact search took 63 ms.
- Plan approved by the human (parallel: two `layer-builder` agents and one `test-writer`).
- Step 0 by the lead, before the agents started (350 tests still pass):
  - `rag/config.py`: `DEFAULT_STRATEGY = "fixed_400"` (a placeholder until Layer 8), `KEYWORD_CONFIGS`, `KEYWORD_CONFIG = "english"`, `RRF_C = 60`, `DETECT_PART = False`, `PART_ALIASES`.
  - `text_hash` and `embed_key` moved from `rag/load.py` to `rag/embed.py` (load imports them), so the query path does not import the PDF and chunking code.
  - The `database` and `conn` fixtures moved from `tests/test_db.py` to a new `tests/conftest.py`.
- `scripts/show_search.py` written by the lead (not run yet): vector, keyword (`english` and `simple`) and hybrid top 5 for five questions. It prints the paid calls and asks first.
- Agents started together: builder A owns `rag/db.py` (`nearest_chunks`, `keyword_chunks`, `explain_nearest`), builder B owns `rag/search.py`, the test writer owns `tests/test_search.py`.

- Agent reports, all three finished with no departure from the contract:
  - Builder A: `nearest_chunks`, `keyword_chunks` and `explain_nearest` added to `rag/db.py`. On the real `fixed_400` rows, "TCTH022BE supply voltage" returns a TCTH chunk first and `EXPLAIN` shows `<=>` in the Sort Key.
  - Builder B: `rag/search.py` with `query_terms`, `rrf`, `detect_part`, `question_vector`, `vector_search`, `keyword_search`, `hybrid_search` and `search`.
  - Test writer: `tests/test_search.py`, 168 tests, all passing; the pure-function tests need no database.
- Integration by the lead: read both builders' code, ran `tools/checks.py` (518 passed), and ran keyword and hybrid searches through the joined code on the real chunks (read-only). Nothing had to be changed to make the pieces fit.
- Noted by builder A for Layer 7: a datasheet filter changes the keyword ranking, because inside one datasheet its own part number is no longer rare.

- By-eye review, after the human's yes: `python scripts\show_search.py` made 5 Titan calls (70 tokens, about $0.0000014, 5 cost log rows); a second run made 0. Hybrid had an answer chunk in the top 5 for all five questions and at rank 1 for three. The two sides failed on different questions: keyword search found the LM35CA accuracy table that the vector side had at rank 10, and the vector side found the quiescent current table for the reworded question. `english` beat `simple` on two questions. The weak one is "TCTH022BE supply voltage": the answer chunk does not contain the part number, so it is only at hybrid rank 4. Written up in `runs/search_review.md`.
- The layer's one code review (code-review skill): 9 findings, none a wrong result on the current data. Fixed 6:
  - `search()` returns `[]` for a question with no words in every mode, and never sends it to Titan.
  - Arguments left as `None` take their value from `rag/config.py` or `embed.embed_text` when the function is called, not at import. So a setting changed later is used, and an embedder replaced in a test cannot be bypassed into a paid call. This changes the default values in the contract (told to the human in the hand-over).
  - `nearest_chunks` orders chunks with equal distance by id, in Python, so the raw `<=>` stays alone in `ORDER BY`.
  - New `search.uncached_questions`, used by `show_search.py` to count paid calls, so the count and the cache use one key.
  - The keyword columns in `rag/db.py` are derived from `config.KEYWORD_CONFIGS`.
  - `show_search.py` explains a Bedrock refusal instead of a traceback; `hybrid_search` computes the ranks once.
- Not changed, recorded in `CLAUDE.md`: `detect_part` matches by prefix (`LM358` would select LM35; it is an experiment, off by default); the question cache is keyed on the exact text and grows with every new question (a Cycle 2 decision).
- Checks after the fixes: 518 passed. Review recorded with `tools/review_gate.py --reviewed`. `python scripts\show_db.py` still ends with "OK".
- `CLAUDE.md` status updated: Layer 4 built, Layer 5 next.

Decisions:

- Keyword rank = sum over question terms of rarity weight × `ts_rank(..., 1)`, where the weight is `ln(1 + (N - df + 0.5) / (df + 0.5))`.
- Tie rule for RRF: score descending, then chunk id ascending.
- Question vectors are kept in `embedding_cache` (`search.question_vector`), as planned for Layer 7.

Still open:

- The human's part: branch, commits, PR and merge for Layer 4.
- Known keyword limits found on the real data, not fixed in this layer: Postgres keeps `VDD/VBAT` as one token (322 such tokens), so `VDD` does not match it; `STM32F205` as an exact word is in 1 chunk (the datasheet writes `STM32F205xx` and `STM32F20x`), and prefix matching is not used because `GPIO1` would then match `GPIO16`; `-40` is stored as a signed token, so `40` does not match it.

## 2026-10-13 — Session 4: Layer 3 (embeddings and storage), in progress

Status at the end: Layer 3 is built, loaded, reviewed and merged (PR #3). 350 tests pass; 5,501 chunks are stored with vectors; the load cost $0.0173. Next is Layer 4, when the human says so.

What we did:

- Plan approved by the human (single agent). Measured while planning: 5,501 chunks over the seven strategies, but only 2,604 different texts, because table chunks repeat across strategies of the same size.
- Checked the Titan V2 request in the Bedrock user guide: `inputText` (one string per request), `dimensions` (1024, 512 or 256), `normalize` (default true); the response has `embedding` and `inputTextTokenCount`. The page does not state the input limit; the longest chunk is 6,476 characters, far below the 8,192 tokens / 50,000 characters usually quoted.
- New `rag/db.py` (all SQL): `connect`, `create_schema`, `upsert_document`, `replace_chunks`, the embedding cache functions, `count_chunks`, `strategy_counts`, `embedding_dims`, `list_documents`, `sample_chunks`, and the test-schema helpers.
- New `rag/load.py`: `plan_load` (no paid call), `load_document`, and the command `python -m rag.load` (prints calls and cost, stops) / `python -m rag.load --run`.
- `rag/embed.py`: `embed_texts` (4 calls at a time, one cost log row per batch), adaptive retries on the client. `rag/costlog.py`: `read_rows`. `rag/config.py`: `EMBED_WORKERS`, `EMBED_BATCH`, `DOCUMENTS`.
- New `tests/test_db.py` (15 tests, throwaway schema, fake embedder, skips without Postgres), `tests/test_embed.py` (6 tests, fake Bedrock), `scripts/show_db.py` (the acceptance check).
- `python -m rag.load` created the three tables in `rag-pg` (free) and printed: 2,604 paid calls, about 666,540 tokens, about $0.0133.

Decisions:

- Vectors that were paid for are kept in an `embedding_cache` table keyed by the SHA-256 of the text. A text is embedded once, a re-run costs nothing, and a stopped run carries on.
- Cost log: one row per batch of up to 100 texts, not one per call.
- Schema against the draft: `documents.name` unique plus a `file` column; `chunks.chunk_index`; two keyword columns `tsv_english` and `tsv_simple`, each with a GIN index, so Layer 4 can compare them without a migration.
- Document details in `rag/config.py`: STM32F205/207 (DS6329 Rev 18), TCTH0xxxE (Rev. 2.0.A), ESP32 (Version 5.3), LM35 (SNIS159H).

Problems:

- A test showed that after one failed embedding call, calls queued behind it could still run (a worker takes its next text before the failure is noticed). Fixed with a shared stop flag in `embed_texts`.
- The TCTH file is named `..._20230425` but its pages print `2026-04-07, Rev. 2.0.A`. The printed revision is what is stored.

- Paid step 1, after the human's yes: `python -m rag.load --run --doc TCTH022BE_datasheet_en_20230425`. 110 calls, 30,087 real tokens ($0.0006), 1.18 times the estimate; 234 rows stored, 7 cost log rows.
- Paid step 2, after a second yes with the corrected figure (2,494 calls, about 755,000 tokens, about $0.015): `python -m rag.load --run` started. It runs at about 1 call per second, because the account's Titan request limit is low and the client slows down when throttled. No call fails and nothing costs more; the load takes about 40 minutes and each batch of 100 is saved as it goes.
- The "Schema" section of `CLAUDE.md` now shows the tables as built (source: `_TABLES` in `rag/db.py`).
- The layer's one code review ran with the code-review skill while the load ran: 10 findings, none a wrong result in the data. Fixed 8: the Bedrock client is made before the worker threads start; a cost log that cannot be written no longer throws away paid vectors (it prints a warning with the tokens); a database error mid-load gets the same plain "run it again" message as a Bedrock error, and the spend summary cannot hide another error; `--run` now asks `[y/N]` before paid calls unless `--yes` is given; a document detail given as None keeps its stored value, and a document missing from `config.DOCUMENTS` prints a warning; `scripts/show_db.py` flags stored rows for a document or strategy that no longer exists, and gives the `docker start rag-pg` hint; `tests/test_db.py` connects in a session fixture, not at import. Not changed: `EMBED_DIMENSIONS` is still fixed in the table columns (the docstring now says a change needs new tables), and each load still asks the cache twice (cheap). Checks pass after the fixes (350 tests); review recorded with `tools/review_gate.py --reviewed`.

- The full load finished (exit 0) after about 45 minutes: 833,043 tokens, $0.0167, 38 cost log rows. With step 1 the layer cost 863,130 tokens, $0.0173, in 45 batch rows. Real tokens were 1.29 times the characters / 4 estimate.
- Acceptance checks, all passed: `python scripts\show_db.py` shows every one of the 28 document and strategy counts equal to Layer 2 (311, 1,038, 1,062, 797, 805, 911, 577; total 5,501) and `vector_dims` 1024. A second `python -m rag.load --run` made 0 paid calls, added 0 cost log rows, and left the counts unchanged.
- The human asked why the load was slow. Answer: batches of 100 arrive exactly 100 seconds apart whatever their token count, so the limit is on requests (about 60 a minute), not on text size or the database. More workers do not help. The quota can be read, and an increase requested, in Service Quotas (Amazon Bedrock, Titan Text Embeddings V2 requests per minute, us-east-1); not checked yet.
- `CLAUDE.md` status updated: Layer 3 done, Layer 4 next.

Still open:

- Done later the same day: the human merged Layer 3 as PR #3 (`4d20ab2`) and deleted the branch locally and on GitHub. Local `main` matches `origin/main`.
- Document ids in `documents` are 1, 8, 22 and 29, not 1 to 4: each repeated upsert uses up a number from the id counter. Harmless; nothing depends on the values.
- Layer 7: at about 1 Titan call per second, 40 questions take about 40 seconds per run unless question vectors are cached. Decide in the Layer 7 plan.
- Confirmed by the human in Service Quotas (us-east-1): Titan Text Embeddings V2 on-demand is 60 requests a minute on this account (AWS default 6,000) and 300,000 tokens a minute, both "Not adjustable". The slow load was this quota, and it cannot be raised from that page. 
- The human then read the other three quotas (requests a minute, all "Not adjustable", AWS default in brackets): Cohere Rerank 3.5 is 3 (250), Nova Lite 100 (2,000), Nova Pro 13 (250), Nova Pro latency-optimized 1 (10, not used). The rerank quota is the tight one: one call every 20 seconds, so 40 questions take at least 14 minutes per reranked run, and the public demo could rerank only 3 questions a minute. Consequences are written into `CLAUDE.md` (Current status): pace and cache rerank calls at Layer 5, limit and cache reranked runs at Layer 7, print time as well as cost in dry runs, and decide the demo's reranker use in the Cycle 2 plan. Seeing a quota row does not prove access to Cohere; the first call at Layer 5 is still the test.

## 2026-10-13 — Session 3: Layer 2 (chunking), the first parallel build

Status at the end: Layer 2 is done and merged (PR #2). Next is Layer 3, when the human says so.

What we did:

- Confirmed on GitHub that the workflow passed for the three pushes to `main` that triggered it (first runs of the push trigger).
- Verified the Titan V2 price from the Bedrock pricing page, pasted by the human: $0.02 per 1M input tokens on-demand, $0.01 batch. It matches `EMBED_USD_PER_1K_TOKENS = 0.00002`; only the comment in `rag/config.py` changed.
- Region: the console opened in Ohio (us-east-2). The console region is only a view; the code sets `us-east-1` in `rag/config.py` and there is no `~\.aws\config` to override it. No regional AWS resource exists yet.

- Set up parallel layer builds at the human's request. Added `.claude/agents/layer-builder.md` and `.claude/agents/test-writer.md`, and a "Parallel layer builds" section in `CLAUDE.md` (which layers split, how a build runs, the rules for the agents). The same two definitions and the rules, with project details removed, went into `..\project-template` (`CLAUDE.md`, `SETUP.md`, `.claude/agents/`). No agent has been run yet.

Decisions:

- The 40 evaluation questions will be written together at Layer 7, not by the human alone.
- Parallel builds: Layers 2, 4 and 7 are built by several agents with Claude as the lead; Layers 3, 5, 6 and 8 and the Cycle 2 steps by one agent unless a plan says otherwise. The lead joins the pieces, runs the full checks and runs the one code review. Agents own separate files in the normal folder (no worktrees, because the parsed files and the venv are not in git), never use git, never make paid calls and never edit the shared files. The test writer works from the plan and does not read the new code. Layer 2 is the trial: keep or drop on the result.

- Git rule tightened by the human: Claude asks before every commit to `main`, small changes included. Reason: Claude pushed two commits to `main` this session (the Titan price note, the parallel-build setup) without saying so first. Written into `CLAUDE.md` and the project template.
- Layer 2 plan approved by the human (chunking, first parallel build): seven strategies, token measure of characters divided by 4, chunks never cross a page, tables written one row per line with their header labels, text labels and units filled down but never numbers, "Figure" tables skipped. Three agents: text builder (`rag/chunk_text.py`), table builder (`rag/chunk_tables.py`), test writer (`tests/test_chunk.py`); the lead writes `rag/chunk_base.py`, `rag/chunk.py` and `scripts/show_chunks.py`.
- Layer 2 build started, in the working folder on `main` with nothing committed (the human will branch, commit and open the PR at the end). The lead wrote `rag/chunk_base.py` (token measure, chunk format), the chunking settings in `rag/config.py`, `rag/chunk.py` (joins the chunkers, writes `runs/chunk_counts.csv`) and `scripts/show_chunks.py`. Three agents were started together: text builder, table builder, test writer.- Text builder reported: `rag/chunk_text.py` written, ruff clean, its own check passed on all four files (no chunk over its size, fixed chunks reproduce each page's words). It tightened the heading rule beyond the plan: a line ending in `:` is rejected, a section number cannot start with 0, a title starting with a unit (`1 Mbyte`) is rejected, and a contents page gives no headings. Known heading misses it reported: all-capital titles (`3.25 SDIO`), titles starting with a digit (`6.3.20 12-bit ADC characteristics`), the unnumbered TCTH sections 1 to 9, and a few numbered footnotes wrongly taken as headings. 
- Table builder reported: `rag/chunk_tables.py` written to the plan, ruff clean, all 259 tables chunk within 256, 400 and 1024 tokens. It also reported that the plan's rules gave wrong output on real tables (see "Problems hit").
- Test writer reported: `tests/test_chunk.py`, 262 tests written from the plan without reading the code; all passed against the builders' first versions.
- Integration by the lead: three rules in `rag/chunk_tables.py` changed (below), the tests for them rewritten and 12 added. `tools/checks.py` passes: 323 tests. `python -m rag.chunk` wrote `runs/chunk_counts.csv`. Table chunks read by eye for STM32 pages 14, 70, 83; LM35 pages 5, 6, 9; ESP32 pages 14, 20, 68; TCTH pages 2, 6. 
- Code review (the one review for this layer, recorded with `tools/review_gate.py --reviewed`): 10 findings. Fixed 6: a group header that starts in the first column (STM32 Table 8, pages 46 to 56: the package names over the pin numbers) is now read as a two-row header, so a pin number says which package it belongs to; a heading row inside a table is no longer filled down into the rows under it; a long cell in a split row is cut between words, not mid-word; `chunk.text_chunks` no longer fails for `page`; a heading with no text of its own joins the block below it (heading-only chunks went from 43 to 17); the `rerank_limited` comment in `rag/config.py` now says that a 400-token chunk can still pass the reranker's limit by the length of the question. Found while checking the first fix: ESP32 page 11 has a figure's text caught in the table's first row, so a group header over 60 characters is now ignored. Not fixed, listed under "Still open": three-row headers, a bus index at the very end of a cell, the slow word loop, two copies of `hard_cut`. One finding was judged not a defect (a `null` in both header rows takes the label on its left).
- Final checks: `tools/checks.py` passes, 329 tests (46 Layer 1, 283 Layer 2).
- Final chunk counts for the four datasheets together (text + table): `page` 311 + 0; `fixed_256` 460 + 578 = 1,038; `fixed_256_overlap` 484 + 578 = 1,062; `fixed_400` 375 + 422 = 797; `fixed_400_overlap` 383 + 422 = 805; `structure_aware` 489 + 422 = 911; `fixed_1024` 306 + 271 = 577. Ten figure tables are skipped (7 STM32, 3 LM35). Per-document numbers are in `runs/chunk_counts.csv`.
- The human branched, committed and merged Layer 2 as PR #2; the workflow passed on the PR and on the merge to `main`. The human then kept parallel builds for Layers 4 and 7, and started `rag-pg` for Layer 3.
- How the parallel build went: the three agents took about 3 to 6 minutes each, side by side, and used about 260,000 tokens between them. The tests written without sight of the code passed first time against both builders, so the contract was clear enough. The table builder's report was the most useful output: it followed the plan and then listed the real tables where the plan was wrong, which a single builder might have quietly worked around. Cost: the lead still had to rewrite three rules and their tests at integration, because the plan's table rules had not been tried on real data first. Lesson for Layer 4: try the contract's rules on real data during planning.

Problems hit:

- Layer 2, merged cells filled into the wrong row. What: on ESP32 page 14 one pin's functions (`ADC1_CH7, RTC_GPIO5`) were copied onto the ten pins below it, and on STM32 page 14 "Yes" was copied into the wrong part columns. Why: the parser writes every merged cell as `null`, whether it is merged with the cell above or the cell to its left, and the plan's rule read every `null` as "same as above". Fix: a `null` is filled from above only when it comes before the first filled cell of its row, or sits in a unit column. Any other `null` is read as part of the cell on its left, and that cell is labelled with all the columns it covers (`LM35 MIN / LM35 TYP / LM35 MAX: ±0.4`).
- Layer 2, group labels on two-row headers. What: STM32 Table 23 labelled the maximum at 25 °C as "Typ", and TCTH page 6 gave the 25 °C maximum the label of the -40 to 125 °C group. Why: the plan shared a group label out evenly over the sub-columns and ignored that a `null` header cell belongs to the header on its left. Fix: a filled header cell names its own column, a `null` takes the group on its left, and only blank cells are shared out.
- Layer 2, footnote stripping removed real data. What: `EMAC_TXD[0](O)` lost its bus index and `2[15]-1` became `2-1`. Why: any `[n]` or `(n)` was treated as a footnote marker. Fix: a marker is removed only at the end of a word, and never when numbered 0.
- Layer 2, a number written with a space after its sign (`≤ 3.6`) was not seen as a number and could be copied down. Fix: the number test allows one space after the sign.

- The Cost and usage box on the console home showed access denied. Why: the human was signed in as the dev IAM user, which has had a Bedrock-only policy since 2026-10-12. Fix: browse the console as root or an admin; leave the dev user narrow.
- `python scripts\show_parsed.py lm35` crashed with `IndexError`. Why: the page number was left off. Fix: `python scripts\show_parsed.py lm35 4`. The script gives no usage message for this case.
- Cohere Rerank 3.5: as root, "Open in playground" and the three-dot menu were greyed out in the Bedrock console. This does not show whether access is granted. Left for the first call at Layer 5.

Still open:

- Cohere Rerank 3.5 access and the rerank IAM action names (Layer 5).
- Parallel builds: for Layer 4, try the contract's rules on real data during planning (the `/agents` command no longer exists; the two definitions loaded without a restart).
- Layer 2 known limits, for Layer 7 to keep in mind:
  - Sections are missed for all-capital titles (`3.25 SDIO`), titles that start with a digit (`6.3.20 12-bit ADC characteristics`) and the unnumbered TCTH sections 1 to 9; a few numbered footnotes are taken as headings (STM32 page 116, ESP32 pages 23 and 25). A check that heading numbers never go backwards would remove those.
  - Tables with three header rows keep only two: the third comes out as a data row (STM32 pages 84, 160, 161; the ESP32 GPIO matrix, pages 63 to 68).
  - A merged cell in a value column is left empty, so some LM35 "Tested Limit" and "Design Limit" lines carry no value. A value that spans columns is labelled with all of them. The real fix is for the parser to record whether a merged cell joins the cell above or the cell beside it.
  - Three uncaptioned figure fragments are still chunked as tables (STM32 pages 103 and 163, TCTH page 8).
  - A bus index at the very end of a cell (`PA[15]`) would be stripped as a footnote marker. None occurs in the four datasheets.

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
