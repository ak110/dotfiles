"""update_claude_settings試験の共通fixture・入力と補助関数。"""

import json
import shutil
from pathlib import Path

import pytest

from pytools._internal import common
from pytools._internal import update_claude_settings as mod
from pytools._internal._test_helpers import run_update_claude_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PROD_MANAGED_SETTINGS = _REPO_ROOT / "share" / "claude_settings_json_managed.json"

_PROD_MANAGED_CONFIG = _REPO_ROOT / "share" / "claude_json_managed.json"

_PROD_MANAGED_SETTINGS_FILES = (
    _PROD_MANAGED_SETTINGS,
    _PROD_MANAGED_SETTINGS.with_suffix(".posix.json"),
    _PROD_MANAGED_SETTINGS.with_suffix(".win32.json"),
)

_PROD_PRETOOLUSE_SCRIPT = _REPO_ROOT / "scripts" / "claude-hook-pretooluse.ps1"

_PROD_POSTTOOLUSE_SCRIPT = _REPO_ROOT / "scripts" / "claude-hook-posttooluse.ps1"

_LEGACY_WINDOWS_PRETOOLUSE_COMMAND = (
    'powershell -NoProfile -ExecutionPolicy Bypass -Command "& { uv run --no-project --script '
    "'__HOME__\\dotfiles\\scripts\\claude_hook.py' pretooluse; "
    'if ($LASTEXITCODE -eq 2) { exit 2 } else { exit 0 } }"'
)

_LEGACY_WINDOWS_AUTONOMOUS_EXIT_COMMAND = (
    'powershell -NoProfile -ExecutionPolicy Bypass -Command "& { uv run --no-project --script '
    "'__HOME__\\dotfiles\\scripts\\claude_hook.py' autonomous_exit; exit 0 }\""
)

MANAGED_ALLOW = [
    "Bash",
    "Edit",
    "Glob",
    "Grep",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
    "mcp__serena",
    "mcp__plugin_serena_serena",
]

MANAGED_DENY = ["Read(./secrets/**)"]

MANAGED = {
    "language": "japanese",
    "permissions": {
        "allow": MANAGED_ALLOW,
        "deny": MANAGED_DENY,
        "defaultMode": "plan",
    },
}


def _run(tmp_path: Path, managed: dict, existing: dict | None = None) -> dict:
    """update_claude_settings でマージしてターゲット結果を返す。"""
    return run_update_claude_settings(tmp_path, managed, existing)


def _use_managed_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, managed_settings_path: Path, managed_config_path: Path
) -> None:
    """管理設定2件を一時的な作業ツリーの`share/`へ複製し、`find_dotfiles_root()`をその作業ツリーへ向ける。"""
    root = tmp_path / "dotfiles-root"
    for source, relative in (
        (managed_settings_path, mod._MANAGED_SETTINGS_RELATIVE),  # pylint: disable=protected-access
        (managed_config_path, mod._MANAGED_CONFIG_RELATIVE),  # pylint: disable=protected-access
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)


def _setup_run_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_settings: dict,
    managed_config: dict | None = None,
) -> Path:
    """`run()` 経由テスト向けに管理設定2件と配置先2件のパスを差し替える。

    管理設定には`managed_settings`、管理configには`managed_config`（省略時は空 dict）を書き込む。
    `_SETTINGS_PATH`・`_CONFIG_PATH` は未作成のまま返す。
    """
    managed_settings_path = tmp_path / "managed_settings.json"
    managed_settings_path.write_text(json.dumps(managed_settings, ensure_ascii=False), encoding="utf-8")
    managed_config_path = tmp_path / "managed_config.json"
    managed_config_path.write_text(json.dumps(managed_config or {}), encoding="utf-8")
    settings_path = tmp_path / "settings.json"
    config_path = tmp_path / "claude.json"
    _use_managed_files(monkeypatch, tmp_path, managed_settings_path, managed_config_path)
    monkeypatch.setattr(mod, "_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(mod, "_CONFIG_PATH", config_path)
    return settings_path
