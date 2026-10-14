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
- Titan V2 price verified on 2026-10-13: $0.02 per 1M input tokens on-demand ($0.01 batch), which matches `EMBED_USD_PER_1K_TOKENS = 0.00002` in `rag/config.py`. Nothing is left open from Layer 0.
- Cohere Rerank 3.5 access: WORKS since 2026-10-13. The first use needed `aws-marketplace:Subscribe` and `aws-marketplace:ViewSubscriptions` on the dev user (a temporary inline policy; the managed policy `AmazonBedrockMarketplaceAccess` does not contain them). The call that starts the subscription is itself refused; a call a few minutes later goes through. The account is now subscribed and the human deleted the temporary policy; a new rerank call made after that was answered, so an identity with only `bedrock:Rerank` and `bedrock:InvokeModel` is enough from here on.
- Layer 1: DONE on 2026-10-12. `rag/parse.py`, `tests/test_parse.py` (46 pass), `conftest.py` and `scripts/show_parsed.py` exist. `python -m rag.parse` writes `runs/parsed/<doc>.json` for the four datasheets (311 pages, 259 tables, none scanned). Each page has `page`, `page_label`, `text` (whole page), `text_outside_tables` and `tables` (`caption`, `rows`; a `null` cell is a merged cell, `""` is an empty one). A bare footnote marker is written in brackets (`GPIO16[2]`); a marker printed as `(1)` stays inline. The manual review covered 12 pages against rendered images; the two defects it found (values with no column lines, glued footnote markers) are fixed and re-checked. Details in `runs/parse_review.md`.
- PR #1 (Layer 1, hooks, CI workflow) was merged on 2026-10-12; later fixes were pushed straight to `main`.
- Parallel layer builds were set up on 2026-10-13 (see "Parallel layer builds"): `.claude/agents/layer-builder.md` and `.claude/agents/test-writer.md`. Layer 2 was the first trial, and on 2026-10-13 the human kept them for Layers 4 and 7. Lesson from it: try the contract's rules on real data while planning, because the Layer 2 table rules had to be rewritten at integration.
- Layer 2: DONE on 2026-10-13, merged as PR #2. 329 tests pass (283 in `tests/test_chunk.py`). Files: `rag/chunk_base.py` (`approx_tokens` = characters / 4 rounded up, `make_chunk`), `rag/chunk_text.py` (headings, `page_chunks`, `fixed_chunks`, `structure_chunks`), `rag/chunk_tables.py` (`normalise_table`, `table_chunks`), `rag/chunk.py` (`chunk_document(parsed, strategy)`; `python -m rag.chunk` writes `runs/chunk_counts.csv`), `scripts/show_chunks.py`. The seven strategies and their sizes are `CHUNK_STRATEGIES` in `rag/config.py`. A chunk has `document`, `page`, `page_label`, `section`, `kind`, `text`, `strategy`.
  - Chunks never cross a page. `page` is the whole page with tables as raw text; every other strategy takes text outside tables plus table chunks.
  - Table text: caption line, then one line per row, `Label: value, Label: value`. A merged cell is filled from above only before the first filled cell of its row or in a unit column, and a number is never copied; any other merged cell is read as part of the cell on its left, which is labelled with every column it covers (`MIN / TYP / MAX: ±0.4`). Tables captioned "Figure" are skipped (10).
  - Totals for the four datasheets: `page` 311, `fixed_256` 1,038, `fixed_256_overlap` 1,062, `fixed_400` 797, `fixed_400_overlap` 805, `structure_aware` 911, `fixed_1024` 577.
  - Known limits are listed in `progress.md` (2026-10-13, "Still open"): missed section headings, three-row headers, value cells left empty, three figure fragments. `page` and `fixed_1024` are `rerank_limited`; a 400-token chunk can also pass the reranker's limit once the question is added, so Layer 5 must truncate.
