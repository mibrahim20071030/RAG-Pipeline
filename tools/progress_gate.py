"""Claude Code Stop hook: keep progress.md up to date with what was done in the session.

A hook is only a command, so it cannot write the log itself. Instead it sends Claude back
to write it when files have changed and either:
  1. progress.md has no entry dated today, or
  2. a file was changed after progress.md was last saved (the entry is out of date).
It asks at most once per turn, and only looks at files git would commit (not runs/, .venv/).
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks

LOG = "progress.md"
SLACK_SECONDS = 2  # files saved in the same moment as the log count as covered


def changed_files() -> list[str]:
    """Changed, new or deleted files other than the log itself, relative to the repo root."""
    paths = []
    for line in checks.git("status", "--porcelain", "--untracked-files=all").splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path and path != LOG:
            paths.append(path)
    return paths


def has_entry(log_text: str, day: date) -> bool:
    return re.search(rf"^## {day.isoformat()}\b", log_text, re.MULTILINE) is not None


def changed_after_log(paths: list[str], log_path: Path) -> list[str]:
    logged_at = log_path.stat().st_mtime
    newer = []
    for rel in paths:
        path = checks.ROOT / rel
        if path.is_file() and path.stat().st_mtime > logged_at + SLACK_SECONDS:
            newer.append(rel)
    return newer


def what_is_missing(day: date) -> str | None:
    """The instruction to give Claude, or None when the log is up to date."""
    paths = changed_files()
    if not paths:
        return None
    log_path = checks.ROOT / LOG
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    if not has_entry(log_text, day):
        return (
            f"{LOG} has no entry for today. Add one at the top of the log, headed "
            f"'## {day.isoformat()} — Session N: <title>', covering what was done, decisions, "
            "problems hit (what, why, fix) and what is still open. Update the checklist in the same file."
        )
    newer = changed_after_log(paths, log_path)
    if newer:
        listed = ", ".join(newer[:5]) + (f" and {len(newer) - 5} more" if len(newer) > 5 else "")
        return (
            f"These files changed after {LOG} was last updated: {listed}. Update today's entry "
            f"('## {day.isoformat()} ...') and the checklist so the log covers this work. "
            "If the change is already described there, add nothing new and just re-save the file."
        )
    return None


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except ValueError:
        hook_input = {}
    if hook_input.get("stop_hook_active"):
        return  # already sent back once this turn; do not loop
    reason = what_is_missing(date.today())
    if reason:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
