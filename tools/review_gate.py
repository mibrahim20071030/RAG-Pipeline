"""Claude Code Stop hook: run the checks when code changes, and ask for one code review per layer.

A "layer" here is all the work since the branch left main (committed or not). Rules:
  1. Code changed and the checks fail (tools/checks.py)  -> Claude is sent back to fix them.
  2. Checks pass and the layer has had no review yet     -> Claude is asked to review before the PR.
  3. Checks pass and the layer has been reviewed         -> nothing more. Fixes made after a review
     only get the checks. A second review is Claude's call (only when a fix rewrote logic
     substantially) and MAX_REVIEWS is the limit.

Claude records a finished review with:  python tools/review_gate.py --reviewed
Show the current state with:            python tools/review_gate.py --status
Markdown files do not count as code. State is kept in .claude/review_state.json (not in git).
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks

STATE_FILE = checks.ROOT / ".claude" / "review_state.json"
NOT_CODE = (".md",)
MAX_REVIEWS = 2

REVIEW_REQUEST = (
    "Code has changed, the checks pass (secrets, ruff, pytest), and this layer has not had its code review. "
    "If the layer is finished and about to go to the user for a PR, run the built-in code-review skill on it now, "
    "fix or report what it finds, then record the review with `python tools/review_gate.py --reviewed`. "
    "After fixing findings, re-run the checks only; do not review again unless a fix rewrote logic substantially, "
    f"and never more than {MAX_REVIEWS} reviews per layer. "
    "If the layer is not finished yet, say so and carry on or wait; you will be asked again when the code changes."
)


def layer_base() -> str:
    """The commit this layer started from: where the branch left main, else the current commit."""
    for ref in ("origin/main", "main"):
        try:
            return checks.git("merge-base", "HEAD", ref).strip()
        except RuntimeError:
            continue
    return checks.git("rev-parse", "HEAD").strip()


def fingerprint(base: str) -> str:
    """Hash of every code file changed since the layer base. Empty string when no code has changed."""
    changed = checks.git("diff", base, "--name-only", "--diff-filter=ACMRD")
    untracked = checks.git("ls-files", "--others", "--exclude-standard")
    paths = sorted({p.strip() for p in (changed + untracked).splitlines() if p.strip()})
    paths = [p for p in paths if not p.lower().endswith(NOT_CODE)]
    digest = hashlib.sha256()
    for rel in paths:
        path = checks.ROOT / rel
        digest.update(rel.encode())
        digest.update(path.read_bytes() if path.is_file() else b"")
    return digest.hexdigest() if paths else ""


def load_state(base: str) -> dict:
    """State for the current layer. A new base (main moved on after a merge) starts a new layer."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return state if state.get("base") == base else {"base": base, "reviews": 0}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def emit(payload: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload))


def record_review() -> int:
    base = layer_base()
    state = load_state(base)
    if state["reviews"] >= MAX_REVIEWS:
        print(f"This layer already has {MAX_REVIEWS} reviews recorded, which is the limit.")
        return 1
    state.update(reviews=state["reviews"] + 1, fingerprint=fingerprint(base), last_block="review")
    save_state(state)
    print(f"Recorded review {state['reviews']} of {MAX_REVIEWS} for this layer.")
    return 0


def stop_hook() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except ValueError:
        hook_input = {}
    # True when Claude is already continuing because this hook blocked it once this turn.
    retrying = bool(hook_input.get("stop_hook_active"))

    base = layer_base()
    state = load_state(base)
    current = fingerprint(base)
    if not current or current == state.get("fingerprint"):
        return

    ok, report = checks.run_checks()
    if not ok:
        if retrying and state.get("last_block") == "checks":
            # Already sent back once for this; do not loop. Tell the user instead.
            emit({"systemMessage": "Pre-commit checks are still failing:\n" + report})
            return
        save_state({**state, "last_block": "checks"})
        emit({"decision": "block", "reason": "Pre-commit checks failed. Fix them before finishing.\n" + report})
        return

    save_state({**state, "fingerprint": current, "last_block": "review"})
    if state["reviews"] == 0:
        emit({"decision": "block", "reason": REVIEW_REQUEST})


def main() -> int:
    if "--reviewed" in sys.argv:
        return record_review()
    if "--status" in sys.argv:
        state = load_state(layer_base())
        print(f"layer base {state['base'][:8]}: {state['reviews']} of {MAX_REVIEWS} reviews recorded")
        return 0
    stop_hook()
    return 0


if __name__ == "__main__":
    sys.exit(main())
