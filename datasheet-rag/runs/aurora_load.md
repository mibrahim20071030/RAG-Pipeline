# Cycle 2 step 3: load the data into Aurora

Run on 2026-10-17 from the laptop, against the `datasheet-rag` cluster (Aurora PostgreSQL 17.9, pgvector 0.8.1). No Bedrock call was made: `runs/cost_log.csv` had 3,122 lines before and after.

## What went in, and what did not

| Data | In Aurora | Why |
|---|---|---|
| `documents` | 4 rows | Every chunk points to its datasheet. |
| `chunks`, `fixed_400_prefix` | 797 rows | The frozen strategy (Layer 8), the only one the pipeline searches. |
| `chunks`, the other strategies | no | Experiments only; they stay in the local database. |
| `embedding_cache` | 3,486 vectors (all of them) | The loader takes each chunk's vector from the cache, so it paid nothing. It also holds the 49 question vectors. |
| `rerank_cache` | no | Step 7 must measure real rerank latency and cost. Aurora's own cache starts empty. |

## Steps and results

| Step | Command | Result |
|---|---|---|
| 1 | `python -m rag.copy_cache`, then `--run` | Aurora woke in about 18 s. 3,486 of 3,486 vectors copied in 44 s. |
| 2 | `DB_TARGET=aurora python -m rag.load --strategy fixed_400_prefix` | 797 chunks, 769 different texts, **0 paid calls**. |
| 3 | the same with `--run` | 797 rows (STM32F205 480, TCTH022BE 33, ESP32 196, LM35 88), 0 embedded, 12 s. |
| 4 | `DB_TARGET=aurora python scripts\show_db.py --strategy fixed_400_prefix` | OK. `vector_dims` 1024. Database 38 MB of the 1 GB Free plan limit. |
| 4 | step 3 again | 797 rows, 0 tokens: the count does not change. |
| 5 | `python scripts\compare_targets.py` | **49 of 49** questions give the same 30 hybrid candidates, in the same order, on both databases. 12 s. |

The local database is unchanged: 141 MB, 3,486 cached vectors, all strategies.

## One question end to end on Aurora (paid, approved by the human)

`DB_TARGET=aurora python -m rag.pipeline "What is the absolute maximum supply voltage of the LM35?"` (q01):

- Answer: "The absolute maximum supply voltage of the LM35 is 35 V. [5]", citing LM35 page 4. That is the expected answer and source in `data/questions.jsonl`.
- Times: search 312 ms (question vector from the copied cache), rerank 1,315 ms (a real Cohere call, since Aurora's `rerank_cache` was empty), answer 690 ms.
- Cost: $0.0023 (one rerank query $0.002, Nova Lite 4,846 input and 21 output tokens). Three cost log rows.

## Notes for the next steps

- The document ids differ between the two databases (Aurora numbered them in load order), so they are compared by name, page and text, never by id.
- Aurora's `rerank_cache` is empty. The first time each question is reranked there costs a call (3 a minute on this account).
