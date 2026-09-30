"""Claude CodeのBashツールが背景実行へ移したコマンドの出力ファイルを、ツール結果の通知文から取り出す。

通知文の例: `Command running in background with ID: <id>. Output is being written to: <path>. You will be notified ...`
フックと`agents_server`の双方がこの形式を読むため、解析を本モジュールへ集約する。
"""

from __future__ import annotations

import pathlib
import re

_OUTPUT_PATH_RE = re.compile(r"Output is being written to:\s*(/\S+)", re.IGNORECASE)


def output_paths(text: str) -> list[str]:
    """通知文が示す出力ファイルの絶対パスを出現順に返す。

    通知文はパスの後に文を続けるため、パス末尾の句読点を除く。絶対パスでない値は返さない。
    """
    paths: list[str] = []
    for match in _OUTPUT_PATH_RE.finditer(text):
        path = match.group(1).rstrip(".,;:)")
        if pathlib.PurePath(path).is_absolute():
            paths.append(path)
    return paths