- Layer 3: DONE on 2026-10-13, merged as PR #3. 350 tests pass (`tests/test_db.py` 15, `tests/test_embed.py` 6). All 5,501 chunks of the seven strategies are in `rag-pg` with 1024-number vectors; `python scripts\show_db.py` compares the stored counts with Layer 2 and must end with "OK".
  - Files: `rag/db.py` (all SQL; functions take a connection from `db.connect()`, which is autocommit, so use `conn.transaction()` for a multi-statement change), `rag/load.py` (`plan_load`, `load_document`; `python -m rag.load` prints calls and cost and stops, `--run` asks then loads, `--run --yes` does not ask, `--doc` and `--strategy` narrow it), `rag/embed.py` (`embed_text` for one string, `embed_texts` for many), `rag/costlog.py` (`read_rows`), `scripts/show_db.py`. Document part and revision are `DOCUMENTS` in `rag/config.py`.
  - Schema as built is in the "Schema" section: `chunk_index`, two keyword columns `tsv_english` and `tsv_simple` (each with a GIN index), and `embedding_cache`. A vector goes in and comes out as the text `[0.1,0.2,...]` cast to `vector`; no `pgvector` Python package.
  - `embedding_cache` holds every vector paid for, keyed by the SHA-256 of the text plus the model setup. Only 2,604 of the 5,501 chunk texts are different, a re-run makes no call, and a stopped load carries on. The header-prefix experiment changes only `load.embed_input` and is stored under its own strategy label.
  - Cost: 863,130 tokens, $0.0173 for the whole load. Real Titan tokens were 1.29 times the characters / 4 estimate. The cost log has one row per batch of up to 100 texts.
  - Speed: the account's Titan V2 on-demand quota is 60 requests a minute (the AWS default is 6,000) and 300,000 tokens a minute, both marked "Not adjustable"; verified in Service Quotas on 2026-10-13. So the load took about 45 minutes, and an increase cannot be requested there. Layer 7 embeds one question per call: plan for that rate, or cache question vectors.
  - Tests that need Postgres run in a throwaway schema (`test_...`) with a fake embedder and skip when the database is not reachable.
- Bedrock on-demand request quotas on this account, read by the human in Service Quotas (us-east-1) on 2026-10-13, all "Not adjustable" (AWS default in brackets): Titan Embeddings V2 60 a minute (6,000); Cohere Rerank 3.5 **3 a minute** (250); Nova Lite 100 (2,000); Nova Pro 13 (250). Token quotas were read only for Titan (300,000 a minute). What follows from them:
  - Layer 5: the rerank client must wait between calls (3 a minute is one call every 20 seconds) and retry on throttling. Keep every rerank result in a cache keyed by question, chunk text and model, as `embedding_cache` does for vectors.
  - Layer 7: one reranked run of 40 questions takes at least 14 minutes, so plan the number of reranker-on configurations around that and reuse cached results. Cache question vectors too. RAGAS with Nova Pro at 13 calls a minute is slow as well: count the judge calls in the dry run and print the time, not only the cost.
  - Cycle 2: the public demo can rerank only 3 questions a minute for everyone together. Decide in the Cycle 2 plan (throttle the API to match, or serve without the reranker).
