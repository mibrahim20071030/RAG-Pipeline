# Layer 7b RAGAS review

Run on 2026-10-14. RAGAS 0.4.3 in `.venv-ragas` (Python 3.13), Nova Pro (`amazon.nova-pro-v1:0`) as judge through LiteLLM, Titan V2 for the relevancy vectors. Sources: `runs/results_ragas.csv`, `runs/ragas/<experiment>.csv` (one row per answer, with the judge's unsupported statements and reasons, and the question it wrote back), log `runs/ragas_run1.log`.

What was judged: the exact Layer 7a answers (`runs/eval/gen_*.csv`) of the four shortlisted setups, 43 answerable questions each, 172 answers. The passages were rebuilt from the cached question vectors and rerank scores, and every rebuilt answer gives the same citations as in 7a, so each score belongs to the passages Nova Lite saw. The 6 no-answer questions were not judged (7a already shows 6/6 refused in every setup). `[n]` labels were taken out of the answers before judging.

Spent on 7b: $0.87 (smoke run $0.02, full run $0.85; estimate $0.95). 516 + 9 judge calls, no retry, no throttling.

## The two metrics as run

- **Faithfulness**: the judge splits the answer into statements, then marks each one supported (1) or not (0) by the passages, with a reason. Score = share supported. A refusal is a statement too ("the datasheets do not contain ..."), so a refusal of a question whose answer is in the passages scores 0.
- **Answer relevancy**: the judge writes the question the answer seems to reply to, and flags it noncommittal if the answer dodges. Score = cosine of that question with the real one (Titan), 0 when noncommittal. Strictness 1 (RAGAS's default is 3): at temperature 0 the three calls would write the same question.

## Results

| Setup | Faithfulness | Relevancy | Faithfulness (answered only) | Relevancy (answered only) | 7a: correct page cited | 7a: answerable refused |
|---|---|---|---|---|---|---|
| **gen_prefix_k10** | **0.972** | **0.859** | **0.995** (41) | 0.901 | 40/43 | 2 |
| gen_prefix_k5 | 0.862 | 0.789 | 0.974 (37) | 0.917 | 37/43 | 6 |
| gen_detect_part | 0.853 | 0.791 | 0.978 (37) | 0.919 | 37/43 | 6 |
| gen_baseline | 0.831 | 0.780 | 0.979 (36) | 0.932 | 36/43 | 7 |

Every answer got a faithfulness score (no NaN). Judge cost per answer: about $0.0062 with k 10, $0.0045 with k 5.

What the numbers say:

- **The ranking matches 7a.** `gen_prefix_k10` is best on both metrics over all answers. The gap comes mostly from false refusals: the judge scores a refusal 0 on both metrics when the answer was in the passages, so the all-answer means follow the refusal counts (2, 6, 6, 7).
- **When Nova Lite does answer, it sticks to the passages.** Faithfulness over answered questions is 0.97–0.995 in every setup. The few unsupported statements are listed below.
- **Relevancy does not separate the setups.** Over answered questions it is 0.90–0.93, and the order is the reverse of the refusal counts only because the setups answer different sets of questions. A low score here mostly means the answer's wording differs from the question's (q05 "push or pull current" vs. "sourcing and sinking"; q27 "power supply Espressif recommends"), not a worse answer. Nova Lite restates the question in its answer, so the written-back question is often the same as the original word for word (cosine 1.0).

## Read by hand

Every answer below 1.0 on faithfulness or below 0.85 on relevancy, and every refusal, was read against its passages (about 40 rows over the four setups). A sample of 8 answers scored 1.0 / 1.0 in `gen_prefix_k10` (q04, q10, q16, q21, q30, q36, q40, q41) was checked against the expected answers: all 8 are right.

Where the judge is right, and catches what 7a could not:

- **q08, a wrong answer that 7a counted as correct.** In `gen_prefix_k5`, `gen_detect_part` and `gen_baseline`, Nova Lite wrote that the LM35 has the same accuracy as the LM35A (±0.2 °C typical, ±0.5 °C tested). The datasheet gives ±0.4 °C typical and ±1 °C tested for the LM35. The passages it had (pages 5 and 6) give only the LM35A and LM35CA columns, so it filled in the LM35 itself. 7a counted it as correct because the cited pages are in the question's source list. The judge marked those statements unsupported (faithfulness 0.25–0.40). With k 10 the LM35 page 9 is in the passages and the answer is right (±0.4 °C), faithfulness 1.0.
- **q19, outside knowledge.** "The ESP32 is a microcontroller" is not in the passages (they say "Wi-Fi-and-Bluetooth combo chip"). The judge is strict but correct (0.8).
- **False refusals (q22, q24, q25, q27, q34 at k 5; q05 in the baseline):** faithfulness 0 with reasons that point at the passage holding the answer (q22: "the maximum current the internal LDO can offer is 40 mA"; q25: "tRST"). These are the same false refusals found by hand in 7a, now found by the judge.
- **q25 at k 10, a self-contradicting answer.** Nova Lite gives "50 µs [2]" and then ends with the refusal sentence. 7a counted it as an answer (it cites a page). The judge gives faithfulness 1.0 but marks it noncommittal (relevancy 0), which flags it.
- **q43 in the three k 5 setups, half an answer.** STM32F205 2000 V, "ESP32 rating not provided". Marked noncommittal (relevancy 0).

Where the judge is wrong:

- **q33 in `gen_detect_part` and `gen_baseline`:** the refusal is correct (hybrid search never finds the FT-pin page, 7a), and the judge's own reason says the passages do not mention it, yet the verdict is 0. The same refusal scored 1.0 in the two prefix setups. Verdict and reason disagree in 2 of the 21 refusals judged.
- **q34 at k 5:** the verdict (0, the refusal is wrong) is right, but the reason says the passages do not give the 10 kcycle / 55 °C row, which they do (page 98, 20 years).
- **q22 at k 10:** the mis-citation found in 7a (right value, cited to page 24 instead of 51) is not seen, because faithfulness checks the statements against all passages together and the labels were taken out. Citations stay checked by 7a's citation hit rate.

## What follows

- **For Layer 8:** `fixed_400_prefix`, hybrid, reranker, **k 10** stays the leading setup. It is best on 7a's citations and refusals and on both judge metrics, and k 10 also fixed the one wrong value found here (q08). Cost per query is still almost all the reranker.
- **The judge is worth its cost as a second check:** it found a wrong answer (q08) and a self-contradicting one (q25) that the 7a rules counted as correct. It also makes mistakes, so its scores are reported next to the 7a numbers, not instead of them.
- Not done: no context metrics (7a's recall@k already measures retrieval against hand-checked pages); no judging of the no-answer questions.
