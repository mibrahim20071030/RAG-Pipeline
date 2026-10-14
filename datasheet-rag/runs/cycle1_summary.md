# Cycle 1 summary

Written 2026-10-14 at Layer 8. The detail behind every number is in `runs/eval_review.md` (Layer 7a), `runs/ragas_review.md` (Layer 7b), `runs/results.csv`, `runs/results_ragas.csv` and `runs/cost_log.csv`.

## What was built

A question-answering pipeline over four component datasheets with page-level citations, run locally:

- **Data:** STM32F205/207 (DS6329 Rev 18), ESP32 (v5.3), LM35 (SNIS159H), TCTH0xxxE (Rev. 2.0.A). 311 pages, 259 tables, no scanned pages.
- **Ingest:** pdfplumber parse with repeated headers and footers removed, and tables kept as rows (Layer 1). Seven chunking strategies (Layer 2). Titan Text Embeddings V2 with 1024 numbers, stored in Postgres with pgvector in Docker (Layer 3). 6,298 chunks are stored: 5,501 from the seven strategies plus 797 for `fixed_400_prefix`.
- **Query:** hybrid search (exact vector plus Postgres full-text, merged with RRF) gives 30 candidates. Cohere Rerank 3.5 keeps the best k. Nova Lite answers from numbered passages and cites `[n]`. Our code maps each `[n]` back to a document and PDF page and flags invalid labels (Layers 4–6).
- **Evaluation:** 49 hand-checked questions (43 answerable, 6 with no answer), recall@k and MRR for every setup, answer checks, and a RAGAS judge (Nova Pro) on the best four (Layer 7).
- **Entry point:** `python -m rag.pipeline "<question>"` (Layer 8).

## The chosen setup (frozen in `rag/config.py`)

| Setting | Value |
|---|---|
| Chunking | `fixed_400_prefix`: 400-token chunks; tables as row sentences; embedded with "part (document). section" on a line before the text |
| Search | hybrid: vector plus `english` keywords, 30 candidates each, RRF c = 60 |
| Datasheet filter | off (`detect_part` was not tested together with the prefix) |
| Vector index | none (exact search) |
| Reranker | Cohere Rerank 3.5, on, keeps k = 10 |
| Answer model | Nova Lite, temperature 0, at most 512 output tokens |
| Question set | `data/questions.jsonl`, 49 questions, frozen by SHA-256 (`config.QUESTIONS_SHA256`, checked by `tests/test_questions.py`) |

### Why

| Setup | R@1 | R@5 | R@10 | MRR@10 | Correct page cited | Answerable refused | Faithfulness | Relevancy |
|---|---|---|---|---|---|---|---|---|
| **Chosen: prefix, reranker, k 10** | 0.767 | 0.977 | 1.000 | 0.865 | **40/43** | **2** | **0.972** | **0.859** |
| prefix, reranker, k 5 | 0.767 | 0.977 | 1.000 | 0.865 | 37/43 | 6 | 0.862 | 0.789 |
| `detect_part`, reranker, k 5 | 0.791 | 0.977 | 0.977 | 0.871 | 37/43 | 6 | 0.853 | 0.791 |
| baseline (`fixed_400`), reranker, k 5 | 0.767 | 0.977 | 0.977 | 0.855 | 36/43 | 7 | 0.831 | 0.780 |
| prefix, search order only (no reranker) | 0.767 | 0.953 | 0.977 | 0.834 | — | — | — | — |
| baseline, search order only | 0.651 | 0.930 | 0.953 | 0.766 | — | — | — | — |

Recall and MRR are over the 43 answerable questions; one question is 0.023. All four answer setups refused 6 of 6 no-answer questions (one refusal was reworded; see Known limits).

- **The header prefix** gave the biggest gain in search order of any experiment (MRR 0.766 → 0.834 without the reranker). With the reranker it is level with the best setups and the only one with R@10 1.000.
- **k 10 rather than 5:** with the same retrieval, more passages cut false refusals from 6 to 2 and fixed the one wrong value the judge found (q08, LM35 accuracy). The cost is about 4,300 input tokens per answer instead of 2,300, which adds $0.0001 per query.
- **Reranker on:** it lifts every setup to R@5 0.977 and raises MRR by 0.03 to 0.2.
- **Hybrid search:** without the reranker it is clearly best (R@5 0.930 against 0.814 for vector only and 0.860 for keyword only), and the two halves miss different questions.
- **Not chosen:** `page` chunking was worst (R@5 0.860 reranked). `simple` keywords were worse than `english` in both modes. HNSW is not needed at about 800 chunks per strategy (index recall 0.995, same metrics as exact search). `structure_aware` and `fixed_1024` were within one or two questions of `fixed_400`; 43 questions cannot separate them.

