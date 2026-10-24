# Cycle 2 step 7: Deployed evaluation

The 49 frozen questions were sent to the live site on 2026-10-18 between 15:46 and 16:52 UTC, the way a visitor's browser sends them: `POST /api/ask` through CloudFront (`SITE_URL`), then API Gateway, Lambda, and Aurora plus Bedrock. Tool: `python -m rag.eval.deployed_eval` (`plan`, `run`, `cold`, `logs`, `summary`). Per-question rows are in `runs/deployed/`, and the summary is in `runs/results_deployed.csv`.

## Runs

| Run | What | Asks | Spend |
|---|---|---|---|
| `first` | All 49 questions. Their vectors were already in Aurora, but most rerank scores were not (`rerank_cache` was never copied), so 47 questions made a real rerank call. | 49, all 200 | $0.1071 (the dry run said $0.1071) |
| `cached` | The same 49 again, so every rerank score came from Aurora's cache. | 49, all 200 | $0.0131 |
| `cold` | 3 trials, each after 10 minutes with no call, asking q01 (cached). | 3, all 200 | $0.0009 |

- Questions went out 21 s apart, because the reranker allows 3 calls a minute and `/api/ask` is throttled to 1 per 20 s. No request was throttled or retried.
- Step 7 spend: **$0.1211**. The answers' `spent_usd` and the `{"cost"}` lines in CloudWatch agree to the cent on every run.
- `logs` matched all 101 asks to their Lambda request, and each match was confirmed by equal `timings_ms`.
- The daily cap counted 101 asks (52 of 300 after the first pass).

## Answers against the local run (`gen_prefix_k10`)

| | Local | Deployed `first` | Deployed `cached` |
|---|---|---|---|
| Citation hit rate (43 answerable) | 0.930 | 0.930 | 0.953 |
| Answerable questions refused | 2 (q24, q33) | 2 (q24, q33) | 2 (q24, q33) |
| Unanswerable questions refused | 5 of 6 | **6 of 6** | **6 of 6** |
| Invalid labels | 0 | 0 | 0 |
| Same answer as local (same refusal, or same cited pages) | – | 46/49 (47/49 after the 2026-10-24 re-score) | 45/49 (46/49 after) |

The questions whose answers differ:

- **q44** (no answer exists). Locally, Nova Lite gave a reworded refusal ("Sorry, I cannot provide ..."), which the check misses. Deployed, it wrote "Sorry, the provided datasheets do not contain this information.", which the check accepts. Same model, different wording.
- **q22.** Deployed cites the right page (ESP32 page 51). Locally it cited page 24.
- **q25** (reset time of CHIP_PU, 50 µs on page 19). Both deployed passes still say the duration is not given. The first pass cites only page 52, which is a miss, while local cited page 19. The cached pass cites page 19, which is a hit, but the answer is still wrong. Layer 7b had already flagged this question as self-contradicting.
- **q30.** The cached pass has one extra citation (page 30). It is harmless, because the right pages are cited too.

The first and cached passes reranked with exactly the same scores (cached from the first pass), yet they differ on q25 and q30. So **Nova Lite is not fully repeatable at temperature 0**, and a difference of one or two questions between runs is noise, not a pipeline difference.

## Latency

Times are in ms. "Client" is measured on this machine (Toronto, CloudFront PoP YTO53). Lambda and connect times come from the CloudWatch REPORT line and our handler line.

| | First (real rerank) | Cached | Cold (after 10 min idle) |
|---|---|---|---|
| Client p50 / p90 / max | 1,088 / 1,331 / 3,818 | 827 / 1,013 / 1,185 | 21,408 / 21,617 / 21,670 |
| Lambda duration p50 | 911 | 645 | 19,425 |
| DB connect p50 (max) | 119 (1,157) | 129 (273) | 17,574 (18,078) |
| Search p50 | 30 | 30 | 950 |
| Rerank p50 | 259 (p90 332) | 3 | 42 |
| Answer (Nova Lite) p50 | 462 | 466 | 525 |
| Lambda cold inits | 3 of 49 | 0 | 3 of 3 (about 1.2 s each) |

- **The time outside Lambda** (CloudFront, API Gateway, network) is about 180 ms per question when warm.
- **Cold start:** all three trials found Aurora paused and Lambda recycled.
  - The resume took 17.3–18.1 s, Lambda init about 1.2 s, and the visitor waited 21.1–21.7 s.
  - That is under API Gateway's 30 s, with about 8 s to spare.
  - The first query on a new connection is also slower (search 950 ms against 30 ms).
  - AWS says a resume after a long pause (a day) can take 30 s or more. That case is not measured yet; see "Open".
- **In the first pass, 3 of 49 requests started a new Lambda container** (q01, q04, q24), each adding about 1.2 s init and about 1 s for the first connect. All three still answered in under 4 s.
- Search and answer times match the local run (local search 63 ms on Docker, answer 513 ms). The reranker adds about 260 ms when it is really called. Locally its scores were all cached, so the local run shows almost no rerank time.
- Memory: at most 122 MB of 512 MB.

## Cost per query

