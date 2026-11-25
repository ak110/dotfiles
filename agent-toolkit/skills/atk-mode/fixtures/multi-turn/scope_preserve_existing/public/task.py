from csv import reader
from pathlib import Path


def import_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(reader(source))
