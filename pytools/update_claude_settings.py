"""Claude Code の設定ファイルを管理対象設定とマージするコマンド。

~/dotfiles/share/ 以下の managed JSON を対応する設定ファイルにマージする。
dict は再帰マージ、list は union マージ (順序維持・重複排除)、それ以外は上書き。

対象:
- share/claude_settings_managed.json → ~/.claude/settings.json
- share/claude_config_managed.json  → ~/.claude.json
"""

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DOTFILES_DIR = Path.home() / "dotfiles"
_MANAGED_SETTINGS_PATH = _DOTFILES_DIR / "share" / "claude_settings_managed.json"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_MANAGED_CONFIG_PATH = _DOTFILES_DIR / "share" / "claude_config_managed.json"
_CONFIG_PATH = Path.home() / ".claude.json"


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    update_claude_settings(_MANAGED_SETTINGS_PATH, _SETTINGS_PATH)
    update_claude_settings(_MANAGED_CONFIG_PATH, _CONFIG_PATH)


def update_claude_settings(managed_path: Path, settings_path: Path) -> None:
    """managed_path の設定を settings_path にマージして書き込む。"""
    managed = json.loads(managed_path.read_text(encoding="utf-8"))
    data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}

    original = copy.deepcopy(data)
    _merge(data, managed)

    if data == original:
        return
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("%s を更新しました。", settings_path)


def _merge(data: dict, managed: dict) -> None:
    """Managed の設定を data に再帰的にマージする。

    dict は再帰マージ、list は union マージ (順序維持・重複排除)、
    それ以外は managed 側で上書き。
    """
    for key, value in managed.items():
        if key in data and isinstance(data[key], dict) and isinstance(value, dict):
            _merge(data[key], value)
        elif key in data and isinstance(data[key], list) and isinstance(value, list):
            data[key] = list(dict.fromkeys(data[key] + value))
        else:
            data[key] = value