| | New question (`first`) | Repeated question (`cached`) | Cold |
|---|---|---|---|
| Bedrock (Titan, Rerank, Nova Lite) | $0.002185 | $0.000267 | $0.000296 |
| Lambda (512 MB, billed ms, plus the request) | $0.000009 | $0.000006 | $0.000173 |
| API Gateway HTTP API | $0.000001 | $0.000001 | $0.000001 |
| CloudFront | $0 (Free flat-rate plan) | $0 | $0 |
| Aurora (spread over all 101 asks) | $0.001923 | $0.001923 | $0.001923 |
| **Total** | **$0.0041** | **$0.0022** | **$0.0024** |

- Bedrock is almost all of the per-question cost. A new question costs about $0.0022, almost all of it the rerank query ($0.002). A repeated one costs $0.0003, only the answer.
- **A cold Lambda costs 20 times a warm one**, because it is billed while it waits for Aurora to resume. It is still only $0.00017.
- **Aurora:** the human read `ServerlessDatabaseCapacity` in CloudWatch for 15:00–17:00 UTC (Average, period 1 hour).
  - The hourly averages were 0.463 ACU (15:00) and 1.155 ACU (16:00), so **1.619 ACU-hours**.
  - At $0.12 per ACU-hour (Standard) that is **$0.194**, or $0.0019 per ask over the 101 asks.
  - A paused cluster is 0 ACU, so the hourly averages count only the time it was awake: the dry run at about 15:40, the two passes, and four wake-ups for the cold trials (each followed by 5 minutes awake before it paused again).
  - The 15:00 hour may also hold a few minutes from step 6's last check at about 14:55.
  - At this low traffic, **Aurora costs about as much as all the Bedrock calls together** ($0.19 against $0.12). That is because every wake-up keeps it at about 0.5–2 ACU for at least 5 minutes, however few questions come in.
- Prices were checked on the AWS pricing pages on 2026-10-18 (us-east-1). Lambda: $0.0000166667 per GB-s and $0.20 per 1M requests. HTTP API: $1.00 per 1M requests. All of it is inside the Free plan credits; the table shows what it would cost.

## Findings

1. **The deployed pipeline answers like the local one.** It has the same citation hit rate, the same two false refusals and no invalid labels. The questions that differ come from Nova Lite's run-to-run variation, not from the deployment.
2. **The reranker's real latency is about 260 ms.** Cycle 1 never measured it, because its scores were cached.
3. **The cold path is about 21 s, inside API Gateway's 30 s limit.** Aurora's resume is 85 % of it. If a resume ever passes the limit, the page shows its "busy or waking up" message, so the visitor knows to ask again.
4. **CloudWatch needs a moment.** A log read about a minute after the last call missed that call's lines; a second read a few minutes later found them.
5. **A reworded refusal can go either way between runs** (q44). The known limit from Layer 6 stays. **Update 2026-10-24:** `is_not_contained` now also detects a refusal in other words when the answer cites nothing (a missing-information phrase plus a reference to the datasheets or passages). `scripts/rescore_refusals.py` re-checked all saved answers: only `gen_prefix_k10` q44 changed, so that run is now 6/6 unanswerable refused and its citation hit is unchanged.
6. **Aurora is half of the cost per question at demo traffic:** $0.0019 a question, against $0.0022 for Bedrock on a new one. Its cost depends on how often it wakes, not on how many questions it answers. A visitor who asks one question pays for 5 minutes of Aurora. Pausing at 0 ACU still saves the most: an always-on cluster at 0.5 ACU would cost about $1.44 a day.

7. **Anyone can keep Aurora awake, and the daily cap does not stop it.**
   - Every `/api/ask` and `/api/documents` request opens a database connection. `handler.py` connects before the question is checked, so an empty question also wakes Aurora.
   - The daily cap is counted in Aurora, so even a request over the cap wakes it to check.
   - A caller who sends a request every few minutes keeps the cluster from pausing. That costs about $1.50–3 a day at 0.5–1 ACU, and at most $11.52 a day at the express cluster's 4 ACU limit, plus about $0.70 a day of capped questions.
   - Budget alerts only warn. The API can be stopped in the console at any time.
   - To be addressed next session.

## Open

- **Cost risk (finding 7).** Options: check the question before connecting; remove `GET /api/documents` (the page no longer calls it); keep the daily counter outside Aurora; pause the demo when it is not being shown; tear down after the report.
- **Optional long-idle trial.** After a night with no calls, run `python -m rag.eval.deployed_eval cold --trials 1 --idle-minutes 0 --yes` (about $0.0003), then `logs` and `summary`. This shows whether a long pause passes the 30 s limit.

## Code review (1 of 2)

8 of 10 findings fixed. None of them changed this run's numbers, because no request was retried.

1. The pace is now counted from the answer of the last send, so a retried question cannot shorten the gap before the next one.
2. A response cut off mid-body (`http.client.IncompleteRead`) is now recorded as no answer and retried, instead of stopping the run.
3. The daily cap is `config.DEPLOYED_DAILY_LIMIT`, and `run` refuses to start when fewer asks are left today than it would make.
4. `invalid_labels` now reads both forms: the local CSV stores the labels themselves, the deployed rows store True or False.
5. The pass file is read once, not once per question.
6. The Lambda requests are sorted once, not once per row.
7. `DEPLOYED_MAX_ATTEMPTS` of 0 now sends once.
8. `summary` prints a warning when a question was retried. Only the answered send is costed and timed, but an earlier 504 may still have run and paid in Lambda.

Not changed:

- **Costing and timing every attempt of a retried question.** No retries happened; the warning in point 8 covers it.
- **The imported private helpers from `run_eval`.** Style only.
