# Progress log

One entry per working session, newest first. Each entry records the date, what was done, what was decided, and what is still open. The build plan and current status live in `CLAUDE.md`; this file is the history.

## Your checklist

Next session (Cycle 2):

- [x] 2026-10-14: branch `layer-8b-UI` deleted on GitHub (by the human) and locally (by Claude).
- [ ] First thing: find the test that hung during the pre-commit run (see the end of Session 10b).
- [ ] Optional: check that the README and its screenshot look right on GitHub.
- [ ] Open the PR for the web page redesign and README tidy-up (`web/index.html`, `README.md`, `docs/ui.png`, `progress.md`) and merge it.
- [ ] Start Docker Desktop and run `docker start rag-pg` before any session that uses the pipeline.
- [ ] Open the PR for Cycle 2 step 1 (Aurora smoke test) and merge it.
- [x] 2026-10-16: Cycle 2 step 1 console work done (cluster, roles, Data API, secret, inline policy `rag-aurora-smoke`, `.env`).
- [x] 2026-10-16 (done): Before Cycle 2 step 1, have ready:
  - which Python versions Lambda supports;
  - access to the AWS console. The dev IAM user can only call Bedrock, so creating Aurora, Lambda, API Gateway and S3 needs another identity, set up by you in the console. Claude never creates credentials.
- [x] 2026-10-14: PR #9 (Layer 8) merged into `main` (`ead7ff3`).
- [x] 2026-10-14: PR #10 (Layer 8b, web UI and README) merged into `main` (`0988c26`); branch `layer-8b-UI` deleted on GitHub.
- [x] 2026-10-14: port fix pushed to `main` (`5adfa43`).
- [x] 2026-10-14: web UI tried locally by the human.

- [x] 2026-10-14: Python 3.13 installed next to 3.14.
- [x] 2026-10-14: Nova Pro price confirmed on the pricing page ($0.80 / $3.20 per 1M tokens).
- [x] 2026-10-14: Layer 7b smoke run and full judge run approved ($0.87 in total).
- [x] 2026-10-14: PR #8 (Layer 7b, RAGAS judge) merged into `main` (`14dd8f1`).

- [ ] Optional spot check of one table page: from `datasheet-rag` with the venv active, run `python scripts\show_parsed.py lm35 4` and compare it with page 4 of the LM35 PDF (this page had a defect that is now fixed).
- [ ] Optional look at the chunks: from `datasheet-rag`, `python scripts\show_chunks.py lm35 6 structure_aware --tables`.
- [x] 2026-10-14: PR #6 (Layer 6, generation and citations) merged into `main` (`b57b216`).
- [x] 2026-10-14: Layer 7a plan approved (split: 7a now, RAGAS as 7b later; Claude drafts the questions, you check; reranker on for all 7 chunking strategies).
- [x] Superseded: the questions were checked by Claude against the rendered PDF pages, at the human's request, and frozen (Layer 7a). The README says so.
- [x] 2026-10-14: Nova Lite price confirmed on the pricing page; Layer 6 smoke run approved and passed 6 of 6.
- [ ] After a restart of the PC, open Docker Desktop and run `docker start rag-pg`.

Needed later:

- [ ] Layer 3: start Docker Desktop and run `docker start rag-pg` (the container does not restart on its own).
- [x] Layer 7a: approve each paid evaluation run after its dry run (done 2026-10-14; $1.31 in total).
- [x] Layer 7b (RAGAS with Nova Pro): done 2026-10-14, $0.87.

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
- [x] 2026-10-13: PR #4 (Layer 4, retrieval) merged into `main`; the branch deleted locally and on GitHub.
- [x] 2026-10-12: administrator access removed from the dev IAM user; it now has an inline Bedrock-only policy (InvokeModel on the four project models, Rerank, ListFoundationModels). Titan still embeds; IAM and S3 calls return AccessDenied.

## What is left

Full detail for every item is in `CLAUDE.md`. A layer is finished only when its test passes.

Start of next session:

1. Cycle 1 is complete. Cycle 2 step 1 (Aurora smoke test) starts only when the human says so, in plan mode.
2. Read "Open for Cycle 2" in `datasheet-rag/runs/cycle1_summary.md` first. It covers:
   - the reranker's limit of 3 calls a minute for a public demo (throttle, or serve without it: R@5 0.953, MRR 0.834);
   - the caches that need write access;
   - the Lambda Python version.
3. The web page (`datasheet-rag/web/index.html`) and the API (`rag/api.py`, no HTTP) already exist. Cycle 2 step 6 hosts the same page from S3, and the Lambda handler can call `api.ask`.
4. Check on GitHub that the workflow passed for the push of `5adfa43`.

Carried over from Layer 0:

- [x] Titan V2 price verified on 2026-10-13: $0.02 per 1M input tokens, the same as the value already in `rag/config.py`.

Cycle 1 (local pipeline and evaluation):

- [x] Layer 0: setup and guardrails.
- [x] Layer 1: PDF parsing. `tests/test_parse.py` passes (46 tests) and the manual table review is done: 12 of 12 pages match after the two defects found in it were fixed.
- [x] Layer 2: chunking. Built and reviewed on 2026-10-13; `tests/test_chunk.py` passes (283 tests) and `runs/chunk_counts.csv` is written. PR #2 merged the same day.
- [x] Layer 3: embeddings and storage. Built, loaded and reviewed on 2026-10-13: row counts match Layer 2 (5,501), `vector_dims` is 1024, a re-run changes nothing and costs nothing. PR #3 merged the same day.
- [x] Layer 4: retrieval. Built and reviewed on 2026-10-13; `tests/test_search.py` passes (168 tests), `EXPLAIN` shows `<=>` in the sort, and the by-eye review of five questions is in `runs/search_review.md`. PR #4 merged the same day.
- [x] Layer 5: reranking. Built and reviewed on 2026-10-13; `tests/test_rerank.py` passes (32 tests), the two-chunk test passed on the real model, and the five-question review is in `runs/rerank_review.md`. PR #5 merged the same day.
- [x] Layer 6: generation and citations. Built and reviewed on 2026-10-14; `tests/test_generate.py` passes (39 tests), and the smoke run of 3 answerable and 3 unanswerable questions passed 6 of 6 (`runs/generation_smoke.md`). PR #6 merged the same day.
- [x] Layer 7: evaluation. 7a merged as PR #7, 7b (RAGAS) as PR #8 on 2026-10-14. Hand-built `data/questions.jsonl` (about 40 questions), `metrics.py`, `run_eval.py` with a dry run that prints call count and cost, the strategy comparisons, and RAGAS on the best 3 or 4 strategies. Verify the Nova Pro price before any RAGAS run.
- [x] Layer 8: wrap-up. Built on 2026-10-14: `fixed_400_prefix`, hybrid, reranker, k 10 frozen in `rag/config.py`; `runs/cycle1_summary.md` written; `python -m rag.pipeline` answered three real questions with the right pages, refused a no-answer one, and answered a new one. PR #9 merged.
- [x] Layer 8b: local web UI (`python -m rag.server`) and the recruiter README. PR #10 merged on 2026-10-14; port fix `5adfa43`.

