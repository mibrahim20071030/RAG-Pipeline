"""Write the files for the Knowledge Base's S3 bucket into build/kb/ (Cycle 2 step 8a).

Run from datasheet-rag:  python scripts\\kb_prepare.py

Each PDF gets a sidecar `<file>.pdf.metadata.json` that the KB stores with every chunk of that file, so a passage
says which datasheet it came from and the datasheet dropdown can filter on it. The page number comes from the KB
itself (config.KB_PAGE_KEY). Nothing is uploaded: the human uploads build/kb/.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import config


def sidecar(document: str) -> dict:
    """The KB's metadata file for one PDF: the datasheet name and part."""
    return {"metadataAttributes": {
        "document": {"value": {"type": "STRING", "stringValue": document}},
        "part": {"value": {"type": "STRING", "stringValue": config.DOCUMENTS[document]["part"]}},
    }}


def prepare(out_dir: Path) -> int:
    """Fill out_dir with the PDFs and their sidecars, and return the number of PDFs."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for document in config.DOCUMENTS:
        pdf = config.PDF_DIR / f"{document}.pdf"
        if not pdf.exists():
            raise SystemExit(f"Missing {pdf}")
        shutil.copyfile(pdf, out_dir / pdf.name)
        meta = out_dir / f"{pdf.name}.metadata.json"
        meta.write_text(json.dumps(sidecar(document), indent=2), encoding="utf-8")
    return len(config.DOCUMENTS)


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    count = prepare(config.KB_DIR)
    size = sum(f.stat().st_size for f in config.KB_DIR.iterdir())
    print(f"Wrote {count} PDFs and {count} metadata files to {config.KB_DIR} ({size / 1e6:.1f} MB).")
    print("Upload the contents of that folder to the KB bucket.")


if __name__ == "__main__":
    main()
