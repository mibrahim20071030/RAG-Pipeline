"""Ingest: chunk each parsed datasheet, embed the chunks, store them in Postgres.

Run from datasheet-rag:  python -m rag.load            (prints the number of paid calls and the cost, then stops)
                         python -m rag.load --run      (prints the same, asks, then embeds and stores)
                         python -m rag.load --run --yes   (does not ask)
                         python -m rag.load --run --doc lm35 --strategy fixed_400

A vector that was paid for is kept in the `embedding_cache` table, keyed by the hash of the text, so a text
is embedded once however many strategies produce it, a second run costs nothing, and a run that stopped
halfway carries on from where it stopped.
"""
import argparse
import hashlib
import sys
from itertools import batched

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from rag import chunk, config, costlog, db, embed, parse
from rag.chunk_base import approx_tokens


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_key() -> str:
    """Names the cache entries of one model setup: a change of model or normalisation starts a new set.

    A change of EMBED_DIMENSIONS also needs new tables: the vector columns are created with a fixed size.
    """
    return f"{config.EMBED_MODEL_ID}|{config.EMBED_DIMENSIONS}|normalize={config.EMBED_NORMALIZE}"


def embed_input(chunk_: dict) -> str:
    """The string that is embedded for a chunk. The header-prefix experiment (Layer 7) changes only this."""
    return chunk_["text"]


def parsed_documents() -> list[str]:
    return sorted(path.stem for path in config.PARSED_DIR.glob("*.json"))


def plan_load(conn, documents: list[str], strategies: list[str]) -> dict:
    """What a load would do, with no paid call: chunk counts, and the texts that still need a vector."""
    counts = {}
    texts = {}
    for document in documents:
        parsed = parse.load_parsed(document)
        for strategy in strategies:
            chunks = chunk.chunk_document(parsed, strategy)
            counts[(document, strategy)] = len(chunks)
            texts.update((text_hash(embed_input(c)), embed_input(c)) for c in chunks)
    cached = db.cached_hashes(conn, list(texts), embed_key())
    est_tokens = sum(approx_tokens(text) for key, text in texts.items() if key not in cached)
    return {
        "counts": counts,
        "chunks": sum(counts.values()),
        "unique_texts": len(texts),
        "to_embed": len(texts) - len(cached),
        "est_tokens": est_tokens,
        "est_cost_usd": est_tokens / 1000 * config.EMBED_USD_PER_1K_TOKENS,
    }


def load_document(conn, parsed: dict, strategy: str, embed_fn=embed.embed_texts) -> dict:
    """Chunk one document with one strategy, embed what has no vector yet, and replace its stored rows.

    `embed_fn(texts, label=...)` returns one vector per text, in order.
    """
    document = parsed["document"]
    chunks = chunk.chunk_document(parsed, strategy)
    inputs = [embed_input(c) for c in chunks]
    hashes = [text_hash(text) for text in inputs]
    key = embed_key()

    cached = db.cached_hashes(conn, hashes, key)
    missing = {h: text for h, text in zip(hashes, inputs, strict=True) if h not in cached}
    # Saved batch by batch, so a failure loses at most one batch of paid calls.
    for batch in batched(missing.items(), config.EMBED_BATCH):
        vectors = embed_fn([text for _, text in batch], label=f"{document}/{strategy}")
        db.store_embeddings(conn, key, {h: vector for (h, _), vector in zip(batch, vectors, strict=True)})

    vectors = db.cached_embeddings(conn, hashes, key)
    details = config.DOCUMENTS.get(document, {})
    with conn.transaction():
        document_id = db.upsert_document(conn, document, parsed.get("file"), details.get("part"),
                                         details.get("revision"))
        stored = db.replace_chunks(conn, document_id, strategy, chunks, [vectors[h] for h in hashes])
    return {"document": document, "strategy": strategy, "chunks": stored, "embedded": len(missing)}


def print_plan(plan: dict) -> None:
    print(f"Chunks to store:        {plan['chunks']:>7,}")
    print(f"Different texts:        {plan['unique_texts']:>7,}")
    print(f"Paid calls (not cached):{plan['to_embed']:>7,}")
    print(f"Estimated tokens:       {plan['est_tokens']:>7,}  (characters / {config.CHARS_PER_TOKEN}; the real count differs)")
    print(f"Estimated cost:         ${plan['est_cost_usd']:.4f}  at ${config.EMBED_USD_PER_1K_TOKENS * 1000:.2f} per 1M tokens")


def print_spend(logged_before: int) -> None:
    """Tokens and cost of the cost log rows added since `logged_before` rows. Never hides another error."""
    try:
        new_rows = costlog.read_rows()[logged_before:]
        tokens = sum(int(row["quantity"]) for row in new_rows)
        cost = sum(float(row["est_cost_usd"]) for row in new_rows)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"\nCould not read the cost log ({error}). Check {config.COST_LOG} by hand.")
        return
    print(f"\nThis run: {tokens:,} tokens, ${cost:.4f}, {len(new_rows)} cost log rows ({config.COST_LOG})")


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Embed the chunks and store them in Postgres.")
    parser.add_argument("--run", action="store_true", help="embed and store; without it only the cost is printed")
    parser.add_argument("--yes", action="store_true", help="with --run: do not ask before the paid calls")
    parser.add_argument("--doc",choices=parsed_documents(), help="one parsed datasheet (default: all)")
    parser.add_argument("--strategy", choices=list(config.CHUNK_STRATEGIES), help="one strategy (default: all)")
    args = parser.parse_args(argv)
    documents = [args.doc] if args.doc else parsed_documents()
    strategies = [args.strategy] if args.strategy else list(config.CHUNK_STRATEGIES)
    if not documents:
        sys.exit(f"No parsed datasheets in {config.PARSED_DIR}. Run: python -m rag.parse")

    try:
        conn = db.connect()
    except psycopg.OperationalError as error:
        sys.exit(f"Cannot reach Postgres ({error}).\nStart Docker Desktop, then run: docker start rag-pg")
    with conn:
        db.create_schema(conn)
        plan = plan_load(conn, documents, strategies)
        print_plan(plan)
        if not args.run:
            print("\nNothing was embedded. Add --run to embed and store.")
            return
        if plan["to_embed"] and not args.yes and input("\nMake these paid calls? [y/N] ").strip().lower() != "y":
            print("Stopped. Nothing was embedded.")
            return

        logged_before = len(costlog.read_rows())
        print()
        try:
            for document in documents:
                parsed = parse.load_parsed(document)
                if document not in config.DOCUMENTS:
                    print(f"WARNING: {document} is not in config.DOCUMENTS, so it gets no part or revision.")
                for strategy in strategies:
                    done = load_document(conn, parsed, strategy)
                    print(f"{document:34} {strategy:18} {done['chunks']:>5} rows, {done['embedded']:>5} embedded",
                          flush=True)
        except (ClientError, BotoCoreError, psycopg.Error) as error:
            source = "The database" if isinstance(error, psycopg.Error) else "Bedrock"
            print(f"\n{source} stopped the load: {error}\nThe vectors made so far are saved. Run the same command "
                  "again to carry on.")
            raise SystemExit(1) from error
        finally:
            print_spend(logged_before)
        print(f"Stored chunks for these strategies: "
              f"{sum(db.count_chunks(conn, strategy) for strategy in strategies):,}")


if __name__ == "__main__":
    main(sys.argv[1:])
