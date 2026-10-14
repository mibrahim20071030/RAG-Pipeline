# Layer 7 question set: draft for review

Drafted by Claude on 2026-10-14 from the parsed datasheet text (`runs/parsed/*.json`), not from search results, so the set is not tuned to the pipeline.

**Status (2026-10-14): verified and final.** The human asked Claude to do the check. Every table value was compared with the PDF page rendered as an image (pdfplumber, 110 dpi; 33 pages), and the prose answers with the extracted text. All 49 matched except q32, which was widened: page 71 also gives the junction temperature range per suffix (–40 to 105 °C for 6, –40 to 125 °C for 7), so it is now a source and in the answer. All 49 are kept and written to `data/questions.jsonl` (ids q01 to q49 in the order below). The TCTH questions keep the series name `TCTH0xxxE` where the value holds for every part. Side finding: in the second table on TCTH page 9 (several thermistors), the extracted text swaps the two α column labels; no question uses it.

The review instructions below were for the human check and are kept for the record.

How to review: open the PDF at the page(s) in **Sources** (PDF page position, the number your PDF viewer shows, not the printed number). For each question, mark one:

- `OK`: the answer and the pages are right.
- `FIX: ...`: say what to change (wrong value, a page to add or remove, wording).
- `CUT`: drop it.

**Sources** lists every page that answers the question on its own, because a hit on any of them counts as correct. If you see the same value on another page, add it.

Mix: 49 questions in total. 10 single value, 22 table lookups, 5 comparisons, 6 rephrased, 6 with no answer. By datasheet: STM32F205 12, ESP32 12, LM35 9, TCTH0xxxE 7, plus 3 comparisons across datasheets and 6 with no answer. Table lookups are the largest group because most datasheet facts sit in tables. Document names: `DS_CD00237391` = STM32F205/207, `esp32_datasheet_en` = ESP32, `lm35` = LM35, `TCTH022BE_datasheet_en_20230425` = TCTH0xxxE.

## LM35 (`lm35`)

| id | Type | Question | Expected answer | Sources (PDF page) | Your check |
|---|---|---|---|---|---|
| q01 | single_value | What is the absolute maximum supply voltage of the LM35? | 35 V (minimum –0.2 V) | 4 | |
| q02 | table_lookup | What is the maximum quiescent current of the LM35 at VS = 5 V and 25 °C? | 80 µA tested limit (typical 56 µA) | 8, 10 | |
| q03 | table_lookup | What is the junction-to-ambient thermal resistance of the LM35 in the TO-92 (LP) package? | 180 °C/W | 4, 19 (Table 2, TO-92 no heat sink, still air) | |
| q04 | rephrased | If the temperature goes up by one degree Celsius, how much does the LM35 output voltage rise? | 10 mV (10 mV/°C scale factor) | 1, 13, 15 (also the sensor gain rows on 5, 6, 8, 9) | |
| q05 | rephrased | Can the LM35 output pull current in as well as push it out? | It can only source current; its sinking capability is limited to 1 µA (class A output stage) | 13 | |
| q06 | single_value | What capacitive load can the LM35 drive without special precautions? | 50 pF | 14 | |
| q07 | table_lookup | What is the thermal resistance of an LM35 in the TO-220 package with no heat sink in moving air? | 26 °C/W | 19 | |
| q08 | comparison | At 25 °C, how does the accuracy of the LM35A compare with that of the LM35? | LM35A ±0.5 °C tested limit (±0.2 °C typical); LM35 ±1 °C tested limit (±0.4 °C typical) | 5, 6, 8, 9 | |
| q09 | single_value | What supply voltage range does the LM35 operate from? | 4 V to 30 V | 1, 4, 19 | |

## TCTH0xxxE (`TCTH022BE_datasheet_en_20230425`)

| id | Type | Question | Expected answer | Sources | Your check |
|---|---|---|---|---|---|
| q10 | single_value | What is the operating supply voltage range of the TCTH0xxxE series? | 1.7 V to 5.5 V | 2 | |
| q11 | table_lookup | What is the detect voltage VDET of the TCTH0xxxE at 25 °C? | 0.50 V typical (0.42 V min, 0.58 V max) | 6 | |
| q12 | table_lookup | What is the top marking of the TCTH022BE? | PC8 | 3, 4 | |
| q13 | table_lookup | For a single PTC thermistor with a TCTH02xxE, what thermistor resistance at 25 °C is suggested for α = 50 and β = 10? | 1.78 kΩ to 2.7 kΩ | 9 | |
| q14 | rephrased | Which TCTH parts keep the over-temperature flag low until they are reset? | The TCTH0x2xE parts with the latch function (TCTH012AE, TCTH022AE, TCTH012BE, TCTH022BE); the latch is released by RESET high or by re-applying VDD | 4, 5, 8 (also 1: "FLAG signal latch function (TCTH0x2xE)") | |
| q15 | comparison | How much supply current does a TCTH01xxE draw compared with a TCTH02xxE? | 1.8 µA vs 11.3 µA typical (max 2.4 µA vs 14.7 µA at 25 °C) | 1, 6 | |
| q16 | table_lookup | What is the absolute maximum supply voltage of the TCTH0xxxE? | 6.0 V (–0.3 V to 6.0 V) | 2 | |

## ESP32 (`esp32_datasheet_en`)