### Cost and latency per query (chosen setup)

- About **$0.0023** for a new question: rerank $0.002, Nova Lite about $0.0003, question embed below $0.00001. A question asked before costs only the answer, because its vector and rerank scores are cached.
- Median in eval: search 63 ms with a cached vector, rerank 0.26 s, answer 0.51 s. On the account's quota, rerank calls must be 20 s apart (3 a minute).

## Pipeline check (Layer 8 test, 2026-10-14)

`python -m rag.pipeline "<question>"`, cost $0.0033 for all five:

| Question | Answer | Cited | Expected |
|---|---|---|---|
| What is the absolute maximum supply voltage of the LM35? (q01) | 35 V | LM35, page 4 | lm35 p4 ✓ |
| How much on-chip SRAM does the ESP32 have? (q18) | 520 KB | ESP32, page 26 | esp32 p26 ✓ |
| What is the maximum total current into the VDD power lines of the STM32F205? (q29) | 120 mA | STM32F205/207, page 70 | p70 ✓ |
| What is the I2C address of the TCTH022BE? (q47, no answer) | "The provided datasheets do not contain this information." | none | not contained ✓ |
| What is the operating temperature range of the TCTH022BE? (new, not in the set) | −40 to 125 °C | TCTH0xxxE, page 2 | page 2, "Operation Temperature Topr −40 to 125 °C" ✓ |

The first four reused cached vectors and rerank scores ($0.0002–0.0003 each). The new question paid for an embed and a rerank ($0.0023; search 2.6 s including the embed, rerank 1.4 s).

## Failure analysis by question type

Reranked R@5 / MRR@10 for the chosen chunks (`header_prefix` row in `runs/results_by_type.csv`):

| Type | Questions | R@5 | MRR@10 | Main failure |
|---|---|---|---|---|
| single value | 10 | 1.000 | 0.950 | False refusals at k 5 (q25, q27) with the answer in the passages |
| table lookup | 22 | 1.000 | 0.845 | Several tables on nearby pages carry similar rows, so the answer is found but not always first |
| rephrased | 6 | 0.833 | 0.769 | q33 ("five-volt-tolerant pin"): the datasheet says "FT" and "VDD + 4 V". Keyword search cannot match it and the hybrid list never brings it up. Also false refusals (q22; q24, where the chunk ends before the DAC sentence) |
| comparison | 5 | 1.000 | 0.900 | q42 needs two datasheets in one list; recall counts a hit on one side as correct, so it overstates these |
| no answer | 6 | — | — | None: no invented value in any setup |

## Known limits (carried into Cycle 2)

- **Reworded refusal.** Nova Lite once refused in its own words ("Sorry, I cannot provide ...", q44), and `is_not_contained` misses that. It is left as it is (seen once in 72 refusals). Such an answer cites nothing, so the pipeline shows "Sources: none" anyway.
- **Mis-citation.** q22 at k 10 gives the right value but cites the wrong page. The judge cannot see this; only the 7a citation check does.
- **False refusals** are the main answer failure. A stronger answer model is the untested option.
- **Keyword tokens.** Postgres keeps `VDD/VBAT` as one token, so `VDD` does not match it. `STM32F205` as an exact word is rare in the text. `-40` is a signed token.
- **`detect_part`** matches by prefix (`LM358` would pick the LM35 datasheet). It is off in the frozen setup.
- **Judge errors.** 2 of 21 judged refusals had a verdict that disagreed with its own reason. Judge scores are reported next to the hand checks, not instead of them.
- **Run-to-run variation.** Answer totals moved by one or two questions between identical runs, even at temperature 0.

## Spend

The whole of Cycle 1 so far, from `runs/cost_log.csv` (3,044 rows): **$2.23**.

| Item | USD |
|---|---|
| Titan embeddings (all chunk loads and questions) | 0.022 |
| Cohere reranking | 1.230 |
| Nova Lite answers | 0.104 |
| Nova Pro judge (RAGAS) | 0.870 |

## Open for Cycle 2

- **Reranker quota.** The account allows 3 rerank requests a minute for everyone together. Either throttle the public API to match, or serve without the reranker. Without it, the prefix search order gives R@5 0.953 and MRR 0.834 against 0.977 / 0.865 with it.
- **Caches need write access.** Every new question adds rows to `embedding_cache` and `rerank_cache`. Decide whether the demo caches.
- **Python version.** Check which versions Lambda supports before packaging (local code is on 3.14; all tests also pass on 3.13).
- The Data API backend for `rag/db.py`, and the vector parameter handling there.
