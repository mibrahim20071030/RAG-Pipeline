"""RAGAS judge scores for the Layer 7a answers (Layer 7b): faithfulness and answer relevancy, Nova Pro as judge.

Runs only in .venv-ragas (Python 3.13; RAGAS needs scikit-network, which has no 3.14 build). From datasheet-rag:
    .venv-ragas\\Scripts\\python -m rag.eval.ragas_run --shortlist --dry-run    (prints calls, cost, minutes)
    .venv-ragas\\Scripts\\python -m rag.eval.ragas_run gen_prefix_k10 --limit 3 (asks, then judges 3 answers)
    .venv-ragas\\Scripts\\python -m rag.eval.ragas_run --shortlist --yes        (does not ask)

Every judge call goes through one function that keeps calls JUDGE_MIN_INTERVAL_S apart, waits and retries when
Bedrock throttles, and writes the tokens to the cost log. Each judged answer is appended to
runs/ragas/<experiment>.csv at once; a re-run skips the answers already there. RAGAS and LiteLLM are imported only
where they are used, so the rest of this module is tested on the project's Python.

Settings (RAGAS 0.4.3, read from its source on 2026-10-14): faithfulness makes 2 judge calls (statements, then a
verdict per statement against all passages joined); answer relevancy makes `strictness` calls, each writing one
question from the answer, then compares them with the real question by Titan embeddings. At temperature 0 the
calls would write the same question, so strictness is 1 here (RAGAS's default is 3).
"""
import os

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")  # no RAGAS usage telemetry
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # LiteLLM does not download its price list

import argparse
import asyncio
import csv
import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime

from rag import config, costlog, db, search
from rag.eval import experiments, judge_inputs, questions

RELEVANCY_STRICTNESS = 1
CALLS_PER_ANSWER = 2 + RELEVANCY_STRICTNESS  # judge calls per answer when nothing is retried
EMBEDS_PER_ANSWER = RELEVANCY_STRICTNESS  # the real question's vector is already cached from Layer 7a
OVERHEAD_TOKENS = 2000  # judge input per answer besides the passages: RAGAS prompts with examples, question, answer
OUTPUT_TOKENS = 500  # judge output per answer: statements, verdicts with reasons, one question
THROTTLE_RETRIES = 5
THROTTLE_WAIT_S = 30  # first wait after throttling; doubled each retry
ROW_FIELDS = ["id", "type", "refused", "faithfulness", "answer_relevancy", "statements", "unsupported",
              "relevancy_question", "noncommittal", "judge_calls", "input_tokens", "output_tokens"]
SUMMARY_FIELDS = ["experiment", "answers", "faithfulness", "answer_relevancy", "faithfulness_nan",
                  "answered", "faithfulness_answered", "answer_relevancy_answered", "judge_usd", "run_at"]


def _log(input_tokens: int, output_tokens: int) -> None:
    try:
        model = config.JUDGE_MODEL_ID
        costlog.log_cost("judge", model, "input_tokens", input_tokens, judge_inputs.judge_usd(input_tokens, 0))
        costlog.log_cost("judge", model, "output_tokens", output_tokens, judge_inputs.judge_usd(0, output_tokens))
    except OSError as error:
        # The call is paid for; a cost log that cannot be written must not stop the run.
        print(f"WARNING: cost log not written ({error}). Add by hand: judge, {input_tokens} input and "
              f"{output_tokens} output tokens.", file=sys.stderr)