| id | Type | Question | Expected answer | Sources | Your check |
|---|---|---|---|---|---|
| q17 | table_lookup | What is the ESP32's current consumption in Deep-sleep with the RTC timer and RTC memory on? | 10 µA | 30 (also 5: "Power consumption in Deep-sleep mode is 10 µA") | |
| q18 | single_value | How much on-chip SRAM does the ESP32 have? | 520 KB | 4, 26 | |
| q19 | table_lookup | What is the typical current when the ESP32 transmits 802.11b at 1 Mbps and +19.5 dBm? | 240 mA | 52 | |
| q20 | table_lookup | What is the resistance of the ESP32's internal pull-up resistor? | 45 kΩ typical | 52 | |
| q21 | single_value | How many ADC channels does the ESP32 have, and at what resolution? | Two 12-bit SAR ADCs, up to 18 channels | 4, 43, 44 | |
| q22 | rephrased | When VDD_SDIO is set to 1.8 V by the internal regulator, how much current can it supply? | 40 mA (output 1.65 V to 2.0 V) | 51 | |
| q23 | table_lookup | What is the ADC total error after calibration with attenuation 3? | ±60 mV, over 150 to 2450 mV | 44 | |
| q24 | rephrased | Which ESP32 pins can output a true analog voltage? | GPIO25 and GPIO26 (two 8-bit DAC channels) | 44, 46 (also 14 and 69: pin lists showing DAC_1 / DAC_2) | |
| q25 | single_value | How long must CHIP_PU be held low to reset the ESP32? | At least 50 µs (tRST) | 19 | |
| q26 | table_lookup | What is the low-level sink current of an ESP32 GPIO at 3.3 V with maximum drive strength? | 28 mA typical | 52 | |
| q27 | single_value | What power supply does Espressif recommend for the ESP32 when one supply is used? | 3.3 V, with an output current of 500 mA or more | 19 (also 51: IVDD 0.5 A) | |
| q28 | table_lookup | What is the ESP32's current in Hibernation mode? | 5 µA (RTC timer only) | 30 | |

## STM32F205/207 (`DS_CD00237391`)

| id | Type | Question | Expected answer | Sources | Your check |
|---|---|---|---|---|---|
| q29 | table_lookup | What is the maximum total current into the VDD power lines of the STM32F205? | 120 mA | 70 | |
| q30 | single_value | What is the backup (VBAT) operating voltage range of the STM32F205? | 1.65 V to 3.6 V | 71 (also 19, 25, 68) | |
| q31 | table_lookup | What is the typical Standby current of the STM32F205 at VDD = 3.3 V and 25 °C with backup SRAM off and RTC off? | 2.2 µA | 84 | |
| q32 | table_lookup | What is the maximum junction temperature of the STM32F205? | 125 °C | 70 | |
| q33 | rephrased | When the STM32F205 runs from 3.3 V, how high may the voltage on a five-volt-tolerant pin go? | 5.5 V (for 2 V ≤ VDD ≤ 3.6 V); absolute maximum VDD + 4 V | 71, 69 | |
| q34 | table_lookup | How many years does the STM32F205 Flash keep data after 10,000 cycles at 55 °C? | 20 years | 98 | |
| q35 | table_lookup | What is the accuracy of the STM32F205's internal 16 MHz HSI oscillator at 25 °C? | ±1 % (factory calibrated) | 91 | |
| q36 | table_lookup | What is the average slope of the STM32F205's internal temperature sensor? | 2.5 mV/°C (0.76 V at 25 °C) | 127 | |
| q37 | table_lookup | How long does the STM32F205 take to wake up from Stop mode with the regulator in low-power mode? | 17 µs typical, 40 µs max | 87 | |
| q38 | table_lookup | What is the STM32F205's internal reference voltage VREFINT? | 1.21 V typical (1.18 V to 1.24 V) | 128 | |
| q39 | table_lookup | What is the maximum APB1 clock frequency of the STM32F205? | 30 MHz | 70 | |
| q40 | single_value | How many CAN interfaces does the STM32F205 have? | Two (CAN 2.0B Active) | 1, 13, 14 | |

## Comparisons across datasheets

| id | Type | Question | Expected answer | Sources | Your check |
|---|---|---|---|---|---|
| q41 | comparison | Which runs its CPU faster, the STM32F205 or the ESP32, and at what maximum frequencies? | ESP32 up to 240 MHz; STM32F205 up to 120 MHz | esp32 3, 4, 26, 30; DS 1, 13, 14 | |
| q42 | comparison | Which tolerates a higher supply voltage, the LM35 or the TCTH0xxxE? | LM35: 35 V absolute maximum (4 V to 30 V operating); TCTH0xxxE: 6.0 V absolute maximum (1.7 V to 5.5 V operating) | lm35 4; TCTH 2 | |
| q43 | comparison | Compare the human-body-model ESD ratings of the ESP32 and the STM32F205. | ESP32 ±2000 V; STM32F205 2000 V (class 2; 1000 V on VBAT) | esp32 53; DS 100 | |

Note on comparisons: a hit counts as correct if it is on any listed page, so recall@k only checks that one side was found. Whether the answer covers both sides is for the answer checks (and RAGAS in 7b).

## No answer in the datasheets

| id | Type | Question | Why there is no answer | Your check |
|---|---|---|---|---|
| q44 | no_answer | What is the unit price of the LM35 in quantities of 1,000? | No prices in any datasheet | |
| q45 | no_answer | What is the Wi-Fi range of the ESP32 in metres? | Radio specs give power and sensitivity only, no range | |
| q46 | no_answer | How much Flash memory does the STM32F407 have? | Different part, not in the set (a close sibling of the F205) | |
| q47 | no_answer | What is the I2C address of the TCTH022BE? | It has no I2C interface | |
| q48 | no_answer | Which Bluetooth version does the STM32F205 support? | It has no Bluetooth | |
| q49 | no_answer | What is the MTBF of the ESP32? | Reliability table lists qualification tests, not MTBF | |

That is 49 rows. Cut about 4 to keep the set near 45, or keep all if they all check out.
