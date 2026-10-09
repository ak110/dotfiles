"""親のCLI設定へ委譲先のenvを合成し、ファイル由来の設定をargvへ展開しない。"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import uuid

from agent_toolkit._common.state_paths import state_dir


class SettingsFile(str):
    """この起動が所有する合成設定のパス。SDKへは文字列として渡す。"""

    def cleanup(self) -> None:
        """他の起動の設定を触らず、自身のファイルを回収する。"""
        pathlib.Path(self).unlink(missing_ok=True)


def merge_settings(parent: str | None, env: dict[str, str], root: str | None) -> str:
    """親のJSONの意味を保ち、委譲先のenvを最後に適用する。

    ファイル由来は所有者だけが読める起動別ファイルにする。
    不読・不正・非objectの親は継承せず、envだけを渡す。
    """
    from_file = parent is not None and not parent.lstrip().startswith("{")
    try:
        raw = pathlib.Path(parent).read_text(encoding="utf-8") if from_file and parent is not None else parent or "{}"
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        value = {}
    settings = value if isinstance(value, dict) else {}
    inherited = settings.get("env")
    settings["env"] = {**(inherited if isinstance(inherited, dict) else {}), **env}
    content = json.dumps(settings, ensure_ascii=False)
    if not from_file:
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