Cycle 2 (deploy, UI, report):

- [ ] 1. Aurora smoke test (Serverless v2, minimum 0 ACU, Data API, `CREATE EXTENSION vector`). Fallback: RDS db.t4g.micro.
- [ ] 2. Data API backend in `rag/db.py`; the Layer 4 query tests pass against both backends.
- [ ] 3. Load chunks and vectors into Aurora.
- [ ] 4. Lambda: package the pipeline and `handler.py` with an IAM role, concurrency cap and Aurora-resume retry. Check which Python versions Lambda supports first.
- [ ] 5. API Gateway: route, throttling, daily cap, input and output limits, CORS.
- [ ] 6. Frontend: host the existing `web/index.html` in S3, and point its `fetch` calls at the API Gateway URL.
- [ ] 7. Deployed evaluation: latency (including cold starts) and cost per query.
- [ ] 8. Knowledge Base comparison: a second pipeline on Bedrock Knowledge Bases with S3 Vectors, judged on the same 49 questions (added 2026-10-16).
- [ ] 9. Evaluation report (including the comparison) and recorded demo.
- [ ] 10. Teardown checklist (including the Knowledge Base resources), then confirm in Billing that spend has stopped.

## 2026-10-16 — Session 13: Cycle 2 step 1 (Aurora smoke test), in progress

Done:
- Planned in plan mode and approved. The plan was single agent; the human chose to test both connection ways, preferring direct, and to run the checks from the laptop.
- Checked in the AWS docs:
  - On the Free plan, Aurora PostgreSQL can be created only with "express configuration": max 4 ACU, 1 GB of storage, 2 clusters.
  - An express cluster has no VPC. It is reached through an internet access gateway with IAM-token login only; the Data API can be turned on afterwards, but only with a separate password user kept in Secrets Manager.
  - Auto-pause needs PG 16.3+ or 15.7+. A resume takes about 15 s (30 s or more after a day).
  - Lambda supports `python3.14` (AL2023, until 2029-06-30).
  - Prices: $0.12 per ACU-hour, $0.10 per GB-month, Data API $0.35 per 1M requests.
- Code:
  - `rag/config.py`: the `AURORA_*` settings.
  - `rag/db.py`: `server_info` and `DATA_API_SMOKE`.
  - `scripts/aurora_smoke.py` with three commands: `direct` (runs the real `db.py` SQL in a throwaway schema), `data-api` and `resume`.
  - `.env.example`, and 4 new tests in `tests/test_db.py`.
  - Checks pass: 663 passed; 199 skipped because Docker was not running.
- The human chose not to set up the AWS Agent Toolkit for now. Console work stays with the human.

- Part A was done by the human in the console:
  - cluster `datasheet-rag` with 0–4 ACU, PostgreSQL 17.9, pgvector 0.8.1;
  - roles `rag_app` and `rag_data`;
  - the Data API, turned on with `aws rds enable-http-endpoint` in CloudShell, because the Modify page has no setting for it;
  - the secret `datasheet-rag/rag_data`;
  - the inline policy `rag-aurora-smoke`;
  - the keys in `.env`.
- Live run, all passed (`runs/aurora_smoke.md`):
  - `direct` connected in 5.7 s and ran all of `db.py` on Aurora.
  - `data-api` round-tripped 1024-number vectors.
  - The pause to 0 ACU was seen twice.
  - Resume took 18.8 s by the direct way and 17.6 s through the Data API.
  - The local database is 134 MB, under the 1 GB limit.
  - 862 tests pass with Docker up.
- Problems met:
  - The express cluster's Modify page has no Data API setting; the CLI turned it on.
  - The IAM JSON editor reported a syntax error after a paste; re-pasting a compact version fixed it.
  - `\du` in PostgreSQL 16 and later no longer shows role membership.

- Code review done (1 of 2), 8 of 10 findings fixed:
  - the IAM token is now sent only after the server certificate is checked (`verify-full`). On Windows that needs botocore's CA bundle, because libpq's `system` store is empty there;
  - the search_path SQL moved to `db.use_schema`;
  - one Data API client per run, so the per-call times are now 0.05–0.09 s;
  - an empty `AURORA_PORT` no longer breaks every import.
- Not changed: a missing pgvector stops the direct test, which matches the stop rule. The only error seen while the Data API resumed was `DatabaseResumingException`, so no other retry was added.
- Both live checks were run again and pass. 862 tests pass.

Open:
- The human's branch and PR.
- Step 2 plan: decide direct IAM-token vs Data API (recommendation: direct). If direct, delete the secret, the `rag_data` role and the Data API endpoint.

## 2026-10-16 — Session 12: plan change, Knowledge Base comparison

- Decided (the human): after the deployed evaluation, Cycle 2 builds a second RAG pipeline with Amazon Bedrock Knowledge Bases and an S3 Vectors store, and compares it with ours. Added as Cycle 2 step 8 in `CLAUDE.md`; report and teardown moved to steps 9 and 10, and the teardown checklist and definition of done now cover the KB resources.
- Approach agreed in discussion: same PDFs, questions and models where possible; compare retrieval (recall@k, MRR) and end-to-end answers (citations, refusals, RAGAS if affordable); reuse `metrics.py` by mapping KB results to `(document, page)`.
- Open, all VERIFY when step 8 is planned: which models the KB accepts, the page-number metadata field, whether S3 Vectors supports hybrid search, KB parser options and prices, S3 Vectors price. The human creates the KB resources and service role in the console.
- No code changed. Next is still Cycle 2 step 1 (Aurora smoke test), in plan mode, when the human says so.

## 2026-10-15 — Session 11: web page redesign

- The human did not like the page: too dark, green, confusing for a recruiter. `datasheet-rag/web/index.html` was rewritten. The JavaScript behaviour and the API are unchanged, and no test depends on the markup.
  - Light theme only, with a blue accent and no green. One font (Inter) instead of JetBrains Mono and IBM Plex Sans.
  - New layout: a top bar (name and a "Source code" link to the GitHub repo), a centred heading, one search box with the datasheet filter and the Ask button inside it, three example questions, and a three-step "how it works" row before the first question. Answers show the question, the answer, and the sources in a column on the right (stacked on phones).
  - Removed: the tech-stack footer (pdfplumber and the other names), the cost note, the evaluation figures in the header, the pipeline steps while loading, and the timing row. Time, cost, passages read and model are now in a collapsed "Response details".
  - Plain wording: "The datasheets don't cover this." for a refusal, "Page 18 (printed 17)" on sources.
- Checked first with sample data while Docker Desktop was off, then by the human on the real server ("perfect").
- `docs/ui.png` retaken in the new design: one real answer from the server ("How much on-chip SRAM does the ESP32 have?", $0.0002), rendered with headless Edge at 1280 x 840. The README alt text now says one cited page.
- README tidied at the human's request: the question-checking sentence now ends "every answer and page was verified", the SHA-256 sentence is gone, and the Datasheets section ends at "save them in `datasheet-rag/data/pdfs/`" (the table below it now has a Datasheet column and a Link column with the human's download links; the File column is gone).
- Problem: the page vanished on refresh because no server was running (starting Docker starts only the database). Restarted with `python -m rag.server`.

