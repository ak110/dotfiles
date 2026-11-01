"""CSVの1行を値の一覧へ分解するプラグイン。単独のコマンドとしても動く。"""

import sys


def parse(line: str) -> list[str]:
    """カンマ区切りの1行を分解する。"""
    return [cell.strip() for cell in line.split(",")]


if __name__ == "__main__":
    for raw in sys.stdin:
        print("|".join(parse(raw)))
