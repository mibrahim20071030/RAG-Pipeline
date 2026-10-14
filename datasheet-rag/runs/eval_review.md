# Layer 7a evaluation review

Run on 2026-10-14 with the 49 questions in `data/questions.jsonl` (43 answerable, 6 with no answer). Sources: `runs/results.csv`, `runs/results_by_type.csv`, `runs/eval/<experiment>.csv`, logs `runs/eval_run1.log` to `eval_run5.log`.

Spent on Layer 7a: $1.31 (reranking $1.20 of it; question embeds, the header-prefix load $0.005, Nova Lite $0.10 over two generation runs). Every call is in `runs/cost_log.csv`.

How to read the numbers: R@k is the share of the 43 answerable questions with a correct page (any listed `(document, page)`) in the top k; MRR@10 is the mean of 1 / rank of the first correct page, 0 past 10. One question is 0.023, so most gaps below are one or two questions. "Reranker off" is the search order of the same run; the reranker keeps 10 in eval. Latency is the median per question: search with cached question vector, rerank call without the 20 s quota wait.

## Retrieval results

Every experiment changes one thing from the baseline: `fixed_400`, hybrid search, `english` keywords, N = 30, no filter, exact search.

| Experiment | Reranker | R@1 | R@3 | R@5 | R@10 | MRR@10 | Search ms | Rerank ms |
|---|---|---|---|---|---|---|---|---|
| baseline | off | 0.651 | 0.884 | 0.930 | 0.953 | 0.766 | 63 |  |
| baseline | on | 0.767 | 0.930 | 0.977 | 0.977 | 0.855 | 63 | 394 |
| chunk_page | off | 0.488 | 0.791 | 0.837 | 0.930 | 0.651 | 56 |  |
| chunk_page | on | 0.674 | 0.837 | 0.860 | 0.907 | 0.767 | 56 | 462 |
| chunk_fixed_256 | off | 0.651 | 0.884 | 0.930 | 0.953 | 0.767 | 66 |  |
| chunk_fixed_256 | on | 0.744 | 0.930 | 0.953 | 0.977 | 0.834 | 66 | 354 |
| chunk_fixed_256_overlap | off | 0.628 | 0.837 | 0.930 | 0.953 | 0.748 | 67 |  |
| chunk_fixed_256_overlap | on | 0.744 | 0.930 | 0.930 | 0.977 | 0.841 | 67 | 281 |
| chunk_fixed_400_overlap | off | 0.651 | 0.884 | 0.907 | 0.953 | 0.765 | 66 |  |
| chunk_fixed_400_overlap | on | 0.744 | 0.930 | 0.953 | 0.977 | 0.843 | 66 | 275 |
| chunk_structure_aware | off | 0.651 | 0.860 | 0.953 | 0.977 | 0.770 | 65 |  |
| chunk_structure_aware | on | 0.744 | 0.930 | 0.977 | 0.977 | 0.844 | 65 | 301 |
| chunk_fixed_1024 | off | 0.628 | 0.860 | 0.930 | 0.953 | 0.749 | 63 |  |
| chunk_fixed_1024 | on | 0.791 | 0.953 | 0.977 | 0.977 | 0.865 | 63 | 349 |
| vector_only | off | 0.581 | 0.698 | 0.814 | 0.884 | 0.674 | 53 |  |
| vector_only | on | 0.744 | 0.930 | 0.977 | 1.000 | 0.848 | 53 | 278 |
| keyword_only | off | 0.512 | 0.814 | 0.860 | 0.930 | 0.665 | 8 |  |
| keyword_only | on | 0.791 | 0.953 | 0.977 | 0.977 | 0.874 | 8 | 294 |
| simple_hybrid | off | 0.558 | 0.744 | 0.907 | 0.953 | 0.688 | 68 |  |
| simple_hybrid | on | 0.767 | 0.930 | 0.953 | 0.977 | 0.855 | 68 | 260 |
| simple_keyword_only | off | 0.419 | 0.628 | 0.767 | 0.837 | 0.556 | 14 |  |
| simple_keyword_only | on | 0.744 | 0.930 | 0.930 | 0.930 | 0.833 | 14 | 277 |
| detect_part | off | 0.651 | 0.884 | 0.930 | 0.977 | 0.774 | 53 |  |
| detect_part | on | 0.791 | 0.930 | 0.977 | 0.977 | 0.871 | 53 | 264 |
| hnsw (vector) | off | 0.581 | 0.698 | 0.814 | 0.884 | 0.674 | 453 |  |
| hnsw (vector) | on | 0.744 | 0.930 | 0.977 | 1.000 | 0.848 | 453 | 247 |
| header_prefix | off | 0.767 | 0.884 | 0.953 | 0.977 | 0.834 | 63 |  |
| header_prefix | on | 0.767 | 0.953 | 0.977 | 1.000 | 0.865 | 63 | 258 |

