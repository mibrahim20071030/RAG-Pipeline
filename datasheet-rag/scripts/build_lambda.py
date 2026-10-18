"""Build the Lambda package: build/lambda.zip with handler.py, the query-path modules of rag/, and Linux wheels.

Run from datasheet-rag:  python scripts\\build_lambda.py

pip downloads the Linux (manylinux, x86_64, CPython 3.14) builds of psycopg-binary and boto3, not the Windows ones
in .venv. boto3 is packed too, so the version the tests ran with is the one Lambda runs. Nothing is uploaded: the
zip is put into the function by hand in the console.
"""
import ast
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
STAGE = BUILD / "lambda"
ZIP = BUILD / "lambda.zip"

PYTHON_VERSION = "3.14"  # the Lambda runtime python3.14
PLATFORMS = ("manylinux2014_x86_64", "manylinux_2_28_x86_64")  # the x86_64 runtime is Amazon Linux 2023, glibc 2.34
# Packed with the pins of requirements.txt, so Lambda runs the versions the tests ran with.
PACKAGES = ("psycopg[binary]", "boto3")
# The modules the query path imports (handler -> api -> pipeline -> search, rerank, generate). Not parse, chunk,
# load, server or eval: they need pdfplumber or the PDFs, and never run in Lambda.
RAG_MODULES = ("__init__", "api", "chunk_base", "config", "costlog", "db", "embed", "generate", "pipeline", "rerank",
               "search")
MAX_ZIP_MB = 50  # the console's direct upload limit
MAX_UNZIPPED_MB = 250  # Lambda's limit for a .zip package


def rag_imports(path: Path) -> set[str]:
    """The rag modules a file imports, read from its source."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "rag":
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("rag."):
            found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            found |= {alias.name.split(".")[1] for alias in node.names if alias.name.startswith("rag.")}
    return found


def check_modules() -> None:
    """Stop when a packed file imports a rag module that is not packed."""
    files = [ROOT / "handler.py"] + [ROOT / "rag" / f"{name}.py" for name in RAG_MODULES]
    missing = {(path.name, name) for path in files for name in rag_imports(path) if name not in RAG_MODULES}
    if missing:
        sys.exit(f"Packed files import rag modules that are not packed: {sorted(missing)}. Add them to RAG_MODULES.")


def pinned(requirements: Path = ROOT / "requirements.txt") -> list[str]:
    """The requirements.txt lines of PACKAGES, for example "boto3==1.43.111". Stops when one is missing or unpinned."""
    lines = {line.split("==")[0].strip(): line.strip() for line in requirements.read_text(encoding="utf-8").splitlines()
             if "==" in line and not line.lstrip().startswith("#")}
    missing = [name for name in PACKAGES if name not in lines]
    if missing:
        sys.exit(f"requirements.txt has no pinned line (name==version) for {missing}.")
    return [lines[name] for name in PACKAGES]


def size_mb(paths) -> float:
    return sum(path.stat().st_size for path in paths if path.is_file()) / 1_000_000


def main() -> None:
    check_modules()
    print("Packing:", ", ".join(pinned()))
    if BUILD.exists():
        shutil.rmtree(BUILD)
    STAGE.mkdir(parents=True)

    platforms = [arg for platform in PLATFORMS for arg in ("--platform", platform)]
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--target", str(STAGE), "--implementation", "cp", "--python-version", PYTHON_VERSION,
                    "--only-binary=:all:", *platforms, *pinned()], check=True)

    shutil.copy2(ROOT / "handler.py", STAGE / "handler.py")
    (STAGE / "rag").mkdir()
    for name in RAG_MODULES:
        shutil.copy2(ROOT / "rag" / f"{name}.py", STAGE / "rag" / f"{name}.py")

    files = sorted(path for path in STAGE.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(STAGE).as_posix())

    unzipped, zipped = size_mb(files), ZIP.stat().st_size / 1_000_000
    libpq = [path.name for path in files if path.name.startswith("libpq")]
    print(f"{ZIP}\n  {len(files)} files, {zipped:.1f} MB zipped, {unzipped:.1f} MB unzipped")
    print(f"  Linux libpq: {', '.join(libpq) or 'NOT FOUND'}")
    if not libpq:
        sys.exit("psycopg-binary's Linux library is missing from the package.")
    if zipped > MAX_ZIP_MB or unzipped > MAX_UNZIPPED_MB:
        sys.exit(f"Too large for a console upload ({MAX_ZIP_MB} MB zipped, {MAX_UNZIPPED_MB} MB unzipped).")


if __name__ == "__main__":
    main()