## 2026-10-14 — Session 10b: Layer 8b (local web UI, README)

Status at the end: merged as PR #10 (`0988c26`). The port fix found after the merge was pushed straight to `main` with the human's yes.

Done:
- Asked by the human: everything should run locally with a UI like Cycle 2's, and the repo should have a README for recruiters (modelled on the Project 1 README, with no Cycle 2 talk). The plan was approved after the human installed UI skills (`ui-ux-pro-max`, then `web-design-guidelines` and `writing-guidelines`).
- `rag/api.py` (JSON API without HTTP), `rag/server.py` (standard-library server on 127.0.0.1 only, with a Host check, a JSON-only POST, a PDF allow-list, and reconnect after Postgres restarts), `web/index.html`, `datasheet-rag/requirements.txt`, `docs/ui.png`, `README.md`.
- In `pipeline`, `warnings`, `logged_rows` and `spent_since` are now public. New settings `WEB_DIR`, `SERVER_*`, `API_MAX_*`.
- Page design comes from `ui-ux-pro-max`:
  - Swiss minimal style, dark by default, slate with a green accent;
  - JetBrains Mono and IBM Plex Sans; Lucide icons;
  - tokens in three layers, primitive → semantic → component.
- The `web-design-guidelines` audit found 13 issues, all fixed:
  - skip link;
  - `theme-color` and `color-scheme`;
  - `touch-action`;
  - explicit colours on `<option>`;
  - `translate="no"` on code tokens;
  - tabular numbers;
  - `min-width: 0` on a flex child;
  - a pulse animation (opacity only) instead of the background-position shimmer;
  - curly quotes and the ellipsis;
  - a non-breaking space in "p. 30";
  - Title Case;
  - deep links (`?q=&doc=`).
- `writing-guidelines`: 12 fixes applied to the README.
  - One was undone: a password placeholder without angle brackets set off our own secret scan, so `<your_db_password>` stays.
- Tests: `tests/test_api.py` (24) and `tests/test_server.py` (16). 838 pass in all.
- Problems:
  - Two server tests failed now and then with `ConnectionAbortedError`. When the server answered (413, 403) before reading a POST body, Windows reset the connection. `do_POST` now always reads the body (up to 1 MB) first. The server tests passed three runs in a row afterwards.
  - The old one-line `README.md` was UTF-16, and the rewrite kept that encoding. It is now UTF-8.
  - The Chrome extension was not connected, so the UI was checked with `curl` and headless Edge. Headless Edge will not lay a page out narrower than 492 px, which made the 390 px screenshot look cut off; at 500 px it fits.
- Real checks, paid, about $0.012 in all:
  - through the API: ESP32 SRAM p26; LM35 with the datasheet filter p4; TCTH I2C refused; a new question (ESP32 CPU 240 MHz, pp. 30/26/31; it waited 23 s for the rerank quota);
  - screenshots in dark and light;
  - Postgres stopped: 503 with the Docker hint, and it recovered after `docker start`;
  - a path trick on `/pdfs/` gives 404.

- Code review 1 of 2 for Layer 8b (recorded). Layer 8 was merged by the human as PR #9 (`ead7ff3`) during this session, so 8b is its own layer and PR. 10 findings:
  - Fixed:
    - A `?q=` link asked its question as soon as the page opened, so a link from any site could spend money without a click. The link now only fills in the form, and asking waits for the Ask button.
    - The page found `[n]` with its own regex, so a footnote marker copied from a datasheet (`GPIO16[2]`) could show as a citation. `generate.citation_spans` now applies the server's rules, and the API returns `answer_parts`, which the page draws from.
    - A non-text `document` (a list or a number) crashed `api.ask`; it now gets 400.
    - Any unexpected error in a request closed the connection with no answer. The server now returns a JSON 500 and prints the traceback.
    - A client could stall the single-threaded server forever. `Handler.timeout` is now 15 s.
    - An IPv6 Host header without a port (`[::1]`) was refused. Hosts are now parsed with `urlsplit`.
    - The PDF allow-list was read from Postgres on every PDF request. It is now read once.
    - The "spent since" logic was in two places. It is now `costlog.row_count` and `costlog.spent_since`, used by `pipeline`, `api` and `run_eval`.
  - Not fixed, reported:
    - Each question reads the cost log twice (about 3,000 rows, a few milliseconds).
    - The cost shown also counts rows another process writes during the request (noted in `costlog.spent_since`).
  - New tests: 9 in `test_api.py`, 13 in `test_server.py`. Full checks pass: 857 tests.
  - Rechecked on the running server:
    - the page's script parses (`node --check`);
    - a `?q=&doc=` link fills in the form and the datasheet and asks nothing;
    - `answer_parts` comes back for a cached question ($0.0003).
- On the human's second `/claude-in-chrome` the extension connected. The page was tested in real Chrome, about $0.0004:
  - an example chip asked its question (ESP32 SRAM, 520 KB, p. 26);
  - the citation badge highlighted its source card;
  - "Open PDF" opened the datasheet at page 26 of 78, which shows the 520 KB line;
  - a typed question sent with Enter: STM32F205 maximum junction temperature, 125 °C, p. 70;
  - no console errors.
  - A demo GIF, `datasheet_qa_demo.gif`, was downloaded to the browser's download folder; it is not in the repo.
- The human chose to keep the screenshot in the README rather than a GIF. A re-recording without the watermark was aborted.
- Problem found: two servers ran on port 8000 at once (the human's and Claude's).
  - Why: Python's `HTTPServer` sets `SO_REUSEADDR`, and on Windows that lets a second program bind a port already in use; requests then reach either server.
  - Fix: `allow_reuse_address` is off on Windows (`server._Server`), so a second start fails with "Cannot listen on port 8000". Checked against the human's running server, plus a test. 858 tests pass.

- End of session: the human deleted `layer-8b-UI` on GitHub; the local branch remains (optional `git branch -D`). The checklist and "What is left" are rewritten for Cycle 2. `CLAUDE.md` Cycle 2 step 6 now builds on the existing page, and notes that the PDF links need a decision (vendor copyright).

- Problem, not fixed: on the last commit (Markdown only), the pre-commit `pytest -q` hung for more than 6 minutes; a normal run takes about 75 s. Two full runs passed earlier the same evening. This time the human's own `rag.server` was running in another tab, so suspect a test that touches Postgres or the server. That commit was made with `--no-verify` at the human's request ("just force it"). Next session: run `pytest -q -o faulthandler_timeout=60` to see which test hangs, and fix it.

Still open: the hanging test above. Cycle 1 is complete; Cycle 2 starts in plan mode when the human says so.

## 2026-10-14 — Session 10: Layer 8 (Cycle 1 wrap-up)