- Layer 4: DONE on 2026-10-13 (parallel build), merged as PR #4. 518 tests pass (`tests/test_search.py` 168). The by-eye review of five questions is in `runs/search_review.md`.
  - Files: `rag/search.py` (`query_terms`, `rrf`, `detect_part`, `uncached_questions`, `question_vector`, `vector_search`, `keyword_search`, `hybrid_search`, `search(conn, question, mode)` with mode `vector`, `keyword` or `hybrid`), three functions in `rag/db.py` (`nearest_chunks`, `keyword_chunks`, `explain_nearest`), `scripts/show_search.py`, `tests/conftest.py` (the shared `database` and `conn` fixtures). Settings in `rag/config.py`: `DEFAULT_STRATEGY` (`fixed_400`, a placeholder until Layer 8), `KEYWORD_CONFIGS`, `KEYWORD_CONFIG`, `RRF_C`, `DETECT_PART`, `PART_ALIASES`. `text_hash` and `embed_key` now live in `rag/embed.py`.
  - A hit is a dict: `id`, `document`, `page`, `page_label`, `section`, `kind`, `text`, `score`; a hybrid hit adds `vector_rank` and `keyword_rank`. An argument of a search function left as `None` takes its value from `rag/config.py` when the function is called.
  - Keyword ranking is not plain `ts_rank`. Each question word goes through `plainto_tsquery` on its own, and a chunk's score is the sum over the words it matches of a rarity weight, `ln(1 + (N - df + 0.5) / (df + 0.5))`, times `ts_rank(..., 1)`. Reason, found on the real chunks: plain `ts_rank` put an STM32 contents page first for "TCTH022BE supply voltage", because it does not know a part number is rare. No stop-word list of our own is used.
  - N and df are counted inside the searched set, so a datasheet filter changes the keyword ranking (inside one datasheet its own part number is common). Keep this in mind when the `detect_part` experiment is judged at Layer 7.
  - Question vectors are kept in `embedding_cache` by `search.question_vector`, keyed on the exact question text. `search.uncached_questions` gives the questions a run would pay for; use it for the Layer 7 dry run.
  - Known keyword limits, not fixed: Postgres keeps `VDD/VBAT` as one token (322 such tokens), so `VDD` does not match it; `STM32F205` as an exact word is in 1 chunk (the datasheet writes `STM32F205xx`, `STM32F20x`), and prefix matching is not used because `GPIO1` would match `GPIO16`; `-40` is a signed token, so `40` does not match it. `detect_part` matches by prefix, so `LM358` would select the LM35 datasheet. For Cycle 2: every new question adds a row to `embedding_cache`, so a search needs write access; decide there whether the public demo caches.
- Layer 5: DONE on 2026-10-13, merged as PR #5. 550 tests pass (`tests/test_rerank.py` 32); the code review is done and recorded. On the real model the two-chunk test passed (per-pin row 0.47, total-current row 0.08) and the five-question review is in `runs/rerank_review.md`: an answer chunk at rank 1 for four of five questions (three before reranking); "TCTH022BE supply voltage" stays at rank 4. Rerank scores are not comparable across questions, so no fixed score threshold. Cost so far: 8 queries, $0.016.
  - Verified on 2026-10-13: reranking is `rerank` on the `bedrock-agent-runtime` client (one query, inline text sources, model ARN `arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0`; the answer has `index` and `relevanceScore`). IAM actions `bedrock:Rerank` and `bedrock:InvokeModel`. Price $2.00 per 1,000 queries, up to 100 documents a query, a document over 500 tokens counts as several.
  - Files: `rag/rerank.py` (`rerank(conn, question, candidates, k=None, enabled=None)`, `uncached_count`, `calls_needed`, `rerank_text`, `rerank_key`), `rag/db.py` (table `rerank_cache`: `question_sha256`, `text_sha256`, `model`, `score`; `cached_rerank_scores`, `store_rerank_scores`), `scripts/show_rerank.py`. Settings: `RERANK_MODEL_ID`, `RERANK_ENABLED`, `RERANK_MAX_DOCS`, `RERANK_MIN_INTERVAL_S`, `RERANK_USD_PER_QUERY`.
  - `rerank` returns copies of the best k hits: `score` is the relevance score, `search_score` the score the hit came in with. Switched off, it returns the first k candidates in the same shape and calls nothing.
  - A score is cached per question, sent text and model id, so only texts not seen before are sent, and a change of k is free. The sent text is cut so that question plus text stay within `RERANK_MAX_TOKENS`; the hit keeps its full text. Calls are 20 seconds apart inside one process, and the estimate of one billed query per call holds for 30 candidates but may run low near 100 long documents. `calls_needed` is for the Layer 7 dry run.
