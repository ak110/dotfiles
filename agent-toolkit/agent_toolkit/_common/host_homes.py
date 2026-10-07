"""Claude Codeの設定ディレクトリとCodexのホームの解決。

agent-toolkitの各処理（hook、`atk`、agents_server、`atk serve`のSSH先のヘルパー、スキル付属スクリプト）が
同じ規則で位置を得るため、環境変数名と規則を本モジュールだけに置く。標準ライブラリだけを使う。
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Mapping

CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
"""Claude Codeの設定ディレクトリを変える環境変数の名前。"""

CODEX_HOME_ENV = "CODEX_HOME"
"""Codexのホームを変える環境変数の名前。"""


def claude_config_dir(environ: Mapping[str, str] | None = None, *, home: pathlib.Path | None = None) -> pathlib.Path:
    """Claude Codeの設定ディレクトリを返す。

    `CLAUDE_CONFIG_DIR`が空でない絶対パスならその値、それ以外はホーム配下の`.claude`とする。
    Function hooks module（`agent-toolkit/hooks/session_exit.ts`）の`exitStatePath`も同じ規則で解決する。
    `environ`を省略すると現在のプロセスの環境変数を使い、`home`を省略すると現在のユーザーのホームを使う。
    """
    value = (os.environ if environ is None else environ).get(CLAUDE_CONFIG_DIR_ENV)
    if value and pathlib.Path(value).is_absolute():
        return pathlib.Path(value)
    return (pathlib.Path.home() if home is None else home) / ".claude"


def codex_home(environ: Mapping[str, str] | None = None) -> pathlib.Path:
    """Codexのホームを返す。`CODEX_HOME`が空でなければその値、空か未設定なら`~/.codex`とする。"""
    value = (os.environ if environ is None else environ).get(CODEX_HOME_ENV)
    if value:
        return pathlib.Path(value)
    return pathlib.Path.home() / ".codex"
