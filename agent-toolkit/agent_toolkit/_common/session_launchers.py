"""agents_serverの状態ディレクトリの位置と、session登録簿が持つ委譲元sessionの読み取りを共有する。

agents_server（登録簿を書く側）と`atk serve`のセッション一覧（ローカルとリモートヘルパー、読む側）が読み込む。
リモートヘルパーはSSH先で標準ライブラリと`platformdirs`だけを前提に動くため、本モジュールは
`platformdirs`を状態ディレクトリの解決時に遅延importし、それ以外は標準ライブラリだけを使う。
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


def state_dir() -> pathlib.Path:
    """agent-toolkitの状態ファイル配置ディレクトリを返す。

    `appauthor=False`はWindowsでappnameが二重階層になる挙動を防ぐ。
    Linuxでは絶対パスの`XDG_STATE_HOME`だけを受理し、相対値は`HOME/.local/state`へ退避する。
    """
    import platformdirs  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    resolved = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if os.name != "nt" and xdg_state_home and not pathlib.Path(xdg_state_home).is_absolute():
        return pathlib.Path.home() / ".local" / "state" / "agent-toolkit"
    return resolved


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