- Layer 6: DONE on 2026-10-14, merged as PR #6 (`b57b216`). 589 tests pass (`tests/test_generate.py` 39). The smoke run passed 6 of 6 (`runs/generation_smoke.md`): three answers cite the right page, three unanswerable questions get the fixed sentence. Cost $0.013, almost all reranking.
  - Verified on 2026-10-14: Converse on the `bedrock-runtime` client (`modelId`, `system=[{"text"}]`, `messages`, `inferenceConfig` `maxTokens` / `temperature`; answer `output.message.content[].text`, `stopReason`, `usage.inputTokens` / `outputTokens`, `metrics.latencyMs`), IAM `bedrock:InvokeModel`. Nova Lite price $0.06 / $0.24 per 1M input / output tokens (Standard tier), read by the human on the pricing page.
  - Files: `rag/generate.py` (`passage_label`, `build_context`, `system_prompt`, `build_prompt`, `call_llm`, `resolve_citations`, `is_not_contained`, `generate`), `scripts/show_generate.py`. Settings: `GENERATE_MODEL_ID`, `GENERATE_MAX_TOKENS` (512), `GENERATE_TEMPERATURE` (0), `GENERATE_USD_PER_1K_INPUT` / `_OUTPUT`, `NOT_CONTAINED`.
  - A passage is `[n] <part> (<document>), page <page>` (plus the printed page when it differs) and the chunk's full text. `generate` returns `answer`, `sources` (`label`, `id`, `document`, `page`, `page_label`; each label once, in order of use), `invalid`, `not_contained`, `cut_off`, token counts, `stop_reason`, `latency_ms`. With no chunks it returns the sentence and calls nothing.
  - "Not contained" is one fixed sentence the model must give; `is_not_contained` is true when it is there (final full stop optional) and nothing valid is cited. A reworded refusal is missed: watch for it in Layer 7.
  - Citations read: `[n]`, `[n, m]`, `[n][m]`, `[n-m]`. A label glued to a word counts unless that word with the same brackets is in a passage (a copied footnote marker such as `GPIO16[2]`). A footnote marker standing alone in a passage (`Note: [3]`) can still be confused with a passage label; not seen yet.
  - Each call writes two cost log rows (input and output tokens). A stop reason other than `end_turn` or `max_tokens` raises after logging. No answer cache.
- Layer 7 split, as decided with the human on 2026-10-14: 7a is questions, metrics, `run_eval` and every retrieval comparison, plus answer checks for a shortlist; 7b is RAGAS with Nova Pro and gets its own plan.
- Layer 7a: DONE on 2026-10-14 (parallel build), merged as PR #7 (`c3f8bb7`). The code review is done and recorded (1 of 2). Results are in `runs/eval_review.md`.
  - Questions: `data/questions.jsonl` has 49, checked by Claude against the rendered PDF pages, at the human's request: 22 table lookups, 10 single values, 6 rephrased, 5 comparisons, 6 with no answer. It is frozen; the draft and its checks are in `runs/questions_draft.md`.
  - Files:
    - `rag/eval/questions.py`: `load_questions`, which validates every line.
    - `rag/eval/metrics.py`: `recall_at_k`, `mrr`, `index_recall`, `refusal_rates`, `citation_hit_rate`, `by_type`.
    - `rag/eval/experiments.py`: `BASELINE` and `EXPERIMENTS`, a Python dict, not YAML.
    - `rag/eval/run_eval.py`.
    - `rag/db.py`: the HNSW functions. The index is built inside a rolled-back transaction, so the database is never changed.
    - `rag/load.py`: the header prefix. The strategy `<name>_prefix` embeds "part (document). section" on a line before the text.
    - Settings in `rag/config.py`: `QUESTIONS`, `EVAL_DIR`, `RESULTS`, `RESULTS_BY_TYPE`, `EVAL_KS`, `EVAL_RERANK_K`, `EMBED_PER_MIN`, `GENERATE_PER_MIN`, `HEADER_PREFIX`.
  - One run gives two summary rows, the search order and the reranked order. The reranker keeps 10 in eval, so R@1/3/5/10 and MRR@10 come from one list.
  - Outputs:
    - `runs/results.csv`: one row per experiment and reranker state;
    - `runs/results_by_type.csv`;
    - `runs/eval/<experiment>.csv`, one row per question.
  - Results:
    - Hybrid is the best search order.
    - The reranker lifts every setup to about R@5 0.977.
    - `page` chunking is clearly worst.
    - `english` keywords beat `simple`.
    - HNSW index recall is 0.995, so exact search stays.
    - The header prefix (`fixed_400_prefix`, loaded) gives the best search order: MRR 0.834 against 0.766.
    - Answers: prefix with k 10 cites the right page for 40 of 43 questions. False refusals by Nova Lite are the main failure.
  - Shortlist for 7b: prefix k 10, prefix k 5, `detect_part` k 5, and the baseline.
  - Layer 6 changed in 7a:
    - `is_not_contained` also accepts the refusal sentence followed only by labels.
    - The prompt has a rule to read every passage and to refuse without citations. It made no measurable difference.
    - A reworded refusal ("Sorry, I cannot provide ...") is still missed.
  - Spend: $1.31 in total, $1.20 of it on reranking.
