"""Layer 0 check: embed one sentence with Titan V2. Must print 1024 and add one cost log row.

Run from datasheet-rag:  python scripts\\test_embed.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import config
from rag.embed import embed_text


def _rows() -> int:
    if not config.COST_LOG.exists():
        return 0
    return max(len(config.COST_LOG.read_text(encoding="utf-8").splitlines()) - 1, 0)


def main() -> None:
    before = _rows()
    vector = embed_text("The operating supply voltage range is 3.0 V to 3.6 V.")
    print(len(vector))
    print(f"cost log rows added: {_rows() - before} ({config.COST_LOG})")


# Guarded so that importing this file (pytest used to collect it by name) never calls Bedrock.
if __name__ == "__main__":
    main()