Status at the end: Layer 8 built and checked on real questions; code review running. Ready for the human's branch, commit and PR.

Done:
- Plan approved (single agent). Decisions (human): `python -m rag.pipeline` does not ask before paid calls and prints what it spent afterwards; the reworded-refusal miss stays a known limit (`is_not_contained` unchanged).
- Chosen and frozen in `rag/config.py`: `fixed_400_prefix`, hybrid, `english`, N 30, reranker on, `TOP_K` 10, `DETECT_PART` off, new `SEARCH_MODE = "hybrid"`. Reason: best on every check in Layer 7 (40/43 correct page cited, 2 false refusals, faithfulness 0.972, relevancy 0.859, R@10 1.000). `rag/eval/experiments.py` `BASELINE` unchanged, so past runs are reproducible.
- Question set frozen: `config.QUESTIONS_SHA256` and a test in `tests/test_questions.py`. The hash is over the text with "\n" line endings, because Git on Windows checks the file out with "\r\n" and CI on Linux with "\n".
- `show_search`, `show_rerank`, `show_generate` accept the prefix strategy in `--strategy`, as their default is now the prefix strategy (first through `config.SEARCHABLE_STRATEGIES`, replaced after the review by `load.strategy_names()`).
- `rag/pipeline.py`: `answer_question(conn, question, document=None, ...)` calls search, rerank, generate in order and adds `passages` and `timings_ms`; `format_answer`; CLI with `--document`. `tests/test_pipeline.py` (10 tests, fakes only).
- Problem: `tests/test_rerank.py::test_k_defaults_to_top_k` failed after `TOP_K` became 10, because it sent only 9 candidates. Fixed in the test (it now sends `TOP_K + 4`). Full checks pass: 797 tests.
- Pipeline check, paid, $0.0033 in all: q01 LM35 35 V p4, q18 ESP32 520 KB p26, q29 STM32 120 mA p70, q47 refused with no sources, and a new question (TCTH022BE operating temperature, −40 to 125 °C, p2, checked in the parsed page). `show_db.py` still OK.
- `runs/cycle1_summary.md` written: setup, decision with numbers, pipeline check, failures by type, known limits, spend ($2.23 for Cycle 1), open items for Cycle 2.
- `CLAUDE.md` updated: Layer 8 status, and the "Candidates and k" decision row now says k 10.
- Code review (1 of 2, recorded), 9 findings:
  - Fixed:
    - `python -m rag.load` with no `--strategy` left out the frozen `fixed_400_prefix`, so a fresh load (for example into Aurora) would make the pipeline find nothing. The default now adds `DEFAULT_STRATEGY`; the dry run shows 0 paid calls locally, because everything is cached.
    - An empty search printed the same "not contained" answer as a real refusal. `format_answer` now warns that no chunks were found (no searchable word, or the strategy is not loaded).
    - A reranker `RuntimeError` was reported as an answer-model failure; the message now names neither.
    - An unreadable cost log (open in Excel) stopped the pipeline before it answered; now it answers and says the log could not be read.
    - `show_generate` estimated 3,000 input tokens per answer; at k 10 it is 4,500 (Layer 7 measured about 4,300).
    - `config.SEARCHABLE_STRATEGIES` was removed; the scripts use `load.strategy_names()`, the one list of loadable strategies.
  - Not fixed, reported:
    - "Spent" counts cost log rows another process writes meanwhile (noted in `_spent_since`).
    - An empty question would call Titan: not so, `search.search` returns no hits for a question with no words and calls nothing; the new warning covers it.
    - `test_frozen_setup` repeats the values: on purpose, so a change of the frozen setup is deliberate.
  - `tests/test_pipeline.py`: 12 tests. Full checks pass (799 tests).

Still open: Cycle 2 starts only when the human says so, in plan mode. First decisions there: reranker quota (3 a minute) for the demo, cache writes, Lambda Python version.

## 2026-10-14 — Session 9: Layer 7b (RAGAS) built and run

Status at the end: Layer 7b built, run on the shortlist ($0.87) and reviewed by hand; code review pending. The notes below are in the order the work happened.

Done:
- Checked before planning: RAGAS 0.4.3 (latest) needs `scikit-network`, which has wheels only up to Python 3.13, none for 3.14, and only 3.14 is installed. RAGAS reaches Bedrock through LiteLLM (`llm_factory("bedrock/<id>", provider="litellm", client=litellm.completion)`). Nova Pro price from third-party pages: $0.80 / $3.20 per 1M input / output tokens (not yet confirmed on AWS).
- Decisions (human): a second venv `.venv-ragas` on Python 3.13 just for RAGAS; judge the exact Layer 7a answers of the four shortlisted setups, with passages rebuilt from the caches (no Nova Lite or Cohere call). Single-agent build: three small files, and the real RAGAS API must be read first.
- `rag/eval/judge_inputs.py`: `load_answers`, `rebuild_passages` (free: cached question vector, cached rerank scores, a rerank client that fails if used), `strip_labels`, `build_sample(s)` (answerable questions only, citations checked against the 7a CSV), `plan_judge`. `tests/test_judge.py`: 18 pass.
- Rebuild check on the real data: all 172 answers (43 × 4 setups) rebuild with the same citations; refusals 2 / 6 / 6 / 7 as in 7a.
- Settings in `rag/config.py`: `JUDGE_*` (price marked VERIFY), `RAGAS_DIR`, `RAGAS_RESULTS` (`runs/results_ragas.csv`, kept in git), `RAGAS_SHORTLIST`. `.venv-ragas/` added to `.gitignore`.

