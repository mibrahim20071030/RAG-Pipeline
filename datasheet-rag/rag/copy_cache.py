"""Copy the paid-for vectors of `embedding_cache` from the local database to Aurora (Cycle 2 step 3).

Run from datasheet-rag:  python -m rag.copy_cache          (prints the vectors on both sides, then stops)
                         python -m rag.copy_cache --run    (copies the vectors Aurora does not have yet)

No Bedrock call is made: the vectors were paid for in Cycle 1. With them in Aurora, `python -m rag.load` run
with DB_TARGET=aurora finds every vector in the cache and only chunks and stores. Only the missing rows are
sent, so a copy that stopped carries on, and a second run copies nothing.
"""
import argparse
import sys
from collections import defaultdict

import psycopg
from botocore.exceptions import BotoCoreError

from rag import db

BATCH = 500


def copy_embeddings(source, dest, batch: int = BATCH) -> dict:
    """Store in `dest` every `embedding_cache` row of `source` that `dest` does not have. Rows already in `dest`
    are left as they are. Returns the rows read from `source` and the rows copied."""
    read = copied = 0
    after = None
    while rows := db.embedding_rows(source, after=after, limit=batch):
        read += len(rows)
        after = rows[-1][:2]
        by_model = defaultdict(dict)
        for text_sha256, model, vector in rows:
            by_model[model][text_sha256] = vector
        for model, vectors in by_model.items():
            have = db.cached_hashes(dest, list(vectors), model)
            missing = {key: vector for key, vector in vectors.items() if key not in have}
            db.store_embeddings(dest, model, missing)
            copied += len(missing)
    return {"read": read, "copied": copied}


def print_counts(label: str, counts: dict[str, int]) -> None:
    print(f"{label}: {sum(counts.values()):,} vectors")
    for model, count in counts.items():
        print(f"    {model}: {count:,}")


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Copy embedding_cache from the local database to Aurora.")
    parser.add_argument("--run", action="store_true", help="copy; without it only the counts are printed")
    args = parser.parse_args(argv)

    try:
        local = db.connect(target="local")
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach the local Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    with local:
        try:
            aurora = db.connect(target="aurora")
        except (psycopg.OperationalError, ValueError, BotoCoreError) as error:
            sys.exit(f"Cannot reach Aurora ({error}).\nCheck the AURORA_* settings in datasheet-rag\\.env and the "
                     "AWS credentials.")
        with aurora:
            copy_or_count(local, aurora, args.run)


def copy_or_count(local, aurora, run: bool) -> None:
    db.create_schema(aurora)
    print_counts("local ", db.embedding_counts(local))
    print_counts("aurora", db.embedding_counts(aurora))
    if not run:
        print("\nNothing was copied. Add --run to copy the missing vectors (no Bedrock call, no cost log row).")
        return
    done = copy_embeddings(local, aurora)
    print(f"\nRead {done['read']:,} local vectors, copied {done['copied']:,} to Aurora.")
    print_counts("aurora", db.embedding_counts(aurora))


if __name__ == "__main__":
    main(sys.argv[1:])
