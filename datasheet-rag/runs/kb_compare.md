# Cycle 2 step 8: our pipeline against the Bedrock Managed Knowledge Base

The question: **build your own RAG pipeline, or let AWS handle it?**

- **Ours:** the frozen Cycle 1 setup:
  - our parsing and table chunking (`fixed_400_prefix`);
  - Titan V2;
  - hybrid search (vector + rarity-weighted keywords, RRF) over Aurora;
  - Cohere Rerank 3.5, top 10.
- **Knowledge Base:** a Bedrock Managed KB. AWS parses, chunks (default), embeds, searches (always hybrid) and
  reranks (its managed reranker), and one `Retrieve` call gives the top 10.
- **Same answer step for both:** the 10 passages go to our `generate()` (same prompt, Nova Lite, temperature 0, the
  same citation check). Any difference comes from retrieval.
- Same 49 frozen questions (43 answerable, 6 with no answer) and the same metrics code (`rag/eval/metrics.py`).
- Setup and probe: `runs/kb_setup.md`.

## Local run (2026-10-23)

- Ours: `gen_prefix_k10` (2026-10-14).
- KB: `python -m rag.eval.kb_eval`, $0.0588 for 49 questions.
- Printed by `python scripts\compare_pipelines.py`:

| | Ours | Knowledge Base |
|---|---:|---:|
| R@1 | 0.767 | 0.744 |
| R@3 | 0.953 | 0.860 |
| R@5 | 0.977 | 0.860 |
| R@10 | 1.000 | 0.930 |
| MRR@10 | 0.865 | 0.800 |
| Citation hit (answerable) | 0.930 (40/43) | 0.837 (36/43) |
| Answerable refused | 0.047 (2) | 0.070 (3) |
| Unanswerable refused | 1.000 (6/6, re-scored 2026-10-24; 5/6 before) | 1.000 (6/6) |
| Invalid labels | 0 | 0 |
| Answer ms (median) | 513 | 449 |
| Retrieval ms (median) | search + rerank, cached locally | 980 (max 1,734) |
| Model cost per new question | $0.0023 | $0.0012 |

Recall@5 by question type:

| Type | Ours | Knowledge Base |
|---|---:|---:|
| single_value | 1.000 | 1.000 |
| table_lookup | 1.000 | 0.864 |
| rephrased | 0.833 | 0.833 |
| comparison | 1.000 | 0.600 |

Questions only one pipeline answered right:

| Question | Type | Only | First correct passage (ours, KB) |
|---|---|---|---|
| q12 TCTH022BE top marking | table_lookup | ours | 1, 1 (the KB found the page, the answer did not use it) |
| q39 STM32F205 max APB1 clock | table_lookup | ours | 1, not in top 10 |
| q41 STM32F205 or ESP32, faster CPU | comparison | ours | 1, 3 |
| q42 LM35 or TCTH0xxxE, higher supply voltage | comparison | ours | 2, not in top 10 |
| q44 LM35 unit price (no answer) | no_answer | KB | not a real difference: both refused in substance (see below) |

## Findings so far

