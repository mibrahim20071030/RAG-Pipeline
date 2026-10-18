"""Cycle 2 step 3 check: the frozen search gives the same candidates on Aurora as on the local database.

Run from datasheet-rag:  python scripts\\compare_targets.py

For each of the frozen questions, the hybrid search (the frozen strategy, N candidates) runs on both databases. The
lists are compared as (document, page, hash of the chunk text), because the row ids differ between the two. No
paid call: it stops first if either database lacks a question's vector.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from botocore.exceptions import BotoCoreError

from rag import config, db, search
from rag.embed import text_hash
from rag.eval.questions import load_questions


def keys(hits: list[dict]) -> list[tuple[str, int, str]]:
    return [(hit["document"], hit["page"], text_hash(hit["text"])) for hit in hits]


def first_difference(a: list, b: list) -> int | None:
    """The first 1-based rank where the lists differ, or None when they are equal."""
    for rank, (x, y) in enumerate(zip(a, b), start=1):
        if x != y:
            return rank
    return None if len(a) == len(b) else min(len(a), len(b)) + 1


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    questions = load_questions()
    texts = [q["question"] for q in questions]
    local = None
    try:
        local = db.connect(target="local")
        aurora = db.connect(target="aurora")
    except (psycopg.OperationalError, ValueError, BotoCoreError) as error:
        if local is not None:
            local.close()
        sys.exit(f"Cannot reach {'Aurora' if local is not None else 'the local Postgres'} ({error}).\n"
                 "Local: start Docker Desktop and run docker start rag-pg. Aurora: check datasheet-rag\\.env.")
    with local, aurora:
        for name, conn in (("local", local), ("aurora", aurora)):
            missing = search.uncached_questions(conn, texts)
            if missing:
                sys.exit(f"{len(missing)} question vectors are not cached on {name}, so searching would make paid "
                         f"calls. Run: python -m rag.copy_cache --run")
        same = 0
        for question in questions:
            a = keys(search.search(local, question["question"], "hybrid"))
            b = keys(search.search(aurora, question["question"], "hybrid"))
            rank = first_difference(a, b)
            if rank is None:
                same += 1
            else:
                print(f"{question['id']}: differs from rank {rank} (local {len(a)} hits, aurora {len(b)} hits)")
    print(f"\n{same} of {len(questions)} questions give the same {config.N_CANDIDATES} candidates "
          f"({config.DEFAULT_STRATEGY}, hybrid) on both databases.")
    print("OK" if same == len(questions) else "NOT OK: see the differences above.")
    sys.exit(0 if same == len(questions) else 1)


if __name__ == "__main__":
    main()
