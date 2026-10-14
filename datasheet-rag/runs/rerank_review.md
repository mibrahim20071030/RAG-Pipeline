# Layer 5 rerank review (2026-10-13)

The two-chunk test, then the five Layer 4 questions on `fixed_400`: the hybrid top 5 before reranking and the top 5 after Cohere Rerank 3.5 scored all 30 candidates.
Run with `python scripts\show_rerank.py`. Cost: 7 rerank calls, $0.014 (6 for this run, and 1 earlier call for the two-chunk test before a cache key change). The scores are cached, so the second run made 0 paid calls; its output is at the end.

"Answer chunk" is the same as in `runs/search_review.md`: a chunk that states the value asked for, found by reading the stored text. It is not a checked ground truth; that comes at Layer 7.

## Two-chunk test

Question: "What is the maximum current per GPIO pin?" against two rows of STM32F205 Table 12 (page 70).

| Row | Score |
|---|---|
| IIO, output current source by any I/Os and control pin, 25 mA (per pin) | 0.4708 |
| IVDD, total current into VDD power lines, 120 mA (total) | 0.0761 |

Passed: the per-pin row scores higher.

## Five questions

| # | Question | Answer chunk | Rank before | Rank after |
|---|---|---|---|---|
| 1 | What is the operating voltage range of the ESP32? | ESP32 p51 | 1 | 1 |
| 2 | What is the accuracy of the LM35CA at 25 °C? | LM35 p6 / p5 accuracy tables | 1 and 3 | 1 and 2 |
| 3 | How much current does the LM35 draw when it is running? | LM35 p13 / p1 text ("draws only 60 µA"), p10 quiescent current table | 4 and 5 (p1 was 11) | 1, 2 and 4 |
| 4 | What is the maximum output current sourced by any I/O pin on the STM32F205? | STM32 p70 Table 12, p104 text | 2 and 1 | 1 (p104 left the top 5) |
| 5 | TCTH022BE supply voltage | TCTH p2 table (Supply Voltage, VDD) | 4 | 4 |

What it shows:

- An answer chunk is in the top 5 for all five questions, as before, and now at rank 1 for four of them (three before).
- Question 3 gained the most. The question is worded differently from the datasheet, and the two text chunks that answer it in words moved from 4 and 11 to 1 and 2.
- Question 2: the two accuracy tables are now 1 and 2; the text chunk that only repeats the table heading dropped out.
- Question 4 is mixed. The Table 12 chunk moved to rank 1, but the p104 text chunk left the top 5, and an ESP32 chunk and two contents or cover chunks of the STM32 datasheet came in at 2 to 5. The reranker scores a contents page that repeats the question's words quite high.
- Question 5 did not improve: the answer chunk stays at 4. The two chunks from other datasheets left the top 5, so all five are now from the TCTH datasheet, but three TCTH chunks without the answer are above it. A bare part number plus two words gives the reranker little to read; its scores here are the lowest of the five questions (0.52 to 0.60).
- Scores are not comparable across questions: the best hit scores 0.87 for question 1 and 0.60 for question 5. Do not use one fixed score threshold.
- Five questions are not a measurement. Layer 7 decides whether the reranker stays on.

## Access problem on the first run

- What: the first call was answered, then the next was refused with "not authorized to perform the required AWS Marketplace actions (aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe)".
- Why: Cohere Rerank is sold through AWS Marketplace, and the first use subscribes the account. The dev user's policy did not allow that.
- Fix: the human added the two actions to the dev user and ran the script; that call was refused too, but it started the subscription (AWS sent an "offer accepted" email, agreement start 21:00 UTC). The run a few minutes later went through.

## Full output (second run, 0 paid calls)

