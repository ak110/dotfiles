"""Claude Code の設定ファイルを管理対象設定とマージするコマンド。

~/dotfiles/share/ 以下の managed JSON を対応する設定ファイルにマージする。
dict は再帰マージ、list は union マージ (順序維持・重複排除)、それ以外は上書き。

`claude_settings_json_managed.json` は OS 共通のベース設定のみを持ち、
OS 別の差分 (主にフック コマンドの shell/PowerShell ラッパー) は以下のオーバーライドで
上乗せする:

- POSIX (Linux/macOS/他 UNIX 系): share/claude_settings_json_managed.posix.json
- Windows: share/claude_settings_json_managed.win32.json

対象:
- share/claude_settings_json_managed.json (+ 現 OS のオーバーライド) → ~/.claude/settings.json
- share/claude_json_managed.json                                    → ~/.claude.json
"""

import copy
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_DOTFILES_DIR = Path.home() / "dotfiles"
_MANAGED_SETTINGS_PATH = _DOTFILES_DIR / "share" / "claude_settings_json_managed.json"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_MANAGED_CONFIG_PATH = _DOTFILES_DIR / "share" / "claude_json_managed.json"
_CONFIG_PATH = Path.home() / ".claude.json"


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    overrides = _platform_overrides(_MANAGED_SETTINGS_PATH)
    update_claude_settings(_MANAGED_SETTINGS_PATH, _SETTINGS_PATH, overrides=overrides)
    update_claude_settings(_MANAGED_CONFIG_PATH, _CONFIG_PATH)


def _platform_overrides(base_path: Path) -> list[Path]:
    """現プラットフォームに対応するオーバーライド JSON のパス一覧を返す。

    実在するファイルのみを返す。未対応 OS では空リスト。
    """
    suffix = "win32" if sys.platform == "win32" else "posix"
    override = base_path.with_suffix(f".{suffix}.json")
    return [override] if override.exists() else []


def update_claude_settings(
    managed_path: Path,
    settings_path: Path,
    overrides: list[Path] | None = None,
) -> None:
    """managed_path の設定を settings_path にマージして書き込む。

    overrides が与えられた場合は、managed_path の内容に上乗せしてからマージする。
    """
    managed = json.loads(managed_path.read_text(encoding="utf-8"))
    for override_path in overrides or []:
        _merge(managed, json.loads(override_path.read_text(encoding="utf-8")))

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
            data[key] = _union_list(data[key], value)
        else:
            data[key] = value


def _union_list(existing: list, managed: list) -> list:
    """順序維持・重複排除で 2 つの list を結合する。

    hashable な要素はそのまま集合判定に使い、dict/list などの非 hashable 要素は
    JSON 正規化文字列をキーにして重複判定する (hooks 配列のマージで必要)。
    """
    result: list = []
    seen: set = set()
    for item in existing + managed:
        try:
            key: object = ("h", item)
            hash(key)
        except TypeError:
            key = ("j", json.dumps(item, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
