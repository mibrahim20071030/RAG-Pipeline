# Layer 4 search review (2026-10-13)

Five hand-picked questions on `fixed_400`, 30 candidates per search, top 5 looked at by eye.
Run with `python scripts\show_search.py`. Cost: 5 Titan calls, 70 tokens, about $0.0000014; the vectors are cached, so a second run made 0 paid calls. The full output is at the end.

"Answer chunk" below means a chunk that states the value asked for. It was found by reading the stored chunk text, not by opening the PDF, so it is not a checked ground truth; that comes at Layer 7.

| # | Question | Answer chunk | Vector | Keyword english | Keyword simple | Hybrid |
|---|---|---|---|---|---|---|
| 1 | What is the operating voltage range of the ESP32? | ESP32 p51 (electrical characteristics, minimum voltage 2.3 V) | 1 | 1 | 1 | 1 |
| 2 | What is the accuracy of the LM35CA at 25 °C? | LM35 p5 / p6 tables (Accuracy, TA = 25°C, LM35CA ±0.2 typ, ±0.5 limit) | 10 (table); the p6 text chunk is 3 | 1 | not in the top 5 (text chunks only) | 1 |
| 3 | How much current does the LM35 draw when it is running? | LM35 p1 / p13 text ("draws only 60 µA") and the quiescent current tables (p7, p8, p10) | 4 (p10 table) | 2 and 3 (p13, p1) | 3 and 5 | 4 (p13) and 5 (p10) |
| 4 | What is the maximum output current sourced by any I/O pin on the STM32F205? | STM32 p70 Table 12 (current characteristics) and p104 text | 1 and 3 | 1 and 2 | 1 and 3 | 1 and 2 |
| 5 | TCTH022BE supply voltage | TCTH p2 table (Supply Voltage, VDD, operating range 1.7 to 5.5 V) | 11 | 8 | not in the top 5 | 4 |

What it shows:

- Hybrid has an answer chunk in the top 5 for all five questions, and at rank 1 for three.
- The two sides fail on different questions. For question 2 the vector side puts the table at rank 10 and keyword search puts it first. For question 3 the wording differs from the datasheet and the vector side finds the quiescent current table that keyword search misses.
- `english` beat `simple` on questions 2 and 5: `simple` ranked text chunks above the table chunks that hold the values. Five questions are not a measurement; Layer 7 decides.
- Question 5 is the weak one. The part number is only in 2 chunks (pin assignment and product list), and the chunk with the answer does not contain it, so keyword search lifts the wrong TCTH chunks and two chunks from other datasheets reach the hybrid top 5. The reranker (Layer 5) and the datasheet filter experiment (Layer 7) are the places where this should improve.
- Rank 1 came from the right datasheet in every search but one: keyword `simple` on question 3 put a TCTH chunk first.

## Full output