- **Refusals are a tie, not a KB win.** Ours showed 5/6 locally only because of q44.
  - In the local run `gen_prefix_k10` (2026-10-14), Nova Lite refused in other words ("Sorry, I cannot provide
    information about the unit price ... as it is not mentioned"). `is_not_contained` only knows the fixed sentence,
    so it counted that as an answer (the known reworded-refusal limit).
  - The same setup refused q44 with the fixed sentence in both deployed passes (6/6), and so did every other Layer 7
    setup.
  - **Update 2026-10-24:** `is_not_contained` now also detects a refusal in other words when the answer cites nothing (a missing-information phrase plus a reference to the datasheets or passages). `scripts/rescore_refusals.py` re-checked all saved answers: only `gen_prefix_k10` q44 changed, so that run is now 6/6 unanswerable refused and its citation hit is unchanged.

- **Ours retrieves better.** R@5 is 0.977 against 0.860, and the gap is in table lookups (1.000 against 0.864) and
  comparisons (1.000 against 0.600).
  - Comparisons need passages from two datasheets in one top 10. Our search keeps both, and the KB's often fills
    the 10 with one.
  - Single values and rephrased questions are equal.
- **Answers follow retrieval.** The citation hit is 0.930 against 0.837, a gap of 4 questions, all table lookups or
  comparisons.
- **The KB is cheaper and simpler per question:** $0.0012 against $0.0023, with no Cohere call. Our 3-a-minute
  rerank quota does not apply, and nothing has to be kept or woken (no Aurora).
- **Page numbers have gaps.** 7 of the 490 KB passages, all ESP32, came back without `_excerpt_page_number`, so they
  cannot be cited to a page (questions q18, q20, q22, q26, q41, q49). The reason is not checked yet.
- **Build effort.**
  - KB: about 1 hour of console work (bucket, Managed KB, sync, one IAM policy), plus `rag/kb.py` (one call and
    a mapping).
  - Ours: Cycle 1 Layers 1–5 (parsing, table chunking, embedding and storage, hybrid search, reranking), several
    days.

## Deployed run (2026-10-24, UTC)

- `python -m rag.eval.deployed_eval run --pass kb`, through `SITE_URL`, 21 s apart, then `logs` and `summary`.
- Ours is the step 7 `first` pass (2026-10-18). Rows in `runs/deployed/kb.csv`, summary in
  `runs/results_deployed.csv`.

| | Ours (step 7 `first`) | Knowledge Base |
|---|---:|---:|
| Questions answered | 49 / 49 | 49 / 49 (q49 asked later, after the cap was raised) |
| Citation hit | 0.930 | 0.837 |
| Answerable refused | 0.047 (2) | 0.093 (4) |
| Unanswerable refused | 1.000 (6/6) | 1.000 (6/6) |
| Same as the local run | 47/49 (46/49 before the 2026-10-24 re-score) | 43/49 |
| Client time p50 / p90 / max | 1,088 / 1,331 / 3,818 ms | 1,662 / 2,134 / 6,184 ms |
| Lambda time p50 | 911 ms | 1,420 ms |
| Retrieval p50 | search 30 + rerank 259 ms | Retrieve 970 ms |
| Answer p50 | 462 ms | 407 ms |
| Aurora connects | every ask (p50 118 ms) | **none** (`connect_ms` empty in all 49) |
| Model cost per question | $0.0022 | $0.0012 |
| Aurora cost per question | $0.0019 (step 7 ACU-hours) | $0 |
| Total per new question | about $0.0041 | about $0.0012 |

- **The cap held.** The KB pipeline's own `kb_asks` count stopped the run at 50 of 50, and ours stayed at 0 of 50.
  - The 50 places: the smoke ask, 48 answered questions, and q21's first send.
  - That first send was answered in Lambda (200 in 1.3 s at 01:42:02 UTC, $0.0012), but the answer never reached
    the client, which timed out after 40 s and sent it again (200 at 01:43:46). The lost answer happened between
    CloudFront and the client and is not explained.
  - q49 was asked afterwards. The human uploaded the code review's zip and raised `KB_DAILY_ASK_LIMIT` to 53 for
    it; 200 in 1.8 s, a correct refusal.
- **Repeatability.** 43 of 49 answers cite the same pages as the local run (ours: 47 of 49), so Nova Lite's run-to-run
  variation is larger on the KB's passages. The deployed citation hit is the same as the local one (0.837).
- **Speed.** The KB is about 0.6 s slower per question, almost all of it in `Retrieve` (about 1 s, against 0.3 s
  for our search and Cohere rerank).
- **Cost.** About a third of ours per new question, counting Aurora. With caching, a repeated question costs ours
  $0.0003 plus Aurora, and costs the KB $0.0012, since the KB has no cache.
- Spend of the deployed KB pass: $0.059 for 49 answers plus $0.0012 for q21's lost first answer (log lines agree).