What the numbers say:

- **Chunking.** `page` is clearly worst (6 misses at 5 after reranking). The other six are within one or two questions of each other; 43 questions cannot separate `fixed_400`, `structure_aware` and `fixed_1024`. `fixed_400` stays the base.
- **Reranker.** It helps every setup: MRR rises by about 0.08 to 0.2, R@1 by about 0.1. After reranking, vector, keyword and hybrid all reach R@5 0.977, so the reranker hides the gap between search modes at k = 5.
- **Search mode.** Without the reranker, hybrid is clearly best (R@5 0.930 against 0.814 vector and 0.860 keyword). The two halves fail on different questions: vector search never finds q33, keyword search never finds q42. That is the case for keeping both.
- **Keyword configuration.** `english` beats `simple` in both modes. Stemming helps more than it damages part numbers on this set.
- **Datasheet filter.** `detect_part` loses no question and gives slightly better order (MRR 0.871 against 0.855). The keyword weights change inside one datasheet (Layer 4 note), and it did not hurt here.
- **HNSW.** Index recall 0.995 (top 30 of the index against exact top 30), identical retrieval metrics to exact vector search. Its search time (453 ms) is mostly building the index inside each query's rolled-back transaction, so it says nothing about real HNSW speed; after the code review `run_eval` leaves that cell empty instead. With about 800 chunks per strategy, exact search stays (as decided). No HNSW index was left in the database afterwards (checked).
- **Header prefix.** The largest search-order gain of any experiment: R@1 0.651 → 0.767, MRR 0.766 → 0.834 without the reranker, almost what the reranker gives. After reranking it is level with the best (R@5 0.977, MRR 0.865, R@10 1.000). This matters for Cycle 2, where the reranker allows only 3 questions a minute.

## Failures by question type

Reranker on, from `runs/results_by_type.csv` (R@5 / MRR@10):

| Experiment | single value (10) | table lookup (22) | rephrased (6) | comparison (5) |
|---|---|---|---|---|
| baseline | 1.000 / 1.000 | 1.000 / 0.822 | 0.833 / 0.750 | 1.000 / 0.840 |
| vector_only | 1.000 / 0.950 | 1.000 / 0.820 | 1.000 / 0.917 | 0.800 / 0.683 |
| keyword_only | 1.000 / 1.000 | 1.000 / 0.856 | 0.833 / 0.750 | 1.000 / 0.850 |
| header_prefix | 1.000 / 0.950 | 1.000 / 0.845 | 0.833 / 0.769 | 1.000 / 0.900 |

- **Rephrased** is the weak type. q33 ("how high may the voltage on a five-volt-tolerant pin go") is missed by every hybrid and keyword setup: the datasheet writes "FT" pins and "VDD + 4 V", none of the question's words. Vector search finds it after reranking, which is the only reason `vector_only` scores 1.000 here.
- **Comparison** questions need two datasheets in one list. q42 (LM35 against TCTH supply voltage) gets mostly LM35 pages, and the TCTH page arrives late or not at all. A hit on one side counts as correct, so recall overstates how well comparisons are served.
- **Table lookups** are always found within 5 but often not first (MRR about 0.82–0.86): several tables on nearby pages carry similar rows.

## Answers (generation group)

Six setups, all reranked, answered by Nova Lite. The first run found two problems, both fixed with the human's yes before the second run:

1. Nova Lite sometimes wrote the refusal sentence and then labels (`... this information. [1] [2] [3]`). The check counted that as an answer, so "unanswerable refused" showed 67–83 % when it was really 6 of 6. `is_not_contained` now also accepts the sentence followed only by labels, and `citation_hit_rate` counts a refusal as a miss.
2. Nova Lite refused answerable questions whose answer was in a passage (q25: passage [2] is the table row "tRST ... Min (µs): 50"). The prompt got a rule to read every passage, tables included, and to give the refusal without a citation.

