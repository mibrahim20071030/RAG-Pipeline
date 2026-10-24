"""Build the Lambda packages: build/lambda.zip (the API function) and build/ingest.zip (the ingest function, step 9a),
each with handler.py, the rag/ modules it needs, and Linux wheels.

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
INGEST_STAGE = BUILD / "ingest"
INGEST_ZIP = BUILD / "ingest.zip"

PYTHON_VERSION = "3.14"  # the Lambda runtime python3.14
PLATFORMS = ("manylinux2014_x86_64", "manylinux_2_28_x86_64")  # the x86_64 runtime is Amazon Linux 2023, glibc 2.34
# Packed with the pins of requirements.txt, so Lambda runs the versions the tests ran with.
PACKAGES = ("psycopg[binary]", "boto3")
# The modules the query path imports (handler -> api -> pipeline -> search, rerank, generate; api -> kb, uploads).
# Not parse, chunk, load, ingest, server or eval: they need pdfplumber or the PDFs.
RAG_MODULES = ("__init__", "api", "chunk_base", "config", "costlog", "db", "embed", "generate", "kb", "pipeline",
               "rerank", "search", "uploads", "usage")
# Imported inside handler.ingest_handler only, which runs in the ingest function, never in the API function.
LAZY_MODULES = ("ingest",)
# The ingest function (step 9a) also parses and chunks: pdfplumber and the Layer 1 to 3 modules.
INGEST_PACKAGES = (*PACKAGES, "pdfplumber")
INGEST_MODULES = (*RAG_MODULES, "chunk", "chunk_tables", "chunk_text", "ingest", "load", "parse")
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


def check_modules(modules=RAG_MODULES, allowed=LAZY_MODULES) -> None:
    """Stop when a packed file imports a rag module that is not packed (`allowed` names the lazy imports that run
    only in the other function)."""
    files = [ROOT / "handler.py"] + [ROOT / "rag" / f"{name}.py" for name in modules]
    missing = {(path.name, name) for path in files for name in rag_imports(path)
               if name not in modules and name not in allowed}
    if missing:
        sys.exit(f"Packed files import rag modules that are not packed: {sorted(missing)}. Add them to the list.")


def pinned(packages=PACKAGES, requirements: Path = ROOT / "requirements.txt") -> list[str]:
    """The requirements.txt lines of `packages`, for example "boto3==1.43.111". Stops when one is missing or
    unpinned."""
    lines = {line.split("==")[0].strip(): line.strip() for line in requirements.read_text(encoding="utf-8").splitlines()
             if "==" in line and not line.lstrip().startswith("#")}
    missing = [name for name in packages if name not in lines]
    if missing:
        sys.exit(f"requirements.txt has no pinned line (name==version) for {missing}.")
    return [lines[name] for name in packages]


def size_mb(paths) -> float:
    return sum(path.stat().st_size for path in paths if path.is_file()) / 1_000_000


def build(stage: Path, zip_path: Path, packages, modules) -> None:
    """Install the Linux wheels of `packages` and copy handler.py and `modules` into `stage`, then zip it."""
    print(f"Packing {zip_path.name}:", ", ".join(pinned(packages)))
    stage.mkdir(parents=True)
    platforms = [arg for platform in PLATFORMS for arg in ("--platform", platform)]
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--target", str(stage), "--implementation", "cp", "--python-version", PYTHON_VERSION,
                    "--only-binary=:all:", *platforms, *pinned(packages)], check=True)

    shutil.copy2(ROOT / "handler.py", stage / "handler.py")
    (stage / "rag").mkdir()
    for name in modules:
        shutil.copy2(ROOT / "rag" / f"{name}.py", stage / "rag" / f"{name}.py")

    files = sorted(path for path in stage.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(stage).as_posix())

    unzipped, zipped = size_mb(files), zip_path.stat().st_size / 1_000_000
    libpq = [path.name for path in files if path.name.startswith("libpq")]
    print(f"{zip_path}\n  {len(files)} files, {zip_path.stat().st_size:,} bytes ({zipped:.1f} MB) zipped, "
          f"{unzipped:.1f} MB unzipped")
    print(f"  Linux libpq: {', '.join(libpq) or 'NOT FOUND'}")
    if not libpq:
        sys.exit("psycopg-binary's Linux library is missing from the package.")
    if zipped > MAX_ZIP_MB or unzipped > MAX_UNZIPPED_MB:
        sys.exit(f"Too large for a console upload ({MAX_ZIP_MB} MB zipped, {MAX_UNZIPPED_MB} MB unzipped).")


def main() -> None:
    check_modules()
    check_modules(INGEST_MODULES, allowed=())
    if BUILD.exists():
        shutil.rmtree(BUILD)
    build(STAGE, ZIP, PACKAGES, RAG_MODULES)
    build(INGEST_STAGE, INGEST_ZIP, INGEST_PACKAGES, INGEST_MODULES)


if __name__ == "__main__":
    main()
