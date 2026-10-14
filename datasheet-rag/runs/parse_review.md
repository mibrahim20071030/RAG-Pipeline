# Parse review (Layer 1)

Parsed on 2026-10-12 with `python -m rag.parse`. None of the four datasheets is scanned: every page has selectable text.

| Datasheet | Pages | Tables kept | Text chars | Sub/superscripts moved | Unmapped glyphs | Pages with no printed label |
|---|---|---|---|---|---|---|
| DS_CD00237391.pdf (STM32F20x) | 181 | 160 | 283,689 | 8,557 | 4 | 1 |
| esp32_datasheet_en.pdf | 78 | 61 | 116,764 | 258 | 0 | 1, 78 |
| lm35.pdf | 38 | 25 | 52,872 | 840 | 6 | 1, 28 to 38 (package addendum) |
| TCTH022BE_datasheet_en_20230425.pdf | 14 | 13 | 19,211 | 517 | 0 | none |

## Table pages checked beside the PDF

The Layer 1 test asks for at least 3 table pages per datasheet. Claude checked all 12 against a rendered image of each page. First result: 9 matched and 3 had defects. Both defects were fixed on 2026-10-12 and the 3 pages were checked again against the same images, so all 12 now match. The human spot-checks one page with `python scripts\show_parsed.py <doc> <page>`.

| Datasheet | Page | Table | Checked by | Result |
|---|---|---|---|---|
| STM32 | 70 | Tables 12, 13, 14 (current, thermal, operating conditions) | Claude | All values, units and symbols match. Subscripts joined (IVDD, TSTG, fPCLK1). Σ, ±, °C correct. |
| ESP32 | 52 | Tables 5-3, 5-4 (DC characteristics, RF current) | Claude | All values match. ×, Ω, ~ correct. Footnote marker shows as "VDD 1" (see limits). |
| LM35 | 5 | 6.5 Electrical Characteristics | Claude | All 20 data rows match, including which of the TESTED and DESIGN columns each value sits in. ±, ≤, µ, ° correct. |
| TCTH0xxxE | 6 | 9. Electrical characteristics | Claude | All values match. µA and µs correct. |
| STM32 | 71 | Table 14 (continued) | Claude | Match. All values, units and merged cells correct. |
| STM32 | 90 | Table 30 (HSE oscillator) | Claude | Match. kΩ, µA and the two IDD condition rows correct. |
| ESP32 | 20 | Pin and flash tables | Claude | Match after the fix. Was: `GPIO16` with footnote 2 read `GPIO162`. Now `GPIO16[2]` and `VDD_SDIO[1]`. |
| ESP32 | 55 | Tables 5-8, 5-9 (Bluetooth radio) | Claude | Match. All values and units correct. |
| LM35 | 4 | 6.1 to 6.4 (ratings, ESD, thermal) | Claude | Match after the fix. Was: MIN and MAX shared one cell. Now `–0.2` is under MIN and `35` under MAX; a lone MAX value (10, 150) is under MAX. |
| LM35 | 6 | 6.6 Electrical Characteristics | Claude | Match after the fix. Was: every value sat under the first column of its group. Now `±0.2` is under TYP and `±0.5` under MAX, as in the PDF. |
| TCTH0xxxE | 2 | Absolute Maximum Ratings, Operating Ranges | Claude | Match. Both tables correct. |
| TCTH0xxxE | 4 | Product name list | Claude | Match. All eight products correct. |

What to look for: each value under the right column header, units on the right rows, and Ω, µ, ±, ≤, ° and subscripts unchanged.

## Defects found in the review and fixed (2026-10-12)

1. Values lost their column in tables drawn without vertical lines (LM35 pages 4, 6, 7, 9, 10; 102 rows). Why: the parser finds columns from ruled lines, and these tables rule the header (MIN, TYP, MAX) but not the rows below, so a row's values shared one wide cell. Fix (`split_values_by_column`): a wide cell that holds only numbers on one line, under a row that has separate header cells, is split and each number goes to the column it sits under. Not split: wording, and a single number centred in a truly merged cell (it applies to every column under it, as in the STM32 features table).
2. A footnote marker directly after a name or number was glued to it (ESP32 page 20: `GPIO162`). Why: the marker is a small raised digit with no space before it. Fix (in `snap_scripts`): a raised number is written in square brackets, `GPIO16[2]`, `VDD [1]`, `power domain [1, 2]`. Left alone: markers that already have brackets, `(1)`; a raised number inside a name (I2C, I2S); and a raised number after a bare 10, which is a power and is written `10^6`. Known leftovers in one STM32 formula: `2[15]` for 2 to the power 15 and `10[3]` for a thousand.

