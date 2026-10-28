# Step 9c: both pipelines tested again, with uploads and notes live (2026-10-28)

Steps 9a (upload) and 9b (reviewers' notes) changed both pipelines after the step 7 and step 8 measurements. 9c
checks on the deployed site that the 49 frozen questions did not regress now that three notes are published, that
only the questions the notes were written for get one, and that uploads still work and stay private. Spend about
$0.08. Plan: `C:\Users\muham\.claude\plans\temporal-tinkering-rabbit.md`.

The 12 correction cases were run on the same day after the matcher change (24/24, `feedback_eval.md`); their three
originals are q08, q24 and q33 of the 49, so part B re-checks those on the site.

## A. Who is close to a note (free)

`python -m rag.eval.correction_eval similarities` on Aurora (every vector cached; `runs/feedback/similarities_shared.csv`).

- At or above 0.70: q08, q24, q33 and their own paraphrases only.
- Closest other frozen question: q29 at 0.605 (STM32 note). Highest negative: c33n at 0.680.

Both pipelines decide with this same number, so it predicts part B's note decisions.

## B. The 49 on the site with notes on ($0.072)

`python -m rag.eval.deployed_eval run --pass notes` and `--pass kb_notes`, 21 s apart, through `SITE_URL`; then
`logs`, `summary --aurora-acu-hours 1.6186` and `compare`. Rows: `runs/deployed/notes.csv`, `kb_notes.csv`.

| | Custom: cached (step 7) | **Custom: notes** | AWS managed: kb (step 8) | **AWS managed: kb_notes** |
|---|---|---|---|---|
| Status 200 | 49/49 | **49/49** | 49/49 | **49/49** |
| Citation hit | 0.953 | **0.977** | 0.837 | **0.884** |
| Answerable refused | 2/43 | **0/43** | 4/43 | **1/43** |
| Unanswerable refused | 6/6 | **6/6** | 6/6 | **6/6** |
| Notes given | – | **q08, q24, q33 only** | – | **q08, q24, q33 only** |
| Client p50 | 827 ms | 1,122 ms | 1,662 ms | 1,941 ms |
| Model spend per question | $0.00027 | $0.00027 | $0.00120 | $0.00120 |
| Aurora connects | yes | yes | none | **none** |

What changed against the pass before the notes (`compare`):
- **Fixed by a note, both pipelines:** q24 and q33, refused before, now cite the right page (ESP32 p.46, STM32
  p.71). q08 already cited a right page; its note corrects the values, which the page measure cannot see (the 12
  correction cases check the values).
- **Changed without a note:** ours q22 (cited p.24 instead of p.51, no longer right); KB q12 lost and q13 gained.
  A question with no note gets exactly the same prompt as before, so these are Nova Lite's run-to-run variation
  (step 7 already saw two identical passes differ on q25 and q30), and on the KB side also Retrieve's.
- The rest are differences in extra citations only.

Reading the summary for these two passes:
- `same_as_local` compares with the local runs, which had no notes, so it counts the intended fixes as differences.
- `aurora_usd_per_query` is empty: the ACU-hours (1.6186) were read for the step 7 runs only, so only those passes
  share them. The `notes` pass did connect to Aurora.
- The higher client times are most likely the day's conditions; the notes step itself takes a few milliseconds.

**Problem, fixed:** the first summary showed one Aurora connect (115 ms) on `kb_notes`. Its CloudWatch line had been
paired with the previous request (the last `notes` ask, seconds earlier), which `logs` flags as "timings differ".
`summary_row` now takes CloudWatch numbers only from rows matched "ok". No AWS managed request connected.

## C. Uploads ($0.008)

`python -m rag.eval.upload_eval run ..\agentic-ai-foundations.pdf`, with the cases in `data/upload_cases.jsonl`.
Rows: `runs/deployed/uploads.csv`. The PDF is the 9a one (37 pages, 59 passages).

| Case | Question | With the upload? | Must | Custom | AWS managed |
|---|---|---|---|---|---|
| u1 | Who introduced the actor model, and when? | yes | cite p.8, "Hewitt", "1973" | pass (p.8, 13) | pass (p.8) |
| u2 | What did Oliver Selfridge introduce in 1959? | yes | cite p.18 | pass | pass |
| u3 | LM35 supply voltage | yes | refuse, no note | pass | pass |
| u4 | Who introduced the actor model? | **no** | refuse (isolation) | pass | pass (after the fix below) |

- u3: a question about an upload searches only that upload, and no note is added although an LM35 note is
  published.
- u4 is the privacy check: only the upload knows the answer, and it is asked as any visitor would, without the
  upload's id. Both pipelines answered that they could not find it, citing nothing.

**Problem, fixed:** the KB's u4 reply was "Sorry, I cannot find a relevant answer in the given context." It was
scored as an answer because the refusal rule (2026-10-24) named the source only as datasheets, passages or
documents. `generate._SOURCE_MATERIAL` now also matches "the / given / provided context" (not "in this context").
Re-scoring every saved eval answer changed nothing; `upload_eval rescore` turned KB u4 into a pass. The new rule is
live (zip 22,831,264 bytes).

**Problem, cause unknown:** the KB side of the upload stayed "syncing" for over 15 minutes, against 3.5 minutes in
9a, so the first run stopped at its wait limit before asking anything. No error was logged, and the dev user cannot
read ingestion jobs. The human synced `uploads` in the console; the upload's own job then reported complete. The
cases were run on the same upload (`--upload-id`), so no second upload was used.

## Tools added in 9c

- `deployed_eval`: passes `notes` and `kb_notes`; columns `answer_id` and `notes_used` (from the answer's DynamoDB
  record); `compare`; the Aurora share limited to the step 7 passes; CloudWatch numbers only from matched rows.
- `upload_eval`: `run [--upload-id]` (upload, wait for both pipelines, ask, append rows as they arrive, retries and
  the daily-cap stop as `deployed_eval`) and `rescore` (free).
- `rag/uploads.py`: `multipart` and `settled`, shared with `scripts/call_api.py`.
- Code review 1 of 2 done, 8 of 10 fixed. 1,344 tests pass.

## Spend

| Part | Spend |
|---|---|
| A | $0 |
| B `notes` | $0.013 |
| B `kb_notes` | $0.059 |
| C (8 asks; the upload's embeds about $0.0004) | $0.008 |
| **Total** | **about $0.080** |

Both daily caps were raised to 60 for the day and set back to 50 afterwards (checked).
