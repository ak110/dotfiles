#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""agent-toolkitドキュメントの文書サイズ上限（219行）を検査するpre-commitローカルhook。

対象ファイルは`.pre-commit-config.yaml`の`files:`正規表現で選定する。
例外ファイルは同ディレクトリの`check_doc_size_exceptions.yaml`に列挙する。
"""

from __future__ import annotations

import pathlib
import sys

import yaml

LIMIT = 219
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_EXCEPTIONS_FILE = _SCRIPT_DIR / "check_doc_size_exceptions.yaml"


def _load_exceptions() -> set[str]:
    if not _EXCEPTIONS_FILE.exists():
        return set()
    data = yaml.safe_load(_EXCEPTIONS_FILE.read_text(encoding="utf-8")) or {}
    return set(data.get("exempt", []))


def main(argv: list[str]) -> int:
    """対象ファイル群を219行以下か検査し、違反があれば1を返す。"""
    exceptions = _load_exceptions()
    violations: list[tuple[str, int]] = []
    for path_str in argv:
        if path_str in exceptions:
            continue
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
        print("agent-toolkitドキュメントサイズ上限（219行）違反:", file=sys.stderr)
        for path_str, line_count in violations:
            print(f"  {path_str}: {line_count}行（上限{LIMIT}行）", file=sys.stderr)
        print(
            "既存超過なら check_doc_size_exceptions.yaml へ理由と縮減計画とともに登録する。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
