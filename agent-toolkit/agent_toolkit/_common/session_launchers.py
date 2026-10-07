"""agents_serverのsession登録簿の位置と、登録簿が持つ委譲元sessionの読み取りを共有する。

agents_server（登録簿を書く側）と`atk serve`のセッション一覧（ローカルとリモートヘルパー、読む側）が読み込む。
リモートヘルパーはSSH先で標準ライブラリと`platformdirs`だけを前提に動くため、本モジュールは標準ライブラリだけを使う。
状態ディレクトリ自体の解決は`agent_toolkit._common.state_paths`が持つ。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import typing

LAUNCHER_KEY = "launcher_session_id"
"""sessionを作成した時点の委譲元sessionの識別子を保持する、登録簿のレコードの項目名。"""

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


def registry_directory(state_root: pathlib.Path) -> pathlib.Path:
    """状態ディレクトリ配下のsession登録簿のディレクトリを返す。"""
    return state_root / "agents-server" / "sessions"


def valid_launcher(value: object) -> str | None:
    """委譲元sessionの識別子として妥当な値だけを返す。"""
    return value if isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) else None


def read_launcher(path: pathlib.Path) -> str | None:
    """登録簿のレコード1件が持つ委譲元sessionの識別子を返す。読めない場合と項目を持たない場合は`None`を返す。"""
    try:
        payload: typing.Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return valid_launcher(payload.get(LAUNCHER_KEY)) if isinstance(payload, dict) else None


def launcher_reader(state_root: pathlib.Path | None) -> typing.Callable[[str], str | None]:
    """session識別子から登録簿の委譲元を引く関数を返す。

    登録簿のディレクトリを1回だけ列挙し、レコードを持つsessionだけを読む。
    状態ディレクトリを解決できない場合と登録簿が無い場合は、常に`None`を返す関数を返す。
    """
    if state_root is None:
        return lambda _session_id: None
    directory = registry_directory(state_root)
    try:
        names = {entry.name for entry in os.scandir(directory)}
    except OSError:
        return lambda _session_id: None

    def read(session_id: str) -> str | None:
        name = f"{session_id}.json"
        if not _SESSION_ID_PATTERN.fullmatch(session_id) or name not in names:
            return None
        return read_launcher(directory / name)

    return read
