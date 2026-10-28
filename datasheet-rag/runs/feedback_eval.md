# Step 9b: feedback loop, results (2026-10-24 and 2026-10-28)

Setup and console steps are in `feedback_setup.md`. Rows are in `runs/feedback/`.

## Before the notes (notes off, 12 cases × 2 pipelines, $0.0357)

`python -m rag.eval.correction_eval run --pipeline both --state before`, with `DB_TARGET=aurora`.

| Pipeline | Originals (3) | Paraphrases (6) | Negatives (3) |
|---|---|---|---|
| Ours | 0 passed (2 refused) | 2 passed (1 refused) | 3 passed |
| KB | 0 passed (2 refused) | 1 passed (1 refused) | 3 passed |

- **q08:** both pipelines give only the typical values (±0.2 / ±0.4 °C) and not the tested limits (±0.5 / ±1 °C).
  On ours, one paraphrase says the two parts are equally accurate, which is wrong.
- **q24:** the original is refused on both. Some paraphrases do find GPIO25/26.
- **q33:** refused, or a wrong value: "0.7 VDD" on ours, "3.6 V" on the KB.

## The three notes (written and published by the human on the reviewer page)

| Note | Datasheet, PDF page | Text (short) |
|---|---|---|
| `ec2b9b67…` (q08, misread) | LM35, p.5 | LM35A ±0.5 °C tested (±0.2 typical); LM35 ±1 °C tested (±0.4 typical, page 8) |
| `0f950015…` (q24, false refusal) | ESP32, p.46 | only the two 8-bit DACs: DAC_1 on GPIO25, DAC_2 on GPIO26 |
| `3ba01f26…` (q33, false refusal) | STM32F205/207, p.71 | FT and RST pins up to 5.5 V for 2 V ≤ VDD ≤ 3.6 V |

The pages were found in `runs/parsed/` and confirmed by the human with "Show page". All three are published on
both sides.

## How close each question is to each note (`similarities --kb`, $0.061; `runs/feedback/similarities.csv`)

**Ours (Titan cosine similarity) separates the notes:**

| | Similarity to its own note |
|---|---|
| Originals | 0.90 to 0.93 |
| Paraphrases | 0.73 to 0.90 |
| Negatives | 0.47, 0.51, **0.68** (c33n, to the STM32 note) |
| Best of the other 46 frozen questions | **0.60** (q29, STM32 note), then 0.55 (q30), 0.54 (q38) |

- The current `CORRECTION_MIN_SIMILARITY` of 0.6 is too low: q29 (0.6046) would get the STM32 note.
- **0.70** separates every case: all originals and paraphrases at or above 0.73, every other question at or below
  0.68. The margin is small, about 0.03 on each side.
- Not yet applied (decision pending, below).

**The KB's managed-reranker scores do not separate the notes.** Every question scores 0.37 to 0.50 to its best
note:
- q08 scores 0.441 to its own note, and its negative scores 0.440;
- q01, an unrelated LM35 question, scores 0.476;
- no question reaches `KB_CORRECTION_MIN_SCORE` 0.5, so with no datasheet chosen the KB currently uses no note.

The reranker's scores only order passages within one question (the same was found with Cohere at Layer 5).

**With the reranker off (`rerankingModelType: NONE`, a $0.061 probe, `runs/feedback/kb_scores_no_rerank.csv`),
the scores show only the rank:**
- 1st place gets about 0.77 to 0.80;
- 2nd place gets about 0.43 to 0.50;
- 3rd place gets about 0.20 to 0.26.

This holds whatever the question. For example, q01 gives the LM35 note 0.80, which is more than q08's own 0.77.
With three notes, one of them always comes first. **So the Managed KB gives no absolute closeness score, and no
fixed threshold can work on its side.**

## Decision (2026-10-28): one matcher for both pipelines

The human chose option 1. Both pipelines decide with the Titan cosine similarity and the line
`CORRECTION_MIN_SIMILARITY` = 0.70:
- ours: pgvector on the `corrections` table, with the question vector the search already paid for;
- the AWS managed side: the published list (DynamoDB item `notes#list`) holds each note's text and vector (the
  same float32 numbers as Aurora); the question is embedded once with Titan, only while a note that could fit is
  published, so the KB path still never wakes Aurora.

Why not the KB's own Retrieve: a reranker score only orders passages for one question (its scale moves with the
question, so q08's fit at 0.441 and q01's non-fit at 0.476 overlap), and with the reranker off the score follows
the rank. A passage search only fills the top 10; a note needs a yes/no, which needs a score that means the same on
every question. The KB still finds the datasheet passages with its own Retrieve.

Not needed any more: the notes bucket, the `corrections` data source and its ingestion jobs (to delete in the
console).

## Retract test (2026-10-28, on the site, $0.0014)

The ESP32 p.46 note (q24) was retracted on the reviewer page; the first click hit a paused Aurora (503 after
22.2 s) and showed the bug fixed that day ("Finish retract"). Then q24 asked on both pipelines: both refused with
no note used, as before the note. The note was published again (free: its vector was cached); `notes#list` was
created with all three notes, each vector equal to Aurora's (cosine 1.0).

## After the notes (notes on, 12 cases × 2 pipelines, $0.0179)

`python -m rag.eval.correction_eval run --pipeline both --state after`, with `DB_TARGET=aurora`.

| Pipeline | State | Originals (3) | Paraphrases (6) | Negatives (3) |
|---|---|---|---|---|
| Ours | before | 0 passed (2 refused) | 2 passed (1 refused) | 3 passed |
| Ours | after | **3 passed**, note used 3 | **6 passed**, note used 6 | 3 passed, no note |
| KB | before | 0 passed (2 refused) | 1 passed (1 refused) | 3 passed |
| KB | after | **3 passed**, note used 3 | **6 passed**, note used 6 | 3 passed, no note |

Every case got exactly its own note, and no negative got one. The two pipelines used the same notes on the same
cases. Still to do in 9c: the 49 questions with notes on, to check none of the other 46 gets a note (by the
similarities above, the closest is q29 at 0.60) and that nothing regresses.

## Spend so far in 9b

About $0.18:
- the before eval, $0.036;
- the after eval, $0.018; the retract test, $0.0014;
- 3 asks on the site, $0.0008;
- similarities, $0.061;
- the no-rerank probe, $0.061;
- the publish embeds and verification Retrieves, about $0.005.
