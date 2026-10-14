# Layer 6 generation smoke test

Run on 2026-10-14 with `python scripts\show_generate.py --yes`. Pipeline: hybrid search (30 candidates) on `fixed_400`, Cohere Rerank 3.5 to the top 5, then Nova Lite (`amazon.nova-lite-v1:0`, temperature 0, at most 512 output tokens).

Result: **6 of 6 passed.** Every answerable question was answered and cited a page that holds the answer. Every unanswerable question got the fixed "not contained" sentence. No answer had an invalid label.

## Answerable

| Question | Answer | Cited | Expected | Result |
|---|---|---|---|---|
| Maximum output current a single I/O pin of the STM32F205 can source | 25 mA | [3] STM32F205 p70 | Table 12, p70 | PASS |
| Output scale factor of the LM35 | 10 mV/°C | [1] LM35 p1, [2] p13, [3] p1 | p1, 13, 15, 16 | PASS |
| Supply voltage range the ESP32 operates from | 2.3 V to 3.6 V | [3] ESP32 p19 | p19, p51 | PASS |

Notes:
- STM32: the reranked top 5 also had an ESP32 chunk (p51) and the STM32 contents and overview pages. The model ignored them and cited only the Table 12 chunk.
- ESP32: the answer quotes section 2.5 ("ranges from 2.3 V to 3.6 V"). It leaves out the 3.3 V recommended value and the "2.3/3.0" note in Table 5-2 (p51), which was also in the passages (labels [1] and [2]). The answer is correct, but a fuller one would add the conditions. Watch this in the Layer 7 RAGAS checks.
- In all three, the model wrote the label after the full stop (`25 mA. [3]`). Both forms are read.

## Unanswerable

| Question | Answer | Result |
|---|---|---|
| Unit price of the ESP32 chip | The provided datasheets do not contain this information. | PASS |
| Bluetooth range of the LM35 | The provided datasheets do not contain this information. | PASS |
| Flash memory of the STM32H743 (not in the set) | The provided datasheets do not contain this information. | PASS |

The STM32H743 question is the hardest of the three. The passages were STM32F205 memory pages, which list flash sizes for a different part. The model did not use them.

## Cost and speed

- Paid calls: 6 question embeddings (82 tokens), 6 rerank queries ($0.012), 6 answers. The answers used 15,998 input and 125 output tokens ($0.00099).
- Total: $0.0130, close to the dry-run estimate of $0.0135.
- Nova Lite took 329 to 568 ms per answer (`metrics.latencyMs`), with about 1,700 to 4,000 input tokens per prompt.
- A re-run pays only for the six answers (about $0.001). Question vectors and rerank scores are cached.
