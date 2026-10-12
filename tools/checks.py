"""Checks that must pass before a commit: no secrets, lint, tests.

Used by .githooks/pre-commit (python tools/checks.py --staged) and by the Claude Code
Stop hook (tools/review_gate.py). Run it by hand from anywhere:

    datasheet-rag\\.venv\\Scripts\\python.exe tools\\checks.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "datasheet-rag"

# Files that must never be committed, whatever .gitignore says.
FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env(\..+)?|credentials|.*\.pem|.*\.pdf)$", re.IGNORECASE)
ALLOWED_NAMES = {".env.example"}
MAX_FILE_BYTES = 1_000_000

SECRET_PATTERNS = {
    "AWS access key ID": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    # [ \t]* and not \s*: an empty 'KEY=' followed by another line is not a secret.
    "AWS secret key line": re.compile(r"aws_secret_access_key[ \t]*[=:][ \t]*\S+", re.IGNORECASE),
    # Env or docker style (NAME=value, no spaces) or a quoted literal in code (NAME = "value").
    # A placeholder (<...>, $VAR, %VAR%, {var}) or a lookup such as os.environ.get(...) is not a match.
    "database password": re.compile(
        r"\b(?:PG_?PASSWORD|POSTGRES_PASSWORD)(?:=[\"']?[^\s\"'<>$%{]+|[ \t]*[=:][ \t]*[\"'][^\"'<>$%{]+[\"'])"
    ),
    "database URL with a password": re.compile(r"postgres(?:ql)?://[^\s:/@]+:[^\s@<>$%{]+@"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        # Never treat "git failed" as "no files to check".
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def files_to_check(staged_only: bool) -> list[str]:
    """Paths relative to the repo root: what is staged, or everything changed, deleted or new."""
    if staged_only:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    else:
        out = git("diff", "HEAD", "--name-only", "--diff-filter=ACMRD") + git("ls-files", "--others", "--exclude-standard")
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def read_content(rel: str, staged_only: bool) -> bytes | None:
    """What would be committed: the staged copy for a commit, else the file on disk."""
    if staged_only:
        result = subprocess.run(["git", "show", f":{rel}"], cwd=ROOT, capture_output=True, check=False)
        return result.stdout if result.returncode == 0 else None
    path = ROOT / rel
    return path.read_bytes() if path.is_file() else None


def check_secrets(paths: list[str], staged_only: bool = False) -> list[str]:
    problems = []
    for rel in paths:
        content = read_content(rel, staged_only)
        if content is None:  # deleted
            continue
        if FORBIDDEN_NAMES.search(rel) and Path(rel).name not in ALLOWED_NAMES:
            problems.append(f"{rel}: this kind of file must not be committed")
            continue
        if len(content) > MAX_FILE_BYTES:
            problems.append(f"{rel}: larger than {MAX_FILE_BYTES // 1000} KB")
            continue
        text = content.decode("utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{rel}: looks like it contains a secret ({label})")
    return problems


def run_step(name: str, command: list[str], cwd: Path) -> tuple[bool, str]:
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def run_checks(staged_only: bool = False) -> tuple[bool, str]:
    """Run every check. Returns (all passed, report text)."""
    report = []
    ok = True

    try:
        problems = check_secrets(files_to_check(staged_only), staged_only)
    except RuntimeError as error:
        problems = [f"could not list the files to scan ({error})"]
    report.append("secrets: " + ("ok" if not problems else "FAILED\n  " + "\n  ".join(problems)))
    ok &= not problems

    steps = [
        ("lint (ruff)", [sys.executable, "-m", "ruff", "check", "--quiet", "--output-format", "concise", "."], ROOT),
        ("tests (pytest)", [sys.executable, "-m", "pytest", "-q"], CODE_DIR),
    ]
    for name, command, cwd in steps:
        passed, output = run_step(name, command, cwd)
        last_line = output.splitlines()[-1] if output else ""
        report.append(f"{name}: " + (f"ok  {last_line}" if passed else f"FAILED\n{output}"))
        ok &= passed
    return ok, "\n".join(report)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ok, report = run_checks(staged_only="--staged" in sys.argv)
    print(report)
    if not ok:
        print("\nChecks failed. Fix the problems above, then try again.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