def _is_throttle(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".lower()
    return "ratelimit" in text or "throttl" in text or "too many requests" in text


def logged_completion(completion, sleep=asyncio.sleep, clock=time.monotonic):
    """`completion` (LiteLLM's `acompletion`) wrapped for the judge: calls spaced, throttling retried, tokens
    logged. Returns an async function (instructor needs one to make an async client) with a `stats` dict:
    calls, input_tokens, output_tokens. A call that fails for another reason is raised."""
    stats = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    last = [None]

    async def judge(**kwargs):
        for attempt in range(THROTTLE_RETRIES + 1):
            if last[0] is not None:
                wait = config.JUDGE_MIN_INTERVAL_S - (clock() - last[0])
                if wait > 0:
                    await sleep(wait)
            last[0] = clock()
            try:
                response = await completion(**kwargs)
            except Exception as error:
                if not _is_throttle(error) or attempt == THROTTLE_RETRIES:
                    raise
                await sleep(THROTTLE_WAIT_S * 2 ** attempt)
                continue
            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            stats["calls"] += 1
            stats["input_tokens"] += input_tokens
            stats["output_tokens"] += output_tokens
            _log(input_tokens, output_tokens)
            return response
        raise AssertionError("unreachable")

    judge.stats = stats
    return judge


def make_scorers(conn, judge):
    """The two RAGAS metrics with Nova Pro (through `judge`) and Titan (through our cached question vectors)."""
    from ragas.embeddings.base import BaseRagasEmbedding
    from ragas.llms import llm_factory
    from ragas.metrics.collections import AnswerRelevancy, Faithfulness

    class CachedTitan(BaseRagasEmbedding):
        """Titan V2 vectors kept in embedding_cache and logged, as for every other question vector."""

        def embed_text(self, text: str, **kwargs) -> list[float]:
            return json.loads(search.question_vector(conn, text))

        async def aembed_text(self, text: str, **kwargs) -> list[float]:
            return self.embed_text(text)

    def llm():
        return llm_factory(f"bedrock/{config.JUDGE_MODEL_ID}", provider="litellm", client=judge, adapter="instructor",
                           temperature=config.JUDGE_TEMPERATURE, max_tokens=config.JUDGE_MAX_TOKENS)

    os.environ.setdefault("AWS_REGION_NAME", config.AWS_REGION)
    relevancy = AnswerRelevancy(llm=llm(), embeddings=CachedTitan(), strictness=RELEVANCY_STRICTNESS)
    _record_replies(relevancy)
    return Faithfulness(llm=llm()), relevancy


def _record_replies(metric) -> None:
    """Keep each reply of the metric's own LLM in `metric.replies` (the question written back from the answer and
    its noncommittal flag), so the review can read them. RAGAS still computes the score."""
    metric.replies = []
    generate = metric.llm.agenerate

    async def recorded(prompt, response_model):
        reply = await generate(prompt, response_model)
        metric.replies.append(reply)
        return reply

    metric.llm.agenerate = recorded


async def judge_sample(sample: dict, faithfulness, relevancy) -> dict:
    """Both scores of one answer. Faithfulness follows `Faithfulness.ascore` step by step so that the statements
    and verdicts can be kept for the review by hand."""
    statements = await faithfulness._create_statements(sample["user_input"], sample["response"])
    verdicts = []
    if statements:
        context = "\n".join(sample["retrieved_contexts"])
        verdicts = (await faithfulness._create_verdicts(statements, context)).statements
    faithful = sum(1 for verdict in verdicts if verdict.verdict) / len(verdicts) if verdicts else float("nan")
    relevancy.replies.clear()
    relevant = (await relevancy.ascore(user_input=sample["user_input"], response=sample["response"])).value
    return {
        "faithfulness": faithful,
        "answer_relevancy": float(relevant),
        "relevancy_question": " | ".join(reply.question for reply in relevancy.replies),
        "noncommittal": " | ".join(str(reply.noncommittal) for reply in relevancy.replies),
        "statements": len(verdicts),
        "unsupported": " | ".join(f"{v.statement} -> {v.reason}" for v in verdicts if not v.verdict),
    }


def sample_path(experiment: str):
    return config.RAGAS_DIR / f"{experiment}.csv"


def read_rows(experiment: str) -> list[dict]:
    path = sample_path(experiment)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_row(experiment: str, row: dict) -> None:
    path = sample_path(experiment)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _number(value) -> float | None:
    """A score read back from the CSV; None for an empty or NaN cell."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _mean(values) -> float | None:
    values = [value for value in values if value is not None]
    return statistics.mean(values) if values else None


def summarise(experiment: str, rows: list[dict], run_at: str) -> dict:
    """One row of runs/results_ragas.csv from the judged answers of one experiment."""
    answered = [row for row in rows if row["refused"] != "True"]
    return {
        "experiment": experiment,
        "answers": len(rows),
        "faithfulness": _mean(_number(row["faithfulness"]) for row in rows),
        "answer_relevancy": _mean(_number(row["answer_relevancy"]) for row in rows),
        "faithfulness_nan": sum(1 for row in rows if _number(row["faithfulness"]) is None),
        "answered": len(answered),
        "faithfulness_answered": _mean(_number(row["faithfulness"]) for row in answered),
        "answer_relevancy_answered": _mean(_number(row["answer_relevancy"]) for row in answered),
        "judge_usd": sum(judge_inputs.judge_usd(int(row["input_tokens"]), int(row["output_tokens"])) for row in rows),
        "run_at": run_at,
    }


def write_summary(summary: dict, path=None) -> None:
    """Add or replace the experiment's row in runs/results_ragas.csv."""
    path = config.RAGAS_RESULTS if path is None else path
    rows = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            rows = [row for row in csv.DictReader(f) if row["experiment"] != summary["experiment"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows([*rows, summary])


def todo(samples: list[dict], experiment: str, limit: int | None = None) -> list[dict]:
    """The samples not judged yet, at most `limit` of them."""
    done = {row["id"] for row in read_rows(experiment)}
    left = [sample for sample in samples if sample["id"] not in done]
    return left if limit is None else left[:limit]


def plan(samples_by_exp: dict[str, list[dict]], limit: int | None = None) -> dict:
    """The whole run's calls, cost and minutes, at most `limit` new answers per experiment; no call is made."""
    total = {"answers": 0, "skipped": 0, "judge_calls": 0, "embed_calls": 0, "est_input_tokens": 0,
             "est_output_tokens": 0, "est_cost_usd": 0.0, "est_minutes": 0.0}
    for experiment, samples in samples_by_exp.items():
        left = todo(samples, experiment, limit)
        one = judge_inputs.plan_judge(left, (), CALLS_PER_ANSWER, EMBEDS_PER_ANSWER, 1, OVERHEAD_TOKENS,
                                      OUTPUT_TOKENS)
        for key in total:
            total[key] += one[key]["total"] if key == "est_cost_usd" else one[key]
        total["skipped"] += len(samples) - len(todo(samples, experiment))  # judged before, not cut by the limit
    return total


def print_plan(plan_: dict, names: list[str]) -> None:
    print(f"Experiments: {', '.join(names)}")
    print(f"Answers to judge:       {plan_['answers']:>7,}  ({plan_['skipped']} already judged)")
    print(f"Judge calls (Nova Pro): {plan_['judge_calls']:>7,}  about {plan_['est_input_tokens']:,} input and "
          f"{plan_['est_output_tokens']:,} output tokens, at ${config.JUDGE_USD_PER_1K_INPUT * 1000:.2f} / "
          f"${config.JUDGE_USD_PER_1K_OUTPUT * 1000:.2f} per 1M")
    print(f"Embed calls (Titan):    {plan_['embed_calls']:>7,}")
    print(f"Estimated cost:         ${plan_['est_cost_usd']:.4f}  (retries by RAGAS on a bad reply add calls)")
    print(f"Estimated time:         {plan_['est_minutes']:.1f} minutes  (judge calls are "
          f"{config.JUDGE_MIN_INTERVAL_S} s apart)")


async def run(conn, samples_by_exp: dict[str, list[dict]], limit: int | None, completion, scorers=None) -> None:
    """Judge every sample not done yet, one at a time, in one event loop (LiteLLM's connections belong to it)."""
    judge = logged_completion(completion)
    faithfulness, relevancy = (scorers or make_scorers)(conn, judge)
    for experiment, samples in samples_by_exp.items():
        left = todo(samples, experiment, limit)
        print(f"\n{experiment}: {len(left)} answers to judge", flush=True)
        for sample in left:
            before = dict(judge.stats)
            scores = await judge_sample(sample, faithfulness, relevancy)
            append_row(experiment, {
                "id": sample["id"], "type": sample["type"], "refused": sample["refused"], **scores,
                **{key: judge.stats[key] - before[key] for key in ("input_tokens", "output_tokens")},
                "judge_calls": judge.stats["calls"] - before["calls"],
            })
            print(f"  {sample['id']}  faithfulness {scores['faithfulness']:.2f}  "
                  f"relevancy {scores['answer_relevancy']:.2f}", flush=True)
        summary = summarise(experiment, read_rows(experiment), datetime.now(UTC).isoformat(timespec="seconds"))
        write_summary(summary)


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="RAGAS judge scores for Layer 7a answers.")
    parser.add_argument("names", nargs="*", help="generation experiments (see rag/eval/experiments.py)")
    parser.add_argument("--shortlist", action="store_true", help=f"{', '.join(config.RAGAS_SHORTLIST)}")
    parser.add_argument("--limit", type=int, help="judge at most this many new answers per experiment")
    parser.add_argument("--dry-run", action="store_true", help="print the calls, cost and time, then stop")
    parser.add_argument("--yes", action="store_true", help="do not ask before the paid calls")
    args = parser.parse_args(argv)
    if bool(args.names) == args.shortlist:
        parser.error("name experiments or give --shortlist (one of the two)")
    names = list(config.RAGAS_SHORTLIST) if args.shortlist else args.names
    try:
        exps = [experiments.experiment(name) for name in names]
        questions_ = questions.load_questions()
        answers = {name: judge_inputs.load_answers(name) for name in names}
    except (KeyError, ValueError, OSError) as error:
        sys.exit(f"Cannot start: {error}")

    with db.connect() as conn:
        try:
            samples_by_exp = {exp["name"]: judge_inputs.build_samples(conn, questions_, exp, answers[exp["name"]])
                              for exp in exps}
        except (RuntimeError, ValueError) as error:
            sys.exit(f"Cannot rebuild the Layer 7a passages: {error}")
        plan_ = plan(samples_by_exp, args.limit)
        print_plan(plan_, names)
        if args.dry_run:
            print("\nNothing was called. Leave out --dry-run to run.")
            return
        if plan_["answers"] and not args.yes and input("\nRun? [y/N] ").strip().lower() != "y":
            print("Stopped. Nothing was called.")
            return
        import litellm

        logged_before = len(costlog.read_rows())
        try:
            asyncio.run(run(conn, samples_by_exp, args.limit, litellm.acompletion))
        finally:
            spent = sum(float(row["est_cost_usd"]) for row in costlog.read_rows()[logged_before:])
            print(f"\nThis run: ${spent:.4f} ({config.COST_LOG})")
        print(f"Results: {config.RAGAS_RESULTS}, {config.RAGAS_DIR}")


if __name__ == "__main__":
    main(sys.argv[1:])
