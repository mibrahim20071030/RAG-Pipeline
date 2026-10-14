"""The evaluation experiments: each changes one thing of the baseline setup.

An experiment is a dict with every key of BASELINE plus `name` and `group`. `run_eval` runs them by name,
by group or all together.
"""
BASELINE = {"strategy": "fixed_400", "mode": "hybrid", "ts_config": "english", "n": 30, "rerank": True, "k": 5,
            "detect_part": False, "hnsw": False, "generate": False}

_CHUNKING = ("page", "fixed_256", "fixed_256_overlap", "fixed_400_overlap", "structure_aware", "fixed_1024")

# name -> its group and what it changes in BASELINE. The generation group is added after the retrieval results.
EXPERIMENTS: dict[str, dict] = {
    "baseline": {"group": "baseline"},
    **{f"chunk_{strategy}": {"group": "chunking", "strategy": strategy} for strategy in _CHUNKING},
    "vector_only": {"group": "retrieval", "mode": "vector"},
    "keyword_only": {"group": "retrieval", "mode": "keyword"},
    "simple_hybrid": {"group": "keyword", "ts_config": "simple"},
    "simple_keyword_only": {"group": "keyword", "ts_config": "simple", "mode": "keyword"},
    "detect_part": {"group": "filter", "detect_part": True},
    "hnsw": {"group": "index", "hnsw": True, "mode": "vector"},
    "header_prefix": {"group": "prefix", "strategy": "fixed_400_prefix"},
    # Answers for the shortlist from the retrieval runs, and k 3 / 5 / 10 on the header prefix.
    "gen_baseline": {"group": "generation", "generate": True},
    "gen_fixed_1024": {"group": "generation", "generate": True, "strategy": "fixed_1024"},
    "gen_detect_part": {"group": "generation", "generate": True, "detect_part": True},
    **{f"gen_prefix_k{k}": {"group": "generation", "generate": True, "strategy": "fixed_400_prefix", "k": k}
       for k in (3, 5, 10)},
}


def experiment(name: str) -> dict:
    """The full setup of one experiment: BASELINE with its changes, plus `name` and `group`."""
    if name not in EXPERIMENTS:
        raise KeyError(f"unknown experiment {name!r}; known: {', '.join(EXPERIMENTS)}")
    return {**BASELINE, **EXPERIMENTS[name], "name": name}


def group(name: str) -> list[str]:
    """The experiment names in one group, in the order of EXPERIMENTS."""
    names = [key for key, changes in EXPERIMENTS.items() if changes["group"] == name]
    if not names:
        groups = sorted({changes["group"] for changes in EXPERIMENTS.values()})
        raise KeyError(f"no experiment in group {name!r}; groups: {', '.join(groups)}")
    return names
