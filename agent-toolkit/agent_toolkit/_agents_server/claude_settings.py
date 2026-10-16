"""親のCLI設定へ委譲先のenvを合成し、ファイル由来の設定をargvへ展開しない。"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import uuid

from agent_toolkit._common.state_paths import state_dir


class SettingsFile(str):
    """この起動が所有する合成設定のパス。SDKへは文字列として渡す。"""

    def cleanup(self) -> None:
        """他の起動の設定を触らず、自身のファイルを回収する。"""
        pathlib.Path(self).unlink(missing_ok=True)


def read_api_env_file(path: str) -> dict[str, str]:
    """限定形式のenvファイルを毎起動読み、変数展開をせず値を返す。"""
    values: dict[str, str] = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
            raise ValueError("API envファイルは1行1つのKEY=VALUEで指定する")
        value = value.strip()
        if value.startswith(("'", '"')) and (len(value) < 2 or value[-1] != value[0]):
            raise ValueError("API envファイルの値を囲む引用符を閉じる")
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        values[key] = value
    return values


def merge_settings(parent: str | None, env: dict[str, str], root: str | None, *, force_file: bool = False) -> str:
    """親のJSONの意味を保ち、委譲先のenvを最後に適用する。

    親の設定がファイルの場合と`force_file`の指定時は、所有者だけが読める起動ごとのファイルへ書き、そのパスを返す。
    親の設定を読めない場合、JSONとして不正な場合およびobjectでない場合は継承せず、envだけを渡す。
    """
    from_file = parent is not None and not parent.lstrip().startswith("{")
    try:
        raw = pathlib.Path(parent).read_text(encoding="utf-8") if from_file and parent is not None else parent or "{}"
        value = json.loads(raw)
    except OSError, ValueError, UnicodeError:
        value = {}
    settings = value if isinstance(value, dict) else {}
    inherited = settings.get("env")
    settings["env"] = {**(inherited if isinstance(inherited, dict) else {}), **env}
    content = json.dumps(settings, ensure_ascii=False)
    if not from_file and not force_file:
        return content
    directory = state_dir() / "agents-server" / (root or f"settings-{uuid.uuid4()}") / "claude-settings"
    directory.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
    except BaseException:
        pathlib.Path(path).unlink(missing_ok=True)
        raise
    return SettingsFile(path)


def cleanup_settings(options: object) -> None:
    """SDK設定が保持する、この起動自身の合成ファイルだけを回収する。"""
    settings = getattr(options, "settings", None)
    if isinstance(settings, SettingsFile):
        settings.cleanup()