```
Paid rerank calls (not cached): 0, about $0.000, about 0 seconds of waiting between calls.

== Two-chunk test: What is the maximum current per GPIO pin?
    0.4708 | Symbol: IIO, Ratings: Output current source by any I/Os and control pin, Max: 25, Unit: mA
    0.0761 | Symbol: IVDD, Ratings: Total current into VDD power lines (source), Max: 120, Unit: mA
  OK: the per-pin row scored higher.

Strategy: fixed_400. Hybrid search gives 30 candidates; the top 5 are shown.

== What is the operating voltage range of the ESP32?
  hybrid, before reranking
    1. esp32_datashee p51  text  0.0328 | this minimum voltage is 2.3 V; • For more information, see Section 1 ESP32 Serie
    2. esp32_datashee p51  text  0.0320 | 5 Electrical Characteristics 5.1 Absolute Maximum Ratings Stresses above those l
    3. esp32_datashee p19  text  0.0297 | The internal LDO can be configured as having 1.8 V, or the same voltage as VDD3P
    4. esp32_datashee p44  text  0.0282 | • The input voltage range of GPIO pins within VDD3P3_RTC domain should strictly 
    5. esp32_datashee p43  text  0.0282 | • Half-duplex (CSMA/CD) and full-duplex operation • MAC control sublayer (contro
  after reranking (was = place before)
    1. esp32_datashee p51  text  0.8732 was  1 | this minimum voltage is 2.3 V; • For more information, see Section 1 ESP32 Serie
    2. esp32_datashee p51  text  0.7971 was  2 | 5 Electrical Characteristics 5.1 Absolute Maximum Ratings Stresses above those l
    3. esp32_datashee p43  text  0.7318 was  5 | • Half-duplex (CSMA/CD) and full-duplex operation • MAC control sublayer (contro
    4. esp32_datashee p19  text  0.7228 was  3 | The internal LDO can be configured as having 1.8 V, or the same voltage as VDD3P
    5. esp32_datashee p24  text  0.7060 was 12 | Figure 3-2. Chip Boot Flow uart_download_dis controls boot mode behaviors: It pe

== What is the accuracy of the LM35CA at 25 °C?
  hybrid, before reranking
    1. lm35           p6   table 0.0307 | 6.6 Electrical Characteristics: LM35A, LM35CA PARAMETER: Accuracy, TEST CONDITIO
    2. lm35           p6   text  0.0306 | 6.6 Electrical Characteristics: LM35A, LM35CA Unless otherwise noted, these spec
    3. lm35           p5   table 0.0296 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Accuracy, TEST C
    4. lm35           p5   text  0.0295 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits Unless otherwise noted, the
    5. lm35           p5   table 0.0292 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Minimum temperat
  after reranking (was = place before)
    1. lm35           p6   table 0.8149 was  1 | 6.6 Electrical Characteristics: LM35A, LM35CA PARAMETER: Accuracy, TEST CONDITIO
    2. lm35           p5   table 0.7743 was  3 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Accuracy, TEST C
    3. lm35           p1   text  0.7051 was  6 | 0.1°C in still air. The LM35 device is rated to operate over a -55°C to • HVAC 1
    4. lm35           p6   table 0.6791 was 24 | 6.6 Electrical Characteristics: LM35A, LM35CA PARAMETER: Load regulation 0 ≤ IL 
    5. lm35           p5   table 0.6536 was 19 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Line regulation,

== How much current does the LM35 draw when it is running?
  hybrid, before reranking
    1. lm35           p19  text  0.0297 | 9 Power Supply Recommendations The LM35 device has a very wide 4-V to 30-V power
    2. lm35           p8   table 0.0290 | 6.7 Electrical Characteristics: LM35, LM35C, LM35D Limits PARAMETER: Change of q
    3. lm35           p8   table 0.0284 | 6.7 Electrical Characteristics: LM35, LM35C, LM35D Limits PARAMETER: Load regula
    4. lm35           p13  text  0.0283 | 7 Detailed Description 7.1 Overview The LM35-series devices are precision integr
    5. lm35           p10  table 0.0283 | PARAMETER: Quiescent current, TEST CONDITIONS: VS = 30 V, 25°C, TEST CONDITIONS:
  after reranking (was = place before)
    1. lm35           p13  text  0.8428 was  4 | 7 Detailed Description 7.1 Overview The LM35-series devices are precision integr
    2. lm35           p1   text  0.7937 was 11 | Product Order Technical Tools & Support & Folder Now Documents Software Communit
    3. lm35           p10  table 0.6693 was  6 | PARAMETER: Line regulation, TEST CONDITIONS: TA = 25°C, LM35 MIN / LM35 TYP / LM
    4. lm35           p10  table 0.6615 was  5 | PARAMETER: Quiescent current, TEST CONDITIONS: VS = 30 V, 25°C, TEST CONDITIONS:
    5. lm35           p10  text  0.6539 was  7 | Electrical Characteristics: LM35, LM35C, LM35D (continued) Unless otherwise note

== What is the maximum output current sourced by any I/O pin on the STM32F205?
  hybrid, before reranking
    1. DS_CD00237391  p104 text  0.0328 | or source up to ±3mA. When using the PC13 to PC15 GPIOs in output mode, the spee
    2. DS_CD00237391  p70  table 0.0320 | Table 12. Current characteristics Symbol: IVDD, Ratings: Total current into VDD 
    3. DS_CD00237391  p103 text  0.0315 | Table 46. I/O static characteristics (continued) 1. Guaranteed by design, not te
    4. DS_CD00237391  p104 table 0.0305 | Table 47. Output voltage characteristics(1) Symbol: VOL, Parameter: Output low l
    5. DS_CD00237391  p106 text  0.0293 | Table 48. I/O AC characteristics(1) (continued) 1. The I/O speed is configured u
  after reranking (was = place before)
    1. DS_CD00237391  p70  table 0.6766 was  2 | Table 12. Current characteristics Symbol: IVDD, Ratings: Total current into VDD 
    2. esp32_datashee p51  text  0.6061 was  6 | 5 Electrical Characteristics 5.1 Absolute Maximum Ratings Stresses above those l
    3. DS_CD00237391  p9   text  0.5815 was 14 | . . . . . . . . . . . 41 Figure 11. STM32F20x WLCSP64+2 ballout. . . . . . . . .
    4. DS_CD00237391  p5   text  0.5585 was  8 | STM32F20xxx Contents 6.1.7 Current consumption measurement . . . . . . . . . . .
    5. DS_CD00237391  p1   text  0.5125 was 15 | STM32F205x x STM32F207xx Arm®-based 32-bit MCU, 150 DMIPs, up to 1 MB Flash/128+

== TCTH022BE supply voltage
  hybrid, before reranking
    1. TCTH022BE_data p3   text  0.0307 | Pin Assignment (Top view) TCTH0x1xE TCTH0x2xE PTCO PTCGOOD PTCO PTCGOOD 5 4 5 4 
    2. esp32_datashee p19  text  0.0298 | single-power supply, the recommended voltage of the power supply is 3.3 V, and i
    3. DS_CD00237391  p27  text  0.0288 | specified threshold, below which the device must be maintained under reset, is 1
    4. TCTH022BE_data p2   table 0.0288 | Operating Ranges Characteristics: Supply Voltage, Symbol: VDD, Operating Ranges:
    5. TCTH022BE_data p4   table 0.0281 | List for Product name, Output type, Top marking Product name: TCTH011AE, PTCO ou
  after reranking (was = place before)
    1. TCTH022BE_data p5   table 0.5994 was 13 | 8-4. TCTH0x2BE (RESET included) VDD: VDD < VUVLO, RESET: X, VPTCO: X, PTCGOOD: I
    2. TCTH022BE_data p8   text  0.5603 was 30 | 10.2. Operation description (TCTH0x2AE, TCTH0x2BE) PTCO output current and PTC T
    3. TCTH022BE_data p11  table 0.5484 was  6 | Unless otherwise specified, VDD = 3.3 V, VPTCO = 0.61 V, Tj = 25 °C PTCO output 
    4. TCTH022BE_data p2   table 0.5242 was  4 | Operating Ranges Characteristics: Supply Voltage, Symbol: VDD, Operating Ranges:
    5. TCTH022BE_data p4   table 0.5193 was  5 | List for Product name, Output type, Top marking Product name: TCTH011AE, PTCO ou
```
