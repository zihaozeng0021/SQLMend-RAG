import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sqlmend_pipeline.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["enrich", *__import__("sys").argv[1:]]))