Problem found: the 7a CSV for `gen_prefix_k5` q34 lists a page for a refusal that ended with "[1]" (written before `generate` cleared a refusal's sources). The rebuild check accepts a refusal with or without those labels.

Later in the session:
- The human installed Python 3.13.15 and confirmed the Nova Pro price on the Bedrock pricing page: $0.80 / $3.20 per 1M input / output tokens (now marked verified in `rag/config.py`).
- `.venv-ragas` (Python 3.13) made; pins in `datasheet-rag/requirements-ragas.txt`. Problem: RAGAS 0.4.3 failed on import (`langchain_community.chat_models.vertexai` missing) because pip installed LangChain 0.4, which removed it, and RAGAS does not pin LangChain. Fixed by pinning LangChain to 0.3 and removing the leftover langgraph packages; `pip check` is clean. All 777 tests pass on 3.13 too.
- Read from the installed RAGAS source: `ragas.metrics.collections.Faithfulness` (2 judge calls: statements, then one verdict with reason per statement against all passages joined; NaN when no statement) and `AnswerRelevancy` (`strictness` calls, each writing one question from the answer, cosine with Titan vectors; 0 when noncommittal). Metrics call the LLM asynchronously; `llm_factory(..., provider="litellm", client=<async completion>, adapter="instructor")` uses instructor's JSON mode and sends `temperature`, `top_p` 0.1, `max_tokens`, `response_format` json_object.
- Decision: answer relevancy strictness 1, not RAGAS's default 3: at temperature 0 the three calls would write the same question. To be stated in the review.
- `rag/eval/ragas_run.py`: `logged_completion` (calls 5 s apart, throttling retried with doubling waits, two cost log rows per call), `make_scorers` (Nova Pro through LiteLLM, Titan through our cached `question_vector`), `judge_sample` (keeps statements and reasons of unsupported ones), resumable per-answer CSV `runs/ragas/<experiment>.csv`, summary `runs/results_ragas.csv`, CLI with `--shortlist`, `--limit`, `--dry-run`, `--yes`. `tests/test_judge.py` now 23 tests (no RAGAS import, runs in CI). A free end-to-end check through the real RAGAS code with a fake LiteLLM function passed.
- Dry runs: smoke (3 answers) 9 Nova Pro calls, about $0.02; full shortlist 172 answers, 516 calls, about $0.95 and 46 minutes.

- Smoke run (human's yes): 3 answers of `gen_prefix_k10`, 9 Nova Pro calls, $0.0203 (estimate $0.0209). Nova Pro answered RAGAS's JSON requests through LiteLLM with no retry. Real tokens per answer about 6,900 input and 380 output (estimate 6,700 and 500). Faithfulness 1.0 for all three. Relevancy 1.0 for all three with no Titan call logged: the question written back from each answer was already in `embedding_cache`, so it matched one of our questions word for word (Nova Lite's answers restate the question). To check this in the full run, `ragas_run` now saves that question and the noncommittal flag (a second judge LLM object for relevancy records its replies; RAGAS still computes the score). The 3 smoke rows have the old columns, so they were moved to `runs/ragas/smoke1_gen_prefix_k10.csv` and are judged again in the full run.

- Full run (human's yes): 172 answers, 516 judge calls, no retry or throttling, $0.85 (estimate $0.95), about 33 minutes. 7b total $0.87. Results in `runs/results_ragas.csv` and `runs/ragas_review.md`: `gen_prefix_k10` best (faithfulness 0.972, relevancy 0.859; others 0.83–0.86 / 0.78–0.79). The means follow 7a's false refusals, which the judge scores 0. Over answered questions faithfulness is 0.97–0.995 everywhere; relevancy does not separate setups. The judge found a wrong answer that 7a counted as correct (q08, k 5 setups: LM35 given the LM35A's ±0.2 °C) and a self-contradicting one (q25 at k 10). Judge errors: q33 refusal scored 0 in two setups although its own reason supports it; q34 reason wrong; mis-citations are invisible to it.
- Read by hand: every answer below full marks and every refusal (about 40 rows), plus 8 answers scored 1.0 (all right).
- `CLAUDE.md` updated (status, test commands).

- Code review (1 of 2, recorded), 10 findings:
  - Fixed:
    - The dry run always said "0 already judged".
    - An answer made only of labels would have paid for faithfulness calls and then crashed. It is now refused before any call.
    - `strip_labels` reflowed the whole answer and could join a decimal (".5" became "is.5"). It now removes only the labels and the spaces before them. Checked on all 172 judged answers: no text changed beyond line breaks, so the scores stand.
    - The rebuild check compared citations only, so a refusal could be judged against other passages. It now also requires the top 10 ranking to equal the 7a CSV's `rerank_hits`. All 172 pass.
    - A rebuild error gave a traceback; it now gives a "Cannot rebuild" message.
    - `JUDGE_MIN_INTERVAL_S` is now worked out from `JUDGE_PER_MIN`.
    - The judge cost formula is written once (`judge_inputs.judge_usd`).
    - The done-ids set is built once.
  - Not fixed, reported:
    - Re-running a 7a generation experiment keeps the old judged rows. Delete `runs/ragas/<experiment>.csv` first; this is noted in `CLAUDE.md`.
    - `judge_inputs` reuses private helpers of `run_eval`. The ranking check now catches any drift between the two.
  - `tests/test_judge.py`: 28 tests. Full checks pass (787 tests).

- PR #8 merged by the human (`14dd8f1`).

Still open: Layer 8 (wrap-up), in plan mode, when the human says so.

## 2026-10-14 — Session 8: Layer 7a (evaluation), parallel build in progress

Status at the end: the plan is approved and the build is under way. Agents A (metrics, question loader) and B (runner, experiments, HNSW functions) are done and pass their own checks; the test writer is still working. The question draft is waiting for the human's check. No paid call was made.

Decisions (with the human):
- Layer 7 is split. 7a: questions, metrics, `run_eval`, every retrieval comparison, answers and cheap answer checks for a shortlist. 7b: RAGAS with Nova Pro, planned later, because RAGAS on Python 3.14 and the Nova Pro price are not checked yet.
- Claude drafts the questions from the datasheet text (not from search results); the human checks each one against the PDF.
- The reranker is on for all 7 chunking strategies (about 280 rerank calls, about 95 minutes at 3 a minute, about $0.57).
- One experiment run gives two result rows: the search order (reranker off) and the reranked order (on). The reranker keeps 10 in eval, so recall@1/3/5/10 and MRR@10 all come from one list at no extra cost.
- Experiments are a Python dict in `rag/eval/experiments.py`; no YAML (PyYAML is not installed and is not needed). CLI: `python -m rag.eval.run_eval <names> | --group <g> | --all [--dry-run] [--yes] [--questions PATH]`.
- HNSW is measured without changing the database: the index is built inside a transaction that is always rolled back. pgvector 0.8.7 is installed, so `hnsw.iterative_scan` keeps the strategy filter from returning too few rows. Agent B found that Postgres also needs `enable_sort = off` to use the index; with it, HNSW returned the same 30 of 30 chunks as exact search on a check question.
- Header prefix: strategy `<name>_prefix` stores the same chunks, embedded with "part (document). section" on a line before the text (`load.embed_input`, `load.strategy_inputs`, `config.HEADER_PREFIX`). A plan-only run for `fixed_400_prefix`: 769 texts, about $0.004.

Files so far: `rag/eval/questions.py`, `rag/eval/metrics.py`, `rag/eval/experiments.py`, `rag/eval/run_eval.py`, HNSW functions in `rag/db.py`, eval settings and `HEADER_PREFIX` in `rag/config.py`, the prefix in `rag/load.py`, `runs/questions_draft.md` (49 draft questions).

Problems: printing datasheet text to the Windows console failed on symbols such as Ω (the console uses an old character set). Worked around by writing the text to files; no project code affected.

Integration (later the same session): all three agents reported; `tools/checks.py` passes with 752 tests (163 new in `test_metrics.py`, `test_questions.py`, `test_run_eval.py`). Two fixes:
- `run_eval._upsert` put brand-new result rows at the place of the first replaced row; the contract (and the test) say new rows go at the end. Fixed: a replaced row keeps its place, a new key is appended.
- `db.hnsw_index_count` read `pg_indexes`, which builds the definition text of indexes in every schema and failed once when another test run dropped its throwaway schema at the same time. It now reads `pg_index`/`pg_class`/`pg_am` for the current schema only.
- Not covered by tests, by choice: `main`/the CLI (checked by hand with the dry run) and `write_by_type` (its signature was left to the builder).

Questions (later the same session): the human asked Claude to do the check. Claude rendered the 33 cited PDF pages to images with pdfplumber (the PDF reader of the tools needs poppler, which is not installed) and compared every table value. All 49 matched except q32 (STM32 junction temperature), which now also cites page 71 and gives the per-suffix range. All 49 are in `data/questions.jsonl` and load cleanly: 22 table lookups, 10 single values, 6 rephrased, 5 comparisons, 6 no answer. Side finding: the extracted text of the second table on TCTH page 9 swaps two α column labels (no question uses it).

Dry run of `--all` (no call made, cost log unchanged): 49 embeds, at most 686 rerank calls ($1.37, about 230 minutes). That count is an upper bound, because before the question vectors exist the dry run counts one rerank call per question per experiment; experiments on `fixed_400` share most candidates. `header_prefix` searches nothing until `fixed_400_prefix` is loaded.

Run 1 (approved by the human): baseline plus the six chunking experiments, 49 embeds and 343 rerank calls, $0.686, about 115 minutes. recall@5 / MRR@10 on the 43 answerable questions, search order then reranked: baseline `fixed_400` 0.930 / 0.766 then 0.977 / 0.855; `page` 0.837 / 0.651 then 0.860 / 0.767; `fixed_256` 0.930 / 0.767 then 0.953 / 0.834; `fixed_256_overlap` 0.930 / 0.748 then 0.930 / 0.841; `fixed_400_overlap` 0.907 / 0.765 then 0.953 / 0.843; `structure_aware` 0.953 / 0.770 then 0.977 / 0.844; `fixed_1024` 0.930 / 0.749 then 0.977 / 0.865. One question is 0.023, so most gaps are one or two questions. `page` is clearly worst; the reranker lifts MRR by about 0.08 everywhere. q33 (five-volt-tolerant pin, rephrased) is missed by every strategy; q42 (LM35 vs TCTH supply) is missed at 5 by four. `fixed_400` stays the base for the next runs.

Dry run for Run 2 (`vector_only`, `keyword_only`, `simple_hybrid`, `simple_keyword_only`, `detect_part`, `hnsw`): 0 embeds, 283 rerank calls, $0.566, about 94 minutes. More than the plan's ~160, because each search mode brings new candidates to score.

Run 2 (approved): $0.430 spent (below the estimate, because more scores were already cached than the dry run counted), about 80 minutes. Search order then reranked, recall@5 / MRR@10: `vector_only` 0.814 / 0.674 then 0.977 / 0.848; `keyword_only` 0.860 / 0.665 then 0.977 / 0.874; `simple_hybrid` 0.907 / 0.688 then 0.953 / 0.855; `simple_keyword_only` 0.767 / 0.556 then 0.930 / 0.833; `detect_part` 0.930 / 0.774 then 0.977 / 0.871; `hnsw` identical to `vector_only`, index recall 0.995, and no HNSW index left afterwards. Read: hybrid is the best search order (0.930 at 5 against 0.81 and 0.86), but after reranking hybrid, vector and keyword all reach 0.977, so the reranker hides the gap at k = 5; `english` beats `simple` in both modes; `detect_part` gives the best search-order MRR and does not lose a question. Vector search finds q33 nowhere and keyword search finds q42 nowhere, which is why hybrid has both.

Header-prefix load dry run (`python -m rag.load --strategy fixed_400_prefix`): 797 chunks, 769 texts to embed, about $0.004 and about 13 minutes at 60 calls a minute.

Header prefix (approved): load $0.0049 (245,249 real tokens, 1.3 times the estimate as at Layer 3), 797 chunks stored; run 42 rerank calls, $0.084. Search order recall@1 / @5 / MRR@10 0.767 / 0.953 / 0.834 against 0.651 / 0.930 / 0.766 for plain `fixed_400`, the largest search-order gain of any experiment; reranked 0.977 / 0.865 against 0.977 / 0.855. Only q33 is missed after reranking.

Generation group added to `rag/eval/experiments.py`: `gen_baseline`, `gen_fixed_1024`, `gen_detect_part`, and `gen_prefix_k3` / `_k5` / `_k10` (k on the header prefix, the best search order). Dry run: 294 Nova Lite calls, no rerank or embed call (all cached), about $0.042.

Generation run 1 (approved): 294 Nova Lite calls, $0.050. Reading the flagged answers found two problems, both fixed with the human's yes:
- Nova Lite sometimes writes the refusal sentence and then labels (`... information. [1] [2] [3]`); `is_not_contained` called that an answer, so "unanswerable refused" showed 67–83 % when it was 6 of 6 everywhere, and refusals counted as citation hits. Fix: `is_not_contained` is also true when the answer minus its labels is just the sentence; `citation_hit_rate` counts a refusal as a miss. Tests added.
- Nova Lite refused answerable questions whose answer was in a passage (q25: passage [2] gives tRST 50 µs). The prompt now has a rule to read every passage, tables included, and answer if one gives the fact even in other words, and the sentence is to be given with no citation.
Recount of run 1 under the fixed check (right page cited / answerable refused / unanswerable refused): baseline 36/43, 7, 6/6; fixed_1024 34/43, 7, 6/6; detect_part 36/43, 7, 6/6; prefix k3 36/43, 5, 6/6; prefix k5 37/43, 6, 6/6; prefix k10 40/43, 3, 6/6. Its per-question answers are kept in `runs/eval/gen_old_prompt/`. Also seen: q02 answered 67 µA (the LM35A limit, not the LM35's 80 µA) with fixed_1024 and prefix k3; q24 found the right page but not the chunk with the DAC sentence.

Generation run 2 (new prompt and check): $0.051. Right page cited / answerable refused / unanswerable refused: baseline 36/43, 7, 6/6; fixed_1024 36/43, 6, 6/6; detect_part 37/43, 6, 6/6; prefix k3 34/43, 7, 6/6; prefix k5 37/43, 6, 6/6; prefix k10 40/43, 2, 5/6. The one miss is q44, a reworded refusal ("Sorry, I cannot provide ...") the check does not catch, so it is really 6/6. The prompt change made no measurable difference (moves of one or two questions both ways); it is kept but not counted as an improvement. False refusals (q22, q24, q25, q27) remain the main answer failure.

Bug found while reading the results: `runs/results_by_type.csv` had only the `comparison` rows, because the merge key was (experiment, rerank) and each type replaced the one before. Fixed: `_key` in `run_eval.py` now includes the type, with a test. The file was rebuilt from `runs/eval/*.csv`, with no call: 160 rows.

`runs/eval_review.md` written: the full results table, failures by question type, a read of the flagged answers, and the 7b shortlist (prefix k10, prefix k5, detect_part k5, baseline). Layer 7a total spend $1.31, of which $1.20 was reranking.

`CLAUDE.md` updated: the Layer 7a status (files, results, shortlist, Layer 6 changes, spend), "Next" set to Layer 7b, and the `run_eval` test commands.

Code review of Layer 7a (built-in skill, review 1 of 2, recorded with `tools/review_gate.py --reviewed`). Ten findings. Fixed:
- HNSW `search_ms` timed the throwaway index build; it is now left empty.
- `plan_run` planned HNSW rerank calls from exact search; it now plans them from the HNSW hits. A dry run of `hnsw` shows 3 calls the old plan missed. Run 2 still spent less than its estimate.
- `scripts/show_db.py` called stored `_prefix` strategies STALE. It now checks them against their base strategy's count and ends with OK.
- `generate` kept the sources of a refusal followed by labels; it now returns none.
- A non-text id or source document in `questions.jsonl` raised TypeError; it now raises ValueError naming the line.
- `_mean_chunk_tokens` fetched every chunk; it is now one SQL average, `db.mean_chunk_tokens`.
- `_spent_since` could hide the error that stopped a run when the cost log is malformed; it now warns and returns None.

Reported, not fixed:
- A run with `--questions <other file>` overwrites the real results under the same experiment names.
- A question with no words is embedded in eval, where `search.search` would skip it; every frozen question has words.
- `run_question` looks up the question vector cache twice.

Checks pass: 759 tests.

The human merged the work as PR #7 (`c3f8bb7`). The `runs/` folder stays gitignored, so the eval outputs (`results.csv`, `eval_review.md`) are local only.

Still open: Layer 7b (RAGAS with Nova Pro), in plan mode when the human says so.

## 2026-10-14 — Session 7: Layer 6 (generation and citations)

Status at the end: Layer 6 is done. The smoke run passed 6 of 6, the code review is done and recorded, and the full checks pass with 589 tests (39 in `tests/test_generate.py`). Merged by the human as PR #6 (`b57b216`). Next is Layer 7, when the human says so.

Later the same session:

- The human confirmed the Nova Lite price on the pricing page: $0.06 / $0.24 per 1M input / output tokens (Standard tier). It matches `rag/config.py`.
- Smoke run (`python scripts\show_generate.py --yes`), results in `runs/generation_smoke.md`: 6 of 6 passed. STM32F205 per-pin current 25 mA citing p70, LM35 10 mV/°C citing p1 and p13, ESP32 2.3 V to 3.6 V citing p19; the price, Bluetooth and STM32H743 questions got the fixed sentence. Cost $0.0130 (6 rerank queries $0.012, 6 answers $0.00099, embeddings $0.0000016). Nova Lite answered in 0.3 to 0.6 s. The ESP32 answer is right but leaves out the 3.3 V recommended value and the Table 5-2 note.
- The layer's code review (code-review skill, medium): 10 findings. Fixed 8; checks pass after them (589 tests); recorded with `tools/review_gate.py --reviewed` (review 1 of 2). The fixes:
  - A label glued to a word (`25 mA[1]`) was ignored. Now it counts unless that word with the same brackets is in a passage, which means a copied footnote marker (`GPIO16[2]`).
  - Ranges `[1-3]` were dropped without notice. Now they are written out; a range end past the passages is reported invalid.
  - `is_not_contained` was true for a cited answer that also had the sentence, and false when the final full stop was left out. Now it needs no valid source, and the full stop is optional.
  - The system prompt was filled in at import, so a changed `NOT_CONTAINED` would not reach it. Now `system_prompt()` builds it at each call.
  - A cut-off answer (`max_tokens`) was not marked. `generate` now returns `cut_off`, and the smoke check fails it.
  - The smoke check passed a citation of the right datasheet on the wrong page. Now that is a FAIL.
  - A blocked answer crashed the smoke script. Now that question is a FAIL and the run goes on.
  - The price comment in `rag/config.py` now says the human verified it.
- Not changed:
  - A footnote marker standing alone in a passage (`Note: [3]`) looks like a passage label to the model. Different passage labels (such as `[P3]`) would remove this, but CLAUDE.md fixes the `[n]` form and the smoke run showed no confusion. Watch for it in Layer 7.
  - `_bedrock()` is the third copy of the client factory (embed, rerank, generate). Left as it is; worth one shared helper when Cycle 2 changes client settings.
  - The smoke script searches each cached question twice (estimate, then run). Free, so left.
- The smoke run was not repeated after the fixes. The six answers it got resolve the same way under the new rules, and a re-run would be paid.

What we did:

- Readiness check: `main` clean, Layer 5 merged. Docker was not running at first; later in the session `rag-pg` was up.
- Checked against the AWS docs: Converse is on the `bedrock-runtime` client (`modelId`, `system`, `messages`, `inferenceConfig` with `maxTokens` and `temperature`); the answer has `output.message.content[].text`, `stopReason`, `usage.inputTokens` / `outputTokens`, `metrics.latencyMs`. It needs `bedrock:InvokeModel`, which the dev user has on Nova Lite.
- Nova Lite price from an AWS blog: $0.06 per 1M input and $0.24 per 1M output tokens. The live pricing page could not be read (drawn by script); the human is to confirm it.
- Plan approved by the human (single agent). Built:
  - `rag/config.py`: `GENERATE_MODEL_ID`, `GENERATE_MAX_TOKENS = 512`, `GENERATE_TEMPERATURE = 0.0`, the two prices, `NOT_CONTAINED`.
  - `rag/generate.py`: `passage_label`, `build_context`, `SYSTEM_PROMPT`, `build_prompt`, `call_llm`, `resolve_citations`, `is_not_contained`, `generate`.
  - `tests/test_generate.py`: 30 tests with a fake client; nothing paid.
  - `scripts/show_generate.py`: 3 answerable and 3 unanswerable questions through hybrid search, rerank and Nova Lite, with PASS/FAIL. Dry run: 6 embeddings, at most 6 rerank calls, 6 answers, about $0.0135 and 100 seconds of waiting. Stopped at the question; nothing paid yet.

Decisions:

- A passage is labelled `[n] <part> (<document>), page <page>`, plus the printed page when it differs. Chunks keep their full text.
- When the passages do not hold the answer, the model must reply with one fixed sentence (`config.NOT_CONTAINED`), so "not contained" is detected exactly, not guessed.
- Citations accepted: `[n]`, `[n, m]`, `[n][m]`, `[n] [m]`. Ranges like `[1-3]` are not read; the prompt asks for separate labels.
- Each call writes two cost log rows: input tokens and output tokens, each with its own price. A failed log write only warns.
- A stop reason other than `end_turn` or `max_tokens` raises, after the cost is logged.
- No answer cache: the calls cost almost nothing and the quota is 100 a minute.

Problems:

- What: a datasheet footnote marker such as `GPIO16[2]` looks exactly like the citation `[2]`. Why: the parser writes bare footnote markers in brackets (Layer 1), and the model may copy them. Fix: a bracket glued to a letter or digit is not read as a citation, and the prompt asks for a space before each `[n]`. Tested.
- What: the plan's expected ESP32 answer (3.0 V to 3.6 V) was a guess. Why: written before reading the datasheet. Fix: the parsed text says 2.3 V to 3.6 V (p19, Table 5-2 p51); the script expects that.

Still open:

- Layer 7: reworded refusals are not detected as "not contained"; check the unanswerable questions' answers by eye. Watch whether answers leave out conditions (the ESP32 case).
- Cycle 2: `generate` makes no database writes; the client factory is repeated in three modules.

## 2026-10-13 — Session 6: Layer 5 (reranking)

Status at the end: the code and tests are written and the full checks pass with 550 tests (32 new in `tests/test_rerank.py`), and the layer's code review is done. Layer 5 is done: the two-chunk test passed on the real model and the five-question review is in `runs/rerank_review.md`. Cohere access was refused at first and works now (see "Problem"). Merged as PR #5 (`0603520`). Next is Layer 6, when the human says so.

What we did:

- Checked against the AWS docs before planning:
  - Reranking is the `rerank` call on the `bedrock-agent-runtime` client, with one query, a list of inline text sources and the model ARN `arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0`. The answer gives `index` and `relevanceScore` per source.
  - IAM actions: `bedrock:Rerank` and `bedrock:InvokeModel` on the model. A third-party model also needs `aws-marketplace:ViewSubscriptions` and `aws-marketplace:Subscribe`.
  - Price: $2.00 per 1,000 queries; one query covers up to 100 documents, and a document over 500 tokens (question included) counts as several.
- Plan approved by the human (single agent).
- Built:
  - `rag/config.py`: `RERANK_MODEL_ID`, `RERANK_ENABLED`, `RERANK_MAX_DOCS = 100`, `RERANK_MIN_INTERVAL_S = 20`, `RERANK_USD_PER_QUERY = 0.002`.
  - `rag/db.py`: table `rerank_cache` and the functions `cached_rerank_scores`, `store_rerank_scores`.
  - `rag/rerank.py`: `rerank(conn, question, candidates, k)`, `uncached_count`, `calls_needed`, `rerank_text`, `rerank_key`, `model_arn`.
  - `tests/test_rerank.py`: 32 tests after the review with a fake client and a fake clock; nothing paid.
  - `scripts/show_rerank.py`: the two-chunk test, then the five Layer 4 questions before and after reranking. It prints the paid calls and asks first.
- The dry run printed 6 calls, about $0.012; the human said yes.
- Real run: the two-chunk test passed (per-pin row 0.4708, total-current row 0.0761; rows of STM32F205 Table 12, page 70). One `rerank` row is in the cost log ($0.002).
- `CLAUDE.md` updated: the Layer 5 status entry, the Cohere access note, and `rerank_cache` in the Schema section.
- The layer's one code review (code-review skill): 10 findings. Fixed 4, checks pass after them (550 tests), review recorded with `tools/review_gate.py --reviewed`:
  - A call is logged to the cost log as soon as its answer arrives, so an incomplete answer or a failed later page is still logged.
  - The 20 seconds are counted from the end of a request, not its start, so time spent in boto3's retries does not shorten the gap.
  - The cache key is the model id only. The token limit is not in it, because the cut text is what is hashed; a change of the limit re-sends only the texts it cuts. The two scores of the first real call were stored under the old key, so the two-chunk test is paid once more ($0.002).
  - Switched off, `rerank` returns copies with `search_score` too, so both paths give the same hit shape.
- Review findings not changed:
  - The characters / 4 measure runs low (Titan measured 1.29 times more), so a chunk cut to 400 estimated tokens may be over the 500 tokens AWS counts as one document. With 30 candidates this cannot pass 100 documents, so a call is still one query. A call with close to 100 long documents could be billed as 2 while the log says 1.
  - The spacing is per process: a second process started within 20 seconds is not held back and relies on the retries.
  - boto3 may repeat a request after a timeout, and a paged answer is more than one request; both are logged as one query.
  - Only `scripts/show_rerank.py` creates `rerank_cache` (through `db.create_schema`). It exists in `rag-pg` now; a new database gets it from `create_schema`.
  - A dry run followed by the real run reads the cache twice.
- After the access fix: `python scripts\show_rerank.py --yes` made 6 calls ($0.012); a second run made 0. Cost log: 7 `rerank` rows, $0.014 in total.
- Five-question review (`runs/rerank_review.md`): an answer chunk is at rank 1 for four of five questions (three before). The reworded LM35 question gained the most (answer chunks from 4 and 11 to 1 and 2). The STM32 question is mixed: Table 12 moved to rank 1, but the p104 text chunk left the top 5 and contents pages came in. "TCTH022BE supply voltage" stays at rank 4. Scores differ in level between questions (best hit 0.87 against 0.60).

Problem:

- What: the second rerank call was refused with "not authorized to perform the required AWS Marketplace actions (aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe)".
- Why: Cohere Rerank is sold through AWS Marketplace. The first use of such a model makes AWS subscribe the account to it, and the dev user's Bedrock-only policy does not allow that. The first call was answered before AWS finished that check.
- Fix, as it went: the human added the two actions to the dev user as an inline policy. (The managed policy `AmazonBedrockMarketplaceAccess` was the first idea and is the wrong one: it is for SageMaker-hosted Bedrock Marketplace models and has neither action.) The next run was refused once more, but it started the subscription: AWS emailed that the Cohere Rerank v3.5 offer was accepted (agreement start 21:00 UTC). A run a few minutes later went through.

Decisions:

- The Rerank API is used, not InvokeModel with a Cohere body (that body could not be verified).
- A score is cached per (question, sent text, model), because the model scores each pair on its own. So a re-run is free, changing k costs nothing, and only texts not seen before are sent.
- The text sent is cut so that question plus text stay within `RERANK_MAX_TOKENS` (400, characters / 4). The hit keeps its full text. A full `fixed_400` chunk loses its last few dozen characters; `page` and `fixed_1024` chunks are scored on their first part.
- Calls are spaced 20 seconds apart inside one process; boto3 adaptive retries cover throttling beyond that.
- The cost log gets one row of 1 query per call (the API does not report billed units).
- A returned hit is a copy with `score` = relevance score and `search_score` = the score it came in with.

Still open:

- Done later the same day: the human deleted the temporary Marketplace policy and merged the layer. One new rerank call made after the deletion was answered ($0.002, 8 queries and $0.016 in total), so the Bedrock-only policy is enough now that the account is subscribed.
- Layer 7: use `rerank.calls_needed` in the dry run and print the time.
- Cycle 2: `rerank` writes to the database (the cache), like `question_vector`.

## 2026-10-13 — Session 5: Layer 4 (retrieval), parallel build in progress

Status at the end: Layer 4 is built, reviewed and merged (PR #4, `37b6163`). The full checks pass with 518 tests (350 existing plus 168 in `tests/test_search.py`). The by-eye review is in `runs/search_review.md`. Next is Layer 5, when the human says so.

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

- Done later the same day: the human merged Layer 4 as PR #4 (`37b6163`) and deleted the branch locally and on GitHub. Local `main` matches `origin/main`.
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
