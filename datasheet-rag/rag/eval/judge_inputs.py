"""What the RAGAS judge reads (Layer 7b): the Layer 7a answers with the passages they were written from.

The answers come from runs/eval/<experiment>.csv. The passages are rebuilt for free: the question vector and the
rerank scores are read from their caches, and anything that would need a paid call stops the rebuild. A rebuild is
checked against the answer's citations, so a judge score always belongs to the passages the answer model saw.

Nothing here imports RAGAS, so it runs on the project's Python and in CI; `ragas_run` runs in .venv-ragas.
"""
import csv
import math
import re
from pathlib import Path

from rag import config, generate, rerank, search
from rag.chunk_base import approx_tokens
from rag.eval import run_eval

TOKENS_PER_ESTIMATE = 1.3  # real tokens were 1.29 times the characters / 4 estimate (Layer 3, Titan)
EMBED_TOKENS = 100  # tokens assumed per embedded text of the relevancy metric (a short question)


class _NoRerankCall:
    """A rerank client that fails when used: every score of a rebuild must come from rerank_cache."""

    def rerank(self, **kwargs):
        raise RuntimeError("the rebuild must not rerank; a score is missing from rerank_cache")


def load_answers(experiment: str, eval_dir: Path | None = None) -> dict[str, dict]:
    """The answers of one Layer 7a experiment by question id: `answer`, `cited` (list of 'document:page'),
    `not_contained` (bool), `ranked` (the 'document:page' of the first 10 hits the passages were taken from)."""
    path = (config.EVAL_DIR if eval_dir is None else eval_dir) / f"{experiment}.csv"
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and "answer" not in rows[0]:
        raise ValueError(f"{path} has no answers; is {experiment!r} a generation experiment?")
    return {
        row["id"]: {
            "answer": row["answer"],
            "cited": [item for item in row["cited"].split("; ") if item],
            "not_contained": row["not_contained"] == "True",
            "ranked": [item for item in (row["rerank_hits"] or row["search_hits"]).split("; ") if item],
        }
        for row in rows
    }


def rebuild_ranking(conn, text: str, exp: dict, rerank_client=None) -> list[dict]:
    """The ranked hits the answer model's passages were taken from (the first k), rebuilt with no paid call."""
    if exp["hnsw"] or not exp["generate"]:
        raise ValueError(f"{exp['name']!r} is not a generation experiment with exact search")
    vector = None if exp["mode"] == "keyword" else search.question_vector(conn, text, run_eval._no_paid_call)
    hits = run_eval._search(conn, text, exp, vector, run_eval._document_filter(text, exp))
    if exp["rerank"]:
        hits = rerank.rerank(conn, text, hits, k=config.EVAL_RERANK_K, enabled=True,
                             client=rerank_client or _NoRerankCall())
    return hits


def rebuild_passages(conn, text: str, exp: dict, rerank_client=None) -> list[dict]:
    """The passages the answer model got for this question in this experiment."""
    return rebuild_ranking(conn, text, exp, rerank_client)[:exp["k"]]


def judge_usd(input_tokens: float, output_tokens: float) -> float:
    return (input_tokens / 1000 * config.JUDGE_USD_PER_1K_INPUT
            + output_tokens / 1000 * config.JUDGE_USD_PER_1K_OUTPUT)


def strip_labels(answer: str, passages: list[dict]) -> str:
    """The answer without its [n] citations, so the judge reads claims, not labels. A footnote marker copied from
    a passage (GPIO16[2]) stays."""
    kept, end = [], 0
    for match in generate._LABELS.finditer(answer):
        if generate._copied_footnote(answer, match, passages):
            continue
        kept.append(re.sub(r"[ \t]+$", "", answer[end:match.start()]))  # the spaces before a label go with it
        end = match.end()
    kept.append(answer[end:])
    return "\n".join(line.strip() for line in "".join(kept).splitlines()).strip()