- Layer 7b: built on 2026-10-14 (single agent), not yet merged. Results and the review by hand are in `runs/ragas_review.md`.
  - RAGAS 0.4.3 runs in a second venv, `datasheet-rag/.venv-ragas` on Python 3.13, because its dependency `scikit-network` has no 3.14 build. Pins are in `datasheet-rag/requirements-ragas.txt`. LangChain must stay on 0.3: RAGAS 0.4.3 imports `langchain_community.chat_models.vertexai`, which 0.4 removed. Only `ragas_run` needs that venv; the project, its tests and CI stay on 3.14 (all tests also pass on 3.13).
  - Nova Pro price verified by the human on 2026-10-14: $0.80 / $3.20 per 1M input / output tokens. The `JUDGE_*` settings are in `rag/config.py`.
  - Files:
    - `rag/eval/judge_inputs.py`: the 7a answers plus passages rebuilt from the caches with no paid call; the citations must match the 7a CSV. Also `plan_judge`. No RAGAS import.
    - `rag/eval/ragas_run.py`: `logged_completion` (judge calls 5 s apart, throttling retried, cost logged), `make_scorers` (`Faithfulness` and `AnswerRelevancy` from `ragas.metrics.collections`, LiteLLM `bedrock/amazon.nova-pro-v1:0`, Titan through `search.question_vector`), resumable `runs/ragas/<experiment>.csv`, summary in `runs/results_ragas.csv`.
    - `tests/test_judge.py`: 28 tests, no RAGAS import. 787 tests pass in all.
  - The rebuild is checked three ways: the top 10 ranking must equal the 7a CSV's `rerank_hits`, the citations must match, and an answer that is only labels is refused before any paid call.
  - Known limit: a re-run of a 7a generation experiment does not clear its judged rows. Delete `runs/ragas/<experiment>.csv` before judging new answers.
  - The code review is done and recorded (1 of 2).
  - Faithfulness makes 2 judge calls per answer. Answer relevancy is run with strictness 1 (RAGAS's default is 3), because at temperature 0 the three calls would write the same question.
  - Results for the four shortlisted setups (43 answerable questions each):
    - `gen_prefix_k10` is best: faithfulness 0.972, relevancy 0.859. The other three are 0.83–0.86 and 0.78–0.79.
    - False refusals score 0 on both metrics, so the means follow the 7a refusal counts.
    - Over answered questions only, faithfulness is 0.97–0.995 in every setup, and relevancy does not separate the setups.
    - The judge caught a wrong answer that 7a counted as correct (q08: the LM35 given the LM35A's accuracy) and a self-contradicting one (q25 at k 10).
    - It was wrong on 2 of 21 refusals (q33: the verdict disagrees with its own reason).
    - It cannot see mis-citations, so the 7a citation check stays.
  - Spend: $0.87 for the whole of 7b.
- Next: Layer 8 (wrap-up), in plan mode, when the human says so.
- The four datasheets are the final set. The human is on the bypass list of the `main` ruleset, so direct pushes of small changes work.
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
- Git, as set by the human on 2026-10-12. Layer builds: the human does the branch, commits, PR and merge; Claude says when the work is ready and lists the changed files. Small changes (edits to `.md` files, small fixes, tooling tweaks): Claude commits and pushes them straight to `main` with no PR, but only after telling the human what it wants to commit and getting a yes (added by the human on 2026-10-13; every commit needs its own yes). Before the commit, check `git status` for anything that must not be committed. If it is unclear whether a change is small, ask. The GitHub workflow also runs on a push to `main` unless the push only touches Markdown files.
- Hooks (added 2026-10-12):
  - `.githooks/pre-commit` runs `tools/checks.py --staged` before every commit: secret scan, `ruff`, `pytest`. A failure stops the commit. Enabled per clone with `git config core.hooksPath .githooks`. Needs `ruff` in the venv (`pip install ruff`).
  - `.claude/settings.json` has a Stop hook (`tools/review_gate.py`). When code has changed it runs the same checks and sends Claude back to fix failures. When they pass and the layer has not been reviewed, it asks for the code review.
  - Code review rule (set by the human on 2026-10-12): one review per layer, with the built-in code-review skill, when the layer is finished and before the PR. Claude runs it without being asked; the human never has to call it. Small changes outside a layer build are not reviewed: when the hook asks about one, say it is not a layer and carry on. Record it with `python tools/review_gate.py --reviewed`. After fixing review findings, re-run the checks only. Run a second review only if a fix rewrote logic substantially, and never more than two per layer. The hook counts a layer as everything since the branch left `main`; `python tools/review_gate.py --status` shows the count.
  - `.github/workflows/tests.yml` runs ruff and pytest on every pull request into `main` and on pushes to `main` that touch more than Markdown. It is pinned to `ubuntu-24.04`, `actions/checkout@v6` and `actions/setup-python@v6` (v7 of both exists; not adopted because its changes were not checked). GitHub has no AWS credentials, database or datasheet PDFs, so a test that needs one must skip itself there. `pytest.ini` limits collection to `tests/`; never put a paid call in a file pytest can collect.
  - A second Stop hook (`tools/progress_gate.py`, added 2026-10-12) keeps `progress.md` current. When files have changed and the log has no entry dated today, or a file changed after the log was last saved, it sends Claude back to write or update today's entry. It cannot see a session where no file changed, so log decisions made in discussion by hand.
- Build one layer at a time, in order. Do not start a layer until the previous layer's test passes.
- Keep the pipeline steps as separate small functions in separate modules. The entry point (local server in Cycle 1, Lambda handler in Cycle 2) only calls them in order.
- Keep all SQL in `rag/db.py`. Nothing else writes SQL strings.
- Never put secrets in code, in the repo, or in the frontend. No AWS keys in Git. Add `.env` and `.aws/` to `.gitignore` first.
- Every claim about an AWS price, limit, API field, or model ID that is marked VERIFY must be checked against the current AWS documentation or console before it is relied on.
- Keep `progress.md` (repo root) up to date. At the end of every session, add a dated entry at the top of the log: what was done, decisions, problems, what is still open. Update the human's checklist in the same file.
- Keep a cost log. Every Bedrock call records its token or query count to `runs/cost_log.csv`.
- Do not run any evaluation loop that calls a paid model until the number of calls and the estimated cost are printed and confirmed.

## Parallel layer builds (set by the human on 2026-10-13)

A layer that splits well is built by several agents at the same time, with Claude as the lead. This is a standing request from the human to use subagents for those layers. It was tried on Layer 2 and kept for Layers 4 and 7. When planning a parallel layer, try the contract's rules on real data before the agents start.

Which layers split:

| Layer | Build | Split |
|---|---|---|
| 2 chunking | Parallel | text strategies; table chunking; test writer |
| 3 embeddings and storage | Single | Small, tightly linked, and it makes paid calls |
| 4 retrieval | Parallel | database search functions (SQL in `rag/db.py`); pure functions (`rrf`, tsquery builder, `detect_part`); test writer |
| 5 reranking | Single | One small module around one paid call |
| 6 generation and citations | Single | One small module; the functions depend on each other |
| 7 evaluation | Parallel | `metrics.py`; `run_eval.py`; test writer. The questions are written by the human and Claude together |
| 8 wrap-up | Single | Decisions and a summary, no parallel work |
| Cycle 2 steps | Single by default | Mostly console and deployment work in a fixed order. Decide in each step's plan |

The table is the default. The layer's plan may change it with a reason (for example, a layer turns out smaller than expected).

How a parallel build runs:

1. Plan, in plan mode. The plan has a "Work split" section with: the contract (shared data shapes, function names and signatures); each agent's job and done test; file ownership, with no file given to two agents. A single-agent layer says so in one line instead.
2. Build. After the human approves the plan, the lead starts the agents together: `layer-builder` for each code piece and one `test-writer` (definitions in `.claude/agents/`). Each prompt carries the contract, the job, the owned files and the done test in full, because an agent starts with no memory of the conversation.
3. Integrate. The lead reads every agent's report and the code, joins the pieces, makes the changes agents may not make (`rag/config.py`, shared files), runs the full checks (`tools/checks.py`) and fixes what does not fit. A test that fails is fixed in the code unless the test contradicts the contract.
4. Review and hand over. One code review of the whole layer with the built-in code-review skill, as in the code review rule above. Then the lead updates `CLAUDE.md` and `progress.md` and tells the human the layer is ready, with the list of changed files.

Rules for the agents (also written into each definition):

- An agent changes only the files it owns. It reports problems in other files and does not fix them.
- Agents work in the normal project folder on separate files, not in git worktrees: the parsed datasheets, the PDFs and the venv are not in git, so a worktree would not have them.
- Agents never run a git command that changes anything. Commits, branches and pushes stay as in the Git rule.
- Agents never make a paid or cloud call. Only the lead does, after the cost is printed and confirmed.
- Agents never edit `CLAUDE.md`, `progress.md`, `rag/config.py`, or anything under `.claude/`, `.githooks/`, `.github/` or `tools/`.
- The test writer works from the contract and does not read the new code. Builders do not edit tests.
- An agent that finds the contract wrong stops and reports it. Only the lead changes the contract, and tells the human when it does.

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

## Schema (as built in Layer 3; the source is `_TABLES` in `rag/db.py`)

```sql
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE documents (
    id       SERIAL PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,       -- parsed name, e.g. lm35
    file     TEXT,                       -- PDF file name
    part     TEXT,
    revision TEXT
);

CREATE TABLE chunks (
    id          SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    strategy    TEXT NOT NULL,           -- which chunking strategy produced it
    chunk_index INTEGER NOT NULL,        -- order within the document and strategy
    page        INTEGER NOT NULL,        -- PDF page position, used for citations
    page_label  TEXT,                    -- printed page label, if known
    section     TEXT,
    kind        TEXT NOT NULL,           -- 'text' or 'table'
    text        TEXT NOT NULL,
    embedding   vector(1024) NOT NULL,
    tsv_english tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    tsv_simple  tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    UNIQUE (document_id, strategy, chunk_index)
);

CREATE INDEX chunks_strategy_idx ON chunks (strategy);
CREATE INDEX chunks_tsv_english_idx ON chunks USING GIN (tsv_english);
CREATE INDEX chunks_tsv_simple_idx ON chunks USING GIN (tsv_simple);

-- Every vector that was paid for, keyed by the SHA-256 of the embedded text and the model setup.
CREATE TABLE embedding_cache (
    text_sha256 TEXT NOT NULL,
    model       TEXT NOT NULL,
    embedding   vector(1024) NOT NULL,
    PRIMARY KEY (text_sha256, model)
);

-- Every rerank score that was paid for (Layer 5), keyed by the hashes of the question and of the text sent.
CREATE TABLE rerank_cache (
    question_sha256 TEXT NOT NULL,
    text_sha256     TEXT NOT NULL,
    model           TEXT NOT NULL,
    score           DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (question_sha256, text_sha256, model)
);
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
python -m rag.eval.run_eval baseline --dry-run      # names, or --group <g>, or --all; prints calls, cost, minutes
python -m rag.eval.run_eval baseline               # real run, asks first; --yes does not ask
.venv-ragas\Scripts\python -m rag.eval.ragas_run --shortlist --dry-run   # RAGAS judge (Python 3.13 venv); --limit N, --yes
pytest -q tests/test_judge.py
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