## Found by the human's review

- LM35 page 6 (2026-10-12): the header "TEST CONDITIONS" came out as two cells, "TEST CONDI" and "TIONS". Cause: the header spans two columns in the PDF, but the table was detected with a column border running through it, so the word was cut at that border. Fix: when a word crosses the border between two neighbouring cells, the cells are read as one. This also repaired "LM3" + "5", "3 P" + "INS" (page 4), the same header on pages 7, 9 and 10, and "LM35C, LM35D" (page 8). Four graph grids that had been kept as tables (pages 11, 12, 15) are no longer kept, so the LM35 count went from 29 to 25.

## How to read `show_parsed.py`

Each line of the grid is one table row. `^` means the cell is merged with the one above it or to its left. `(blank)` means the cell is empty in the PDF; a plain `-` is a dash printed in the PDF. Long cells are shortened with "..."; add `--full` to see them whole.

## What the parser changes

- Word spacing: `x_tolerance` 1.5 instead of pdfplumber's default 3. The default glued words together in the ESP32 and LM35 files ("FunctionalDescription").
- Subscripts and superscripts are moved onto the line of the character they follow, so "V" + "DD" on the next line becomes "VDD".
- Symbol-font glyphs in the STM32 file are mapped to real characters (Ω, ≤, ≥, ±, ×, °, Σ, bullets). 381 such glyphs before, 0 after.
- µ and Ω are stored in one spelling each (micro sign, Greek omega), and the minus sign U+2212 becomes "-".
- Headers and footers are removed when the same line (digits ignored) sits at the same height in the top 9% or bottom 12% of at least 3 pages. The removed lines are listed per file under `removed_lines` in the JSON. Table captions and rows inside a table are never removed.
- The printed page label is the number that changes inside the removed footer. Landscape pages take the number between their neighbours.
- Vertical text on portrait pages is read bottom to top (pin-table headers such as "LQFP100"). On landscape pages the sideways header and footer are dropped.
- A table is kept when it has at least 2 rows with at least 2 filled cells. Boxes, figure grids that contain a "Figure n." caption line (a table that only mentions a figure is kept), and tables nested in another table are dropped.
- Caption: the nearest "Table n" line above the table, else the nearest numbered heading, else a short line directly above. A table with no caption that is the first thing on its page takes the previous page's caption plus "(continued)".

## Known limits (carry into Layer 2)

- Merged cells come out as `null` (shown as `^` by `show_parsed.py`). A unit or parameter name that spans several rows is filled only on the first row, so chunking must fill down. A cell that spans columns sits in the first of them.
- Two-row headers (LM35, TCTH) are two separate rows. Chunking must combine them ("LM35A TYP").
- Footnote markers: a bare raised number is bracketed (`GPIO16[2]`). A marker printed with its own brackets stays inline, as in "1.8(1)" and "Accuracy(3)"; the chunker can strip `(n)` and `[n]` from value cells.
- An en dash used as a minus sign is left as it is ("–65 to +150").
- Some figures are still parsed as tables: ballout diagrams and timing figures in the STM32 file (pages 41, 45, 134 to 137, 162, captioned "Figure n"), two uncaptioned fragments (pages 103, 163), and graph grids in the LM35 file (pages 11, 12, 18).
- Text inside figures stays in the page text as loose words (for example the waveform labels on TCTH page 6).
- Unmapped glyphs: 6 in the LM35 file (the µ in graph axis labels, shown as "(cid:29)") and 4 on STM32 page 1 (logo).
- Header removal also took the titles of the LM35 package-drawing pages ("PACKAGE OUTLINE", "EXAMPLE BOARD LAYOUT").
