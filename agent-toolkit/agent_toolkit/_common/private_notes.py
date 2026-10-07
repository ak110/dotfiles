"""WIと計画を保存するprivate-notesの位置の解決。

`atk config get private_notes`、`atk wi`、計画ファイルの配置、`atk serve`の計画ファイル画面とSSH先のヘルパーが
同じ順で位置を得るため、解決の順を本モジュールだけに置く。標準ライブラリと`platformdirs`だけを使う。
"""

from __future__ import annotations

import os
import pathlib

PRIVATE_NOTES_ENV = "AGENT_TOOLKIT_PRIVATE_NOTES"
"""private-notesの位置を指定する環境変数の名前。"""


def default_private_notes(home: pathlib.Path | None = None) -> pathlib.Path:
    """private-notesの位置を返す。

    `AGENT_TOOLKIT_PRIVATE_NOTES`が空でなければその値、未設定なら実在する`~/private-notes`、
    どちらも無ければ`platformdirs`の`user_data_dir`配下の`private-notes`を返す。位置の実在は確かめず、作成もしない。
    `home`を省略すると現在のユーザーのホームを使う。`appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    """
    override = os.environ.get(PRIVATE_NOTES_ENV)
    if override:
        return pathlib.Path(override).expanduser()
    default = (pathlib.Path.home() if home is None else home) / "private-notes"
    if default.exists():
        return default
    import platformdirs  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False)) / "private-notes"


def private_notes_override() -> str | None:
    """`AGENT_TOOLKIT_PRIVATE_NOTES`が空でなければその値を返す。"""
    return os.environ.get(PRIVATE_NOTES_ENV) or None
