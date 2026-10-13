"""Retrieval: vector search, keyword search, and the two merged with Reciprocal Rank Fusion.

A hit is a dict with `id`, `document`, `page`, `page_label`, `section`, `kind`, `text` and `score`. Every search
returns a list of hits, best first. The SQL is in `rag/db.py`.

An argument left as None takes its value from `rag/config.py` (or `embed.embed_text`) when the function is called,
so a setting changed after import, or an embedder replaced in a test, is the one that is used.
"""
from rag import config, db, embed

MODES = ("vector", "keyword", "hybrid")
_EDGE_PUNCTUATION = "?!.,;:()[]{}\"'"


def query_terms(question: str) -> list[str]:
    """The words of the question for the keyword search, in order, each once (case ignored, first spelling kept).

    Only punctuation at the ends of a word is removed, so `ESP32-WROOM-32`, `VDD_SDIO` and `3.3` stay whole.
    """
    terms, seen = [], set()
    for piece in question.split():
        term = piece.strip(_EDGE_PUNCTUATION)
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def rrf(ranked_lists: list[list[int]], c: int | None = None) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: (id, score) pairs, best first. score = sum over the lists of 1 / (c + rank).

    Rank starts at 1. An id repeated inside one list counts once, at its first rank. Ties are ordered by id.
    """
    c = config.RRF_C if c is None else c
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for chunk_id, rank in _ranks(ranked).items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (c + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _ranks(ids: list[int]) -> dict[int, int]:
    """id -> 1-based position in the list; a repeated id keeps its first position."""
    ranks: dict[int, int] = {}
    for rank, chunk_id in enumerate(ids, start=1):
        ranks.setdefault(chunk_id, rank)
    return ranks


def detect_part(question: str) -> str | None:
    """The document the question names, or None when it names none or more than one."""
    words = [term.lower() for term in query_terms(question)]
    named = {
        document
        for document, prefixes in config.PART_ALIASES.items()
        if any(word.startswith(prefix) for word in words for prefix in prefixes)
    }
    return named.pop() if len(named) == 1 else None


def uncached_questions(conn, questions: list[str]) -> list[str]:
    """The questions `question_vector` would have to pay for: those with no stored vector yet, each once."""
    keys = {embed.text_hash(question): question for question in questions}
    cached = db.cached_hashes(conn, list(keys), embed.embed_key())
    return [question for key, question in keys.items() if key not in cached]


def question_vector(conn, question: str, embed_fn=None) -> str:
    """The question's vector as pgvector text. Paid for once: it is kept in `embedding_cache` and read from there."""
    key, model = embed.text_hash(question), embed.embed_key()
    cached = db.cached_embeddings(conn, [key], model)
    if key in cached:
        return cached[key]
    vector = (embed_fn or embed.embed_text)(question)
    db.store_embeddings(conn, model, {key: vector})
    return db.vector_text(vector)


def _defaults(n: int | None, strategy: str | None) -> tuple[int, str]:
    return config.N_CANDIDATES if n is None else n, strategy or config.DEFAULT_STRATEGY


def vector_search(conn, question_vec, n: int | None = None, strategy: str | None = None,
                  document: str | None = None) -> list[dict]:
    """The n chunks nearest to the question vector. score = 1 - cosine distance."""
    n, strategy = _defaults(n, strategy)
    return db.nearest_chunks(conn, question_vec, strategy, n, document=document)


def keyword_search(conn, question: str, n: int | None = None, strategy: str | None = None,
                   ts_config: str | None = None, document: str | None = None) -> list[dict]:
    """The n chunks that best match any word of the question."""
    n, strategy = _defaults(n, strategy)
    ts_config = config.KEYWORD_CONFIG if ts_config is None else ts_config
    return db.keyword_chunks(conn, query_terms(question), strategy, n, ts_config=ts_config, document=document)


def hybrid_search(conn, question: str, question_vec, n: int | None = None, strategy: str | None = None,
                  ts_config: str | None = None, document: str | None = None, c: int | None = None) -> list[dict]:
    """Vector and keyword search merged with RRF: the top n hits in fused order.

    `score` is the RRF score. `vector_rank` and `keyword_rank` give the hit's place in each list, or None.
    """
    n, strategy = _defaults(n, strategy)
    vector_hits = vector_search(conn, question_vec, n, strategy, document)
    keyword_hits = keyword_search(conn, question, n, strategy, ts_config, document)
    vector_ids, keyword_ids = [hit["id"] for hit in vector_hits], [hit["id"] for hit in keyword_hits]
    vector_ranks, keyword_ranks = _ranks(vector_ids), _ranks(keyword_ids)
    by_id = {hit["id"]: hit for hit in keyword_hits} | {hit["id"]: hit for hit in vector_hits}
    fused = rrf([vector_ids, keyword_ids], c)
    return [
        {**by_id[chunk_id], "score": score, "vector_rank": vector_ranks.get(chunk_id),
         "keyword_rank": keyword_ranks.get(chunk_id)}
        for chunk_id, score in fused[:n]
    ]


def search(conn, question: str, mode: str = "hybrid", n: int | None = None, strategy: str | None = None,
           ts_config: str | None = None, document: str | None = None, embed_fn=None) -> list[dict]:
    """Search in one mode: "vector", "keyword" or "hybrid". The question is embedded only when the mode needs it.

    A question with no words gives an empty list in every mode, and is never sent to the embedding model.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not query_terms(question):
        return []
    if document is None and config.DETECT_PART:
        document = detect_part(question)
    if mode == "keyword":
        return keyword_search(conn, question, n, strategy, ts_config, document)
    question_vec = question_vector(conn, question, embed_fn)
    if mode == "vector":
        return vector_search(conn, question_vec, n, strategy, document)
    return hybrid_search(conn, question, question_vec, n, strategy, ts_config, document)