Second run (current prompt and check, `runs/eval/gen_*.csv`); the first run recounted under the fixed check is in brackets (`runs/eval/gen_old_prompt/`):

| Experiment | Correct page cited | Answerable refused | Unanswerable refused | Input tokens / answer | Cost / query |
|---|---|---|---|---|---|
| gen_baseline (fixed_400, k 5) | 36/43 (36) | 7 (7) | 6/6 | ~2,300 | $0.00215 |
| gen_fixed_1024 (k 5) | 36/43 (34) | 6 (7) | 6/6 | ~3,700 | $0.00223 |
| gen_detect_part (k 5) | 37/43 (36) | 6 (7) | 6/6 | ~2,400 | $0.00215 |
| gen_prefix_k3 | 34/43 (36) | 7 (5) | 6/6 | ~1,400 | $0.00209 |
| gen_prefix_k5 | 37/43 (37) | 6 (6) | 6/6 | ~2,300 | $0.00215 |
| **gen_prefix_k10** | **40/43 (40)** | **2 (3)** | 6/6 (see q44) | ~4,300 | $0.00227 |

No answer in either run cited a label that does not exist. Cost per query is almost all the reranker ($0.002); Nova Lite adds $0.0001–0.0003.

Read by hand (all flagged answers of both runs, plus a sample of correct ones):

- **The prompt change made no measurable difference.** Totals moved by one or two questions in both directions (q05 now answered in some setups; q34, q43 and q17 newly refused in one setup each). That is the size of run-to-run variation, even at temperature 0. The "no citation" instruction is also ignored: 5 refusals in the second run still end with labels, which the fixed check now catches. The new prompt is kept because it does no harm and matches the current results; it is not counted as an improvement.
- **False refusals are the main answer failure.** q22, q24, q25 and q27 are refused in most setups although the right page is ranked 1 or 2. For q25 and q22 the exact value is in the passages. For q24 the page is right but the chunk sent ends before the DAC sentence, so that one is a chunking limit. More passages help: k 10 refuses only 2.
- **Reworded refusal (q44, prefix k 10):** "Sorry, I cannot provide information about the unit price of the LM35 ... as it is not mentioned in the provided datasheets." It is a correct refusal, but the check does not catch it, so the table shows 5/6. The true count is 6/6 for every setup. This is the reworded-refusal risk from Layer 6; it appeared once in the 72 answers to unanswerable questions (6 questions × 6 setups × 2 runs).
- **Wrong values or citations:**
  - q02 with `fixed_1024` and prefix k 3: "67 µA". That is the LM35A limit; the LM35 limit is 80 µA. Both tables were in the passages, and the model did not say which variant it used.
  - q22 with prefix k 10: the value (40 mA, 1.65–2.0 V) is right, but it is cited to [3] (page 24) instead of [1] (page 51, where the sentence is). That is a mis-citation.
  - q32 with prefix k 3: "+125 °C" cited to page 15, which gives the range; the right number, a different page.
  - q42 with `fixed_1024`: "TCTH0xxxE has a supply voltage of 3.3 V". That is a test condition, not the rating; the TCTH page with 1.7–5.5 V was not in the passages.
- No answer invented a value for an unanswerable question.

## Shortlist for 7b (RAGAS)

The best three, plus the baseline for reference:

1. **`fixed_400_prefix`, hybrid, reranker, k 10.** Best answers (40/43 cited, 2 false refusals) and best retrieval in both orders. It is the leading candidate for Layer 8.
2. **`fixed_400_prefix`, hybrid, reranker, k 5.** Same retrieval, half the tokens. It tests whether k 10's gain holds under a judge.
3. **`fixed_400`, hybrid, `detect_part`, reranker, k 5.** Best plain-chunk setup.
4. **Baseline (`fixed_400`, hybrid, reranker, k 5)** for reference.

Open for Layer 8, not tuned here (one change at a time, and not to individual questions):
- prefix together with `detect_part`;
- serving without the reranker in Cycle 2 (header prefix, search order: R@5 0.953, MRR 0.834);
- a stronger answer model for false refusals;
- the refusal check for reworded refusals.