def _cited(answer: str, passages: list[dict]) -> list[str]:
    """The 'document:page' of each label the answer cites, in order of first use."""
    sources = generate.resolve_citations(answer, passages)["sources"]
    return [f"{source['document']}:{source['page']}" for source in sources]


def build_sample(question: dict, answer: dict, ranking: list[dict], k: int) -> dict:
    """One judge sample from the rebuilt ranking; its first k hits are the passages. Raises when the ranking is not
    the one Layer 7a wrote, when the passages do not give the answer's citations, or when nothing is left to judge.

    A refusal may have been written with no source or with the pages of the labels after it ("... information.
    [1]"): the 7a CSVs hold both, so both are accepted."""
    names = [f"{hit['document']}:{hit['page']}" for hit in ranking[:len(answer["ranked"])]]
    if names != answer["ranked"]:
        raise ValueError(f"{question['id']}: the rebuilt ranking {names} is not the one Layer 7a wrote, "
                         f"{answer['ranked']}")
    passages = ranking[:k]
    cited = _cited(answer["answer"], passages)
    if cited != answer["cited"] and not (answer["not_contained"] and not answer["cited"]):
        raise ValueError(f"{question['id']}: the rebuilt passages give citations {cited}, Layer 7a wrote "
                         f"{answer['cited']}")
    response = strip_labels(answer["answer"], passages)
    if not response:
        raise ValueError(f"{question['id']}: the answer is only labels; there is nothing to judge")
    return {
        "id": question["id"],
        "type": question["type"],
        "user_input": question["question"],
        "response": response,
        "retrieved_contexts": [f"{generate.passage_label(hit)}\n{hit['text']}" for hit in passages],
        "refused": answer["not_contained"],
        "reference": question["expected_answer"],
    }


def build_samples(conn, questions_: list[dict], exp: dict, answers: dict[str, dict], rerank_client=None) -> list[dict]:
    """A sample for every answerable question. No-answer questions are left out: faithfulness of a refusal is
    undefined, and refusals are already counted in Layer 7a."""
    samples = []
    for question in questions_:
        if not question["sources"]:
            continue
        if question["id"] not in answers:
            raise ValueError(f"{question['id']} has no answer in {exp['name']}")
        ranking = rebuild_ranking(conn, question["question"], exp, rerank_client)
        samples.append(build_sample(question, answers[question["id"]], ranking, exp["k"]))
    return samples


def plan_judge(samples: list[dict], done_ids, calls_per_answer: int, embeds_per_answer: int, context_reads: int,
               overhead_tokens: int, output_tokens: int) -> dict:
    """Calls, tokens, cost and minutes to judge the samples not already done. No call is made.

    Per answer: `calls_per_answer` judge calls with `overhead_tokens` input in total (prompts, question, answer),
    the passages read `context_reads` times, `output_tokens` output in total, and `embeds_per_answer` Titan calls.
    """
    done_ids = set(done_ids)
    todo = [sample for sample in samples if sample["id"] not in done_ids]
    context_tokens = sum(approx_tokens("\n\n".join(sample["retrieved_contexts"])) for sample in todo)
    input_tokens = math.ceil((context_tokens * context_reads + len(todo) * overhead_tokens) * TOKENS_PER_ESTIMATE)
    output_total = len(todo) * output_tokens
    judge_calls, embed_calls = len(todo) * calls_per_answer, len(todo) * embeds_per_answer
    cost = {
        "judge": judge_usd(input_tokens, output_total),
        "embed": embed_calls * EMBED_TOKENS / 1000 * config.EMBED_USD_PER_1K_TOKENS,
    }
    cost["total"] = cost["judge"] + cost["embed"]
    return {
        "answers": len(todo),
        "skipped": len(samples) - len(todo),
        "judge_calls": judge_calls,
        "embed_calls": embed_calls,
        "est_input_tokens": input_tokens,
        "est_output_tokens": output_total,
        "est_cost_usd": cost,
        "est_minutes": judge_calls * config.JUDGE_MIN_INTERVAL_S / 60 + embed_calls / config.EMBED_PER_MIN,
    }
