"""Claude Code の settings.json を管理対象設定とマージするコマンド。

~/dotfiles/share/claude_settings_managed.json の設定を ~/.claude/settings.json に
マージする。permissions.allow/deny は union マージ、それ以外は managed 側で上書き。
"""

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DOTFILES_DIR = Path.home() / "dotfiles"
_MANAGED_PATH = _DOTFILES_DIR / "share" / "claude_settings_managed.json"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    update_claude_settings(_MANAGED_PATH, _SETTINGS_PATH)


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
    """Managed の設定を data にマージする。"""
    managed_perms = managed.pop("permissions", None)

    # トップレベルキー: managed で上書き
    data.update(managed)

    # permissions: リストは union マージ、スカラーは上書き
    if managed_perms is not None:
        existing_perms = data.setdefault("permissions", {})
        for key, value in managed_perms.items():
            if isinstance(value, list):
                existing_list = existing_perms.get(key, [])
                existing_perms[key] = list(dict.fromkeys(existing_list + value))
            else:
                existing_perms[key] = value
