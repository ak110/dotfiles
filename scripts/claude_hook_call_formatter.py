#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code PostToolUse フック: 編集したファイルにフォーマッタを自動適用する。

Write/Edit/MultiEdit で編集されたファイルの拡張子に応じてフォーマッタを呼び出す。
現状 `.py` のみ対応し、`uv run --project ~/dotfiles pyfltr --commands=format
--exit-zero-even-if-formatted <file>` を ~/dotfiles を cwd にして実行する。

前提:
- `~/dotfiles` が clone 済みかつ `make setup` 実行済み (pytools が uv tool install
  済み、pyfltr が dotfiles venv に sync 済み)
- フックは dotfiles の pyfltr 設定 (preset=latest, --commands=format → ruff-format)
  を全ての編集対象 `.py` ファイルに適用する。他プロジェクト固有の pyfltr 設定を
  尊重しないのは意図通り (本リポジトリ内で正しく動けばよいという方針)

PostToolUse なので失敗しても編集は既に済んでおり、本スクリプトはログを残して
常に exit 0 する。

将来 `.ts` / `.tsx` 等の対応を足しやすくするため、拡張子→コマンドのマップ方式で
ディスパッチする。
"""

import json
import logging
import os
import pathlib
import subprocess
import sys

logger = logging.getLogger(__name__)

_DOTFILES_DIR = pathlib.Path.home() / "dotfiles"

# 拡張子 → フォーマッタ コマンド (file_path が最後尾に追加される)
_FORMATTERS: dict[str, list[str]] = {
    ".py": [
        "uv",
        "run",
        "--project",
        str(_DOTFILES_DIR),
        "pyfltr",
        "--commands=format",
        "--exit-zero-even-if-formatted",
    ],
}

_TARGET_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="INFO", stream=sys.stderr)

    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if payload.get("tool_name") not in _TARGET_TOOLS:
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not file_path_raw:
        sys.exit(0)

    file_path = pathlib.Path(file_path_raw)
    command = _FORMATTERS.get(file_path.suffix)
    if command is None:
        sys.exit(0)
    if not file_path.exists():
        # 編集前後で削除されたなどの異常系。フォーマッタを走らせても意味がない
        sys.exit(0)

    # pyfltr は cwd の pyproject.toml のみ参照し親階層へはウォークアップしないため、
    # dotfiles の設定を適用するため常に dotfiles を cwd にする
    # Claude Code のフック実行環境で VIRTUAL_ENV が設定されていると
    # `uv run --project` が警告を出すため除去する
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    try:
        subprocess.run([*command, str(file_path)], check=False, cwd=_DOTFILES_DIR, env=env)
    except (OSError, FileNotFoundError) as e:
        logger.warning("フォーマッタ起動に失敗しました: %s", e)

    sys.exit(0)


if __name__ == "__main__":
    _main()
