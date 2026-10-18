"""Layer 3 check: stored rows per document and strategy beside the Layer 2 chunk counts, and the vector size.

Run from datasheet-rag:  python scripts\\show_db.py
                         python scripts\\show_db.py --strategy fixed_400_prefix   (only that strategy is expected,
                                                                                  as on Aurora)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from rag import config, db, load, parse


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Stored rows beside the Layer 2 chunk counts.")
    parser.add_argument("--strategy", action="append", choices=load.strategy_names(),
                        help="expect only this strategy (may be repeated); rows of any other count as stale")
    args = parser.parse_args()
    try:
        conn = db.connect()
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    with conn:
        db.create_schema(conn)
        stored = db.strategy_counts(conn)
        ok = True
        if args.strategy or config.DB_TARGET == "aurora":
            # Aurora holds only the frozen strategy (Cycle 2 step 3), so that is what it is checked for by default.
            strategies = list(dict.fromkeys(args.strategy or [config.DEFAULT_STRATEGY]))
            expected_pairs = {(d, s) for d in load.parsed_documents() for s in strategies}
        else:
            expected_pairs = {(d, s) for d in load.parsed_documents() for s in load.strategy_names()}
            # The header-prefix strategies are loaded only for the Layer 7 experiment; checked when stored.
            stored_strategies = {strategy for _, strategy in stored}
            strategies = [s for s in load.strategy_names() if s in config.CHUNK_STRATEGIES or s in stored_strategies]
        for (document, strategy), count in sorted(stored.items()):
            if (document, strategy) not in expected_pairs:
                ok = False
                print(f"STALE: {count} stored rows for {document} / {strategy}, which are not expected here")
        print(f"{'document':34} {'strategy':18} {'chunks':>7} {'stored':>7}")
        totals = dict.fromkeys(strategies, 0)
        for document in load.parsed_documents():
            parsed = parse.load_parsed(document)
            for strategy in strategies:
                expected = len(load.strategy_inputs(parsed, strategy)[0])
                found = stored.get((document, strategy), 0)
                totals[strategy] += found
                ok &= expected == found
                print(f"{document:34} {strategy:18} {expected:>7} {found:>7}{'' if expected == found else '  MISMATCH'}")
        print()
        for strategy, total in totals.items():
            print(f"{strategy:18} {total:>6,}  (SELECT count(*) gives {db.count_chunks(conn, strategy):,})")
        print(f"{'all strategies':18} {db.count_chunks(conn):>6,}")
        dims = db.embedding_dims(conn)
        ok &= dims == config.EMBED_DIMENSIONS
        print(f"\nvector_dims: {dims}")
        for d in db.list_documents(conn):
            print(f"document {d['id']}: {d['name']} | {d['file']} | {d['part']} | {d['revision']}")
        print()
        for model, count in db.embedding_counts(conn).items():
            print(f"embedding_cache {model}: {count:,} vectors")
        print(f"database size: {db.database_size(conn) / 1e6:,.0f} MB ({config.DB_TARGET})")
    print("\nOK: every count matches Layer 2." if ok else "\nNOT OK: see STALE or MISMATCH above, or the vector size.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
