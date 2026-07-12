#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# ///
"""agent-toolkitドキュメントの文書サイズ上限（220行）を検査するpre-commitローカルhook。

対象ファイルは`.pre-commit-config.yaml`の`files:`正規表現で選定する。
"""

from __future__ import annotations

import pathlib
import sys

LIMIT = 220


def main(argv: list[str]) -> int:
    """対象ファイル群を220行以下か検査し、違反があれば1を返す。"""
    violations: list[tuple[str, int]] = []
    for path_str in argv:
        path = pathlib.Path(path_str)
        try:
            with path.open("r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
        except OSError as exc:
            print(f"{path_str}: 読み込み失敗: {exc}", file=sys.stderr)
            return 1
        if line_count > LIMIT:
            violations.append((path_str, line_count))
    if violations:
        print("agent-toolkitドキュメントサイズ上限（220行）違反:", file=sys.stderr)
        for path_str, line_count in violations:
            print(f"  {path_str}: {line_count}行（上限{LIMIT}行）", file=sys.stderr)
        print(
            "該当ファイルを220行以下へ縮減してください。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