```
Questions: 5. Paid Titan calls (not cached): 0, about 0 tokens, about $0.000000.

Strategy: fixed_400. Each search takes 30 candidates; the top 5 are shown.

== What is the operating voltage range of the ESP32?
  vector
    1. esp32_datashee p51  text  0.7159 | this minimum voltage is 2.3 V; • For more information, see Section 1 ESP32 Serie
    2. esp32_datashee p51  text  0.5886 | 5 Electrical Characteristics 5.1 Absolute Maximum Ratings Stresses above those l
    3. esp32_datashee p5   text  0.5701 | • Motor PWM • LED PWM up to 16 channels Power Management • Fine-resolution power
    4. esp32_datashee p2   text  0.5644 | Product Overview ESP32 is a single 2.4 GHz Wi-Fi-and-Bluetooth combo chip design
    5. esp32_datashee p6   text  0.5482 | Note: Check the link or the QR code to make sure that you use the latest version
  keyword (english)
    1. esp32_datashee p51  text  0.1106 | this minimum voltage is 2.3 V; • For more information, see Section 1 ESP32 Serie
    2. TCTH022BE_data p2   table 0.0924 | Operating Ranges Characteristics: Supply Voltage, Symbol: VDD, Operating Ranges:
    3. esp32_datashee p51  text  0.0849 | 5 Electrical Characteristics 5.1 Absolute Maximum Ratings Stresses above those l
    4. esp32_datashee p19  text  0.0844 | The internal LDO can be configured as having 1.8 V, or the same voltage as VDD3P
    5. DS_CD00237391  p72  text  0.0740 | Table 15. Limitations depending on the operating power supply range 1. The numbe
  keyword (simple)
    1. esp32_datashee p51  text  0.1259 | this minimum voltage is 2.3 V; • For more information, see Section 1 ESP32 Serie
    2. DS_CD00237391  p70  text  0.1117 | Table 12. Current characteristics 1. All main power (VDD, VDDA) and ground (VSS,
    3. DS_CD00237391  p72  text  0.1113 | Table 15. Limitations depending on the operating power supply range 1. The numbe
    4. DS_CD00237391  p73  text  0.1067 | Figure 21. Number of wait states versus fCPU and VDD range 8 7 6 5 4 3 2 1 0 ai1
    5. esp32_datashee p19  text  0.1043 | The internal LDO can be configured as having 1.8 V, or the same voltage as VDD3P
  hybrid (english; v = vector rank, k = keyword rank)
    1. esp32_datashee p51  text  0.0328 v1/k1 | this minimum voltage is 2.3 V; • For more information, see Section 1 ESP32 Serie
    2. esp32_datashee p51  text  0.0320 v2/k3 | 5 Electrical Characteristics 5.1 Absolute Maximum Ratings Stresses above those l
    3. esp32_datashee p19  text  0.0297 v11/k4 | The internal LDO can be configured as having 1.8 V, or the same voltage as VDD3P
    4. esp32_datashee p44  text  0.0282 v13/k9 | • The input voltage range of GPIO pins within VDD3P3_RTC domain should strictly 
    5. esp32_datashee p43  text  0.0282 v10/k12 | • Half-duplex (CSMA/CD) and full-duplex operation • MAC control sublayer (contro

== What is the accuracy of the LM35CA at 25 °C?
  vector
    1. lm35           p15  text  0.6407 | 2.0 1.5 1.0 0.5 0.0 ±0.5 ±1.0 ±1.5 ±2.0 ±75 ±25 25 75 125 175 TEMPERATURE ERROR 
    2. lm35           p1   text  0.6082 | Product Order Technical Tools & Support & Folder Now Documents Software Communit
    3. lm35           p6   text  0.6021 | 6.6 Electrical Characteristics: LM35A, LM35CA Unless otherwise noted, these spec
    4. lm35           p13  text  0.5919 | 7 Detailed Description 7.1 Overview The LM35-series devices are precision integr
    5. lm35           p5   text  0.5869 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits Unless otherwise noted, the
  keyword (english)
    1. lm35           p6   table 0.1170 | 6.6 Electrical Characteristics: LM35A, LM35CA PARAMETER: Accuracy, TEST CONDITIO
    2. lm35           p5   table 0.1121 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Accuracy, TEST C
    3. lm35           p5   table 0.1063 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Minimum temperat
    4. lm35           p15  table 0.0948 | Table 1. Design Parameters PARAMETER: Accuracy at 25°C, VALUE: ±0.5°C PARAMETER:
    5. lm35           p7   table 0.0937 | PARAMETER: Change of quiescent current, TEST CONDITIONS: Tested Limit, LM35A MAX
  keyword (simple)
    1. lm35           p1   text  0.1482 | 0.1°C in still air. The LM35 device is rated to operate over a -55°C to • HVAC 1
    2. lm35           p6   text  0.1383 | 6.6 Electrical Characteristics: LM35A, LM35CA Unless otherwise noted, these spec
    3. lm35           p5   text  0.1378 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits Unless otherwise noted, the
    4. lm35           p9   text  0.1308 | 6.8 Electrical Characteristics: LM35, LM35C, LM35D Unless otherwise noted, these
    5. lm35           p8   text  0.1304 | 6.7 Electrical Characteristics: LM35, LM35C, LM35D Limits Unless otherwise noted
  hybrid (english; v = vector rank, k = keyword rank)
    1. lm35           p6   table 0.0307 v10/k1 | 6.6 Electrical Characteristics: LM35A, LM35CA PARAMETER: Accuracy, TEST CONDITIO
    2. lm35           p6   text  0.0306 v3/k8 | 6.6 Electrical Characteristics: LM35A, LM35CA Unless otherwise noted, these spec
    3. lm35           p5   table 0.0296 v14/k2 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Accuracy, TEST C
    4. lm35           p5   text  0.0295 v5/k11 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits Unless otherwise noted, the
    5. lm35           p5   table 0.0292 v15/k3 | 6.5 Electrical Characteristics: LM35A, LM35CA Limits PARAMETER: Minimum temperat

== How much current does the LM35 draw when it is running?
  vector
    1. lm35           p8   table 0.5360 | 6.7 Electrical Characteristics: LM35, LM35C, LM35D Limits PARAMETER: Load regula
    2. lm35           p2   text  0.5129 | Electrical Characteristics: LM35A, LM35CA ............. 6 11.1 Receiving Notific
    3. lm35           p9   table 0.5120 | 6.8 Electrical Characteristics: LM35, LM35C, LM35D PARAMETER: Sensor gain (avera
    4. lm35           p10  table 0.5119 | PARAMETER: Quiescent current, TEST CONDITIONS: VS = 30 V, 25°C, TEST CONDITIONS:
    5. lm35           p10  table 0.5090 | PARAMETER: Line regulation, TEST CONDITIONS: TA = 25°C, LM35 MIN / LM35 TYP / LM
  keyword (english)
    1. lm35           p19  text  0.0896 | 9 Power Supply Recommendations The LM35 device has a very wide 4-V to 30-V power
    2. lm35           p13  text  0.0851 | 7 Detailed Description 7.1 Overview The LM35-series devices are precision integr
    3. lm35           p1   text  0.0829 | Product Order Technical Tools & Support & Folder Now Documents Software Communit
    4. DS_CD00237391  p9   text  0.0675 | . . . . . . . . . . . . . . . . . . 73 Figure 23. Typical current consumption vs
    5. DS_CD00237391  p80  text  0.0668 | Figure 25. Typical current consumption vs. temperature, Run mode, code with data
  keyword (simple)
    1. TCTH022BE_data p9   text  0.1436 | 10.3. How to choose PTC thermistor The resistor value of PTC thermistor is incre
    2. lm35           p14  text  0.1376 | 8 Application and Implementation NOTE Information in the following applications 
    3. lm35           p13  text  0.1343 | 7 Detailed Description 7.1 Overview The LM35-series devices are precision integr
    4. lm35           p19  text  0.1287 | 9 Power Supply Recommendations The LM35 device has a very wide 4-V to 30-V power
    5. lm35           p1   text  0.1274 | Product Order Technical Tools & Support & Folder Now Documents Software Communit
  hybrid (english; v = vector rank, k = keyword rank)
    1. lm35           p19  text  0.0297 v15/k1 | 9 Power Supply Recommendations The LM35 device has a very wide 4-V to 30-V power
    2. lm35           p8   table 0.0290 v7/k11 | 6.7 Electrical Characteristics: LM35, LM35C, LM35D Limits PARAMETER: Change of q
    3. lm35           p8   table 0.0284 v1/k23 | 6.7 Electrical Characteristics: LM35, LM35C, LM35D Limits PARAMETER: Load regula
    4. lm35           p13  text  0.0283 v22/k2 | 7 Detailed Description 7.1 Overview The LM35-series devices are precision integr
    5. lm35           p10  table 0.0283 v4/k19 | PARAMETER: Quiescent current, TEST CONDITIONS: VS = 30 V, 25°C, TEST CONDITIONS:

== What is the maximum output current sourced by any I/O pin on the STM32F205?
  vector
    1. DS_CD00237391  p104 text  0.6585 | or source up to ±3mA. When using the PC13 to PC15 GPIOs in output mode, the spee
    2. DS_CD00237391  p106 text  0.6384 | Table 48. I/O AC characteristics(1) (continued) 1. The I/O speed is configured u
    3. DS_CD00237391  p70  table 0.5611 | Table 12. Current characteristics Symbol: IVDD, Ratings: Total current into VDD 
    4. DS_CD00237391  p103 text  0.5303 | Table 46. I/O static characteristics (continued) 1. Guaranteed by design, not te
    5. DS_CD00237391  p104 table 0.5046 | Table 47. Output voltage characteristics(1) Symbol: VOL, Parameter: Output low l
  keyword (english)
    1. DS_CD00237391  p104 text  0.1382 | or source up to ±3mA. When using the PC13 to PC15 GPIOs in output mode, the spee
    2. DS_CD00237391  p70  table 0.1214 | Table 12. Current characteristics Symbol: IVDD, Ratings: Total current into VDD 
    3. DS_CD00237391  p103 text  0.1191 | Table 46. I/O static characteristics (continued) 1. Guaranteed by design, not te
    4. esp32_datashee p52  table 0.1039 | Table 5-3. DC Characteristics (3.3 V, 25 °C) Parameter: CIN, Description: Pin ca
    5. esp32_datashee p52  text  0.0946 | 5.3 DC Characteristics (3.3 V, 25 °C) Table 5-3. DC Characteristics (3.3 V, 25 °
  keyword (simple)
    1. DS_CD00237391  p104 text  0.1918 | or source up to ±3mA. When using the PC13 to PC15 GPIOs in output mode, the spee
    2. esp32_datashee p51  text  0.1537 | 5 Electrical Characteristics 5.1 Absolute Maximum Ratings Stresses above those l
    3. DS_CD00237391  p70  table 0.1524 | Table 12. Current characteristics Symbol: IVDD, Ratings: Total current into VDD 
    4. esp32_datashee p52  text  0.1499 | 5.3 DC Characteristics (3.3 V, 25 °C) Table 5-3. DC Characteristics (3.3 V, 25 °
    5. DS_CD00237391  p76  text  0.1351 | Table 19. Embedded reset and power control block characteristics (continued) 1. 
  hybrid (english; v = vector rank, k = keyword rank)
    1. DS_CD00237391  p104 text  0.0328 v1/k1 | or source up to ±3mA. When using the PC13 to PC15 GPIOs in output mode, the spee
    2. DS_CD00237391  p70  table 0.0320 v3/k2 | Table 12. Current characteristics Symbol: IVDD, Ratings: Total current into VDD 
    3. DS_CD00237391  p103 text  0.0315 v4/k3 | Table 46. I/O static characteristics (continued) 1. Guaranteed by design, not te
    4. DS_CD00237391  p104 table 0.0305 v5/k6 | Table 47. Output voltage characteristics(1) Symbol: VOL, Parameter: Output low l
    5. DS_CD00237391  p106 text  0.0293 v2/k16 | Table 48. I/O AC characteristics(1) (continued) 1. The I/O speed is configured u

== TCTH022BE supply voltage
  vector
    1. TCTH022BE_data p12  text  0.5618 | TCTH0x1xE Operation TCTH0x2xE Operation VDD = 3.3 V, VPTCO = 0 V to 0.61 V VDD =
    2. TCTH022BE_data p11  table 0.4551 | Unless otherwise specified, VDD = 3.3 V, VPTCO = 0.61 V, Tj = 25 °C PTCO output 
    3. TCTH022BE_data p5   table 0.4499 | 8-4. TCTH0x2BE (RESET included) VDD: VDD < VUVLO, RESET: X, VPTCO: X, PTCGOOD: I
    4. TCTH022BE_data p5   table 0.4429 | 8-2. TCTH0x1BE (without RESET) VDD: VDD < VUVLO, VPTCO: X, PTCGOOD: Indefinite V
    5. TCTH022BE_data p9   table 0.4423 | Product name: TCTH01xxE, PTCO output current (Typ.): 1 µA, PTC thermistor resist
  keyword (english)
    1. TCTH022BE_data p3   text  0.0654 | Pin Assignment (Top view) TCTH0x1xE TCTH0x2xE PTCO PTCGOOD PTCO PTCGOOD 5 4 5 4 
    2. DS_CD00237391  p4   text  0.0541 | supply scheme . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 
    3. esp32_datashee p19  text  0.0498 | single-power supply, the recommended voltage of the power supply is 3.3 V, and i
    4. TCTH022BE_data p4   table 0.0468 | List for Product name, Output type, Top marking Product name: TCTH011AE, PTCO ou
    5. DS_CD00237391  p105 text  0.0464 | Unless otherwise specified, the parameters given in Table 48 are derived from te
  keyword (simple)
    1. TCTH022BE_data p3   text  0.0642 | Pin Assignment (Top view) TCTH0x1xE TCTH0x2xE PTCO PTCGOOD PTCO PTCGOOD 5 4 5 4 
    2. DS_CD00237391  p4   text  0.0555 | supply scheme . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 
    3. TCTH022BE_data p4   table 0.0467 | List for Product name, Output type, Top marking Product name: TCTH011AE, PTCO ou
    4. esp32_datashee p51  table 0.0457 | Table 5-2. Recommended Power Supply Characteristics Parameter: VDDA, VDD3P3_RTC,
    5. esp32_datashee p19  text  0.0442 | single-power supply, the recommended voltage of the power supply is 3.3 V, and i
  hybrid (english; v = vector rank, k = keyword rank)
    1. TCTH022BE_data p3   text  0.0307 v10/k1 | Pin Assignment (Top view) TCTH0x1xE TCTH0x2xE PTCO PTCGOOD PTCO PTCGOOD 5 4 5 4 
    2. esp32_datashee p19  text  0.0298 v12/k3 | single-power supply, the recommended voltage of the power supply is 3.3 V, and i
    3. DS_CD00237391  p27  text  0.0288 v8/k11 | specified threshold, below which the device must be maintained under reset, is 1
    4. TCTH022BE_data p2   table 0.0288 v11/k8 | Operating Ranges Characteristics: Supply Voltage, Symbol: VDD, Operating Ranges:
    5. TCTH022BE_data p4   table 0.0281 v20/k4 | List for Product name, Output type, Top marking Product name: TCTH011AE, PTCO ou
```
