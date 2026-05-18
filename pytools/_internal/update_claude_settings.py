"""Claude Code設定ファイルを管理対象設定とマージするコマンド。

`~/dotfiles/share/`配下のmanaged JSONを対応する設定ファイルへマージする。
OS別の差分（主にhookコマンドのshell/PowerShellラッパー）は
`*.posix.json`/`*.win32.json`のオーバーライドで上乗せする。
"""

import copy
import json
import logging
import sys
from pathlib import Path

from pytools._internal import log_format
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

_DOTFILES_DIR = Path.home() / "dotfiles"
_MANAGED_SETTINGS_PATH = _DOTFILES_DIR / "share" / "claude_settings_json_managed.json"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_MANAGED_CONFIG_PATH = _DOTFILES_DIR / "share" / "claude_json_managed.json"
_CONFIG_PATH = Path.home() / ".claude.json"

# settings.json の hooks 配下から除去する command 部分文字列。
# share/claude_settings_json_managed.* から廃止したエントリを列挙する。
# union マージは削除を反映しないため、ここで明示的に除去する。
_REMOVED_HOOK_COMMAND_SUBSTRINGS: tuple[str, ...] = (
    "claude_hook_call_formatter.py",
    # 2026-04: 統合フック (claude_hook_pretooluse.py) に統合したため旧エントリを除去
    "claude_hook_check_mojibake.py",
    "claude_hook_check_ps1_eol.py",
    # 2026-05: `uv run --script` を `uv run --no-project --script` に置き換えたため旧形式エントリを除去
    "uv run --script ~/dotfiles/scripts/claude_hook_pretooluse.py",
    "uv run --script ~/dotfiles/scripts/claude_hook_stop.py",
    "uv run --script $env:USERPROFILE\\dotfiles\\scripts\\claude_hook_pretooluse.py",
    "uv run --script $env:USERPROFILE\\dotfiles\\scripts\\claude_hook_stop.py",
)

_MAX_VALUE_LEN = 60
_MAX_INLINE_DIFF = 3

#: マージ時に無視するトップレベルキー。
#: `$schema` は配布元 JSON の IDE 補完用メタ情報であり、ユーザー設定に伝播させない。
_IGNORED_KEYS: frozenset[str] = frozenset({"$schema"})


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()


def run() -> bool:
    """Claude 設定ファイル 2 本をマージ更新する。

    Returns:
        いずれかのファイルを実際に書き換えたかどうか。呼び出し側がログ集計に使う。
    """
    overrides = _platform_overrides(_MANAGED_SETTINGS_PATH)
    changed_settings = update_claude_settings(_MANAGED_SETTINGS_PATH, _SETTINGS_PATH, overrides=overrides)
    changed_config = update_claude_settings(_MANAGED_CONFIG_PATH, _CONFIG_PATH)
    return changed_settings or changed_config


def _platform_overrides(base_path: Path, *, platform: str | None = None) -> list[Path]:
    """現プラットフォームに対応するオーバーライド JSON のパス一覧を返す。

    実在するファイルのみを返す。未対応 OS では空リスト。
    """
    plat = platform or sys.platform
    suffix = "win32" if plat == "win32" else "posix"
    override = base_path.with_suffix(f".{suffix}.json")
    return [override] if override.exists() else []


def update_claude_settings(
    managed_path: Path,
    settings_path: Path,
    overrides: list[Path] | None = None,
    removed_hook_substrings: tuple[str, ...] = _REMOVED_HOOK_COMMAND_SUBSTRINGS,
) -> bool:
    """`managed_path` の設定を `settings_path` にマージして書き込む。

    `overrides` が与えられた場合は、`managed_path` の内容に上乗せしてからマージする。
    マージ前に `settings_path` から `removed_hook_substrings` に該当するhookエントリを
    除去することで、配布元から削除されたhookが残り続けるのを防ぐ。

    Returns:
        実際にファイルを書き換えた場合True。
    """
    managed = json.loads(managed_path.read_text(encoding="utf-8"))
    for override_path in overrides or []:
        _merge(managed, json.loads(override_path.read_text(encoding="utf-8")))

    data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}

    original = copy.deepcopy(data)
    _strip_removed_hooks(data, removed_hook_substrings)
    _merge(data, managed)

    short = log_format.home_short(settings_path)
    if data == original:
        logger.info(log_format.format_status(short, "変更なし"))
        return False
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(log_format.format_status(short, "更新しました"))
    for line in _diff_lines(original, data):
        logger.info(line)
    return True


def _diff_lines(before: dict, after: dict, path: str = "") -> list[str]:
    """2つのdictの差分を行リストで返す。

    dictは再帰的に差分を取り、listは件数差のサマリーを表示する。
    差分行は6スペースのインデントを持ち、`basicConfig`の2スペースと合わせて合計8スペースになる。
    """
    lines = []
    for key in sorted(set(before) | set(after)):
        full_path = f"{path}.{key}" if path else key
        b_exists = key in before
        a_exists = key in after
        bv = before.get(key)
        av = after.get(key)
        if not b_exists:
            lines.append(f"      {full_path}: (新規) {_value_summary(av)}")
        elif not a_exists:
            lines.append(f"      {full_path}: {_value_summary(bv)} → (削除)")
        elif bv != av:
            if isinstance(bv, dict) and isinstance(av, dict):
                lines.extend(_diff_lines(bv, av, path=full_path))
            elif isinstance(bv, list) and isinstance(av, list):
                lines.append(f"      {full_path}: {_list_diff_summary(bv, av)}")
            else:
                lines.append(f"      {full_path}: {_value_summary(bv)} → {_value_summary(av)}")
    return lines


def _list_diff_summary(before: list, after: list) -> str:
    """リストの件数差と追加・削除アイテムを文字列化する。

    全要素が文字列かつ差分が`_MAX_INLINE_DIFF`件以下の場合のみ内容を表示し、それ以外は件数のみ。
    """
    summary = f"{len(before)} → {len(after)} 件"
    if all(isinstance(x, str) for x in before + after):
        b_set, a_set = set(before), set(after)
        added = [x for x in after if x not in b_set]
        removed = [x for x in before if x not in a_set]
        parts = []
        if 0 < len(added) <= _MAX_INLINE_DIFF:
            parts.append("+" + ", ".join(json.dumps(x, ensure_ascii=False) for x in added))
        if 0 < len(removed) <= _MAX_INLINE_DIFF:
            parts.append("-" + ", ".join(json.dumps(x, ensure_ascii=False) for x in removed))
        if parts:
            summary += " " + " ".join(parts)
    return summary


def _value_summary(value: object) -> str:
    """値を短い文字列に変換する。dict/listはサマリー、その他はJSON文字列（60文字上限）。"""
    if isinstance(value, dict):
        return f"{{...}} ({len(value)} keys)"
    if isinstance(value, list):
        return f"[...] ({len(value)} 件)"
    s = json.dumps(value, ensure_ascii=False)
    return s[:_MAX_VALUE_LEN] + "..." if len(s) > _MAX_VALUE_LEN else s


def _strip_removed_hooks(data: dict, substrings: tuple[str, ...]) -> None:
    """`data["hooks"][event][*]["hooks"]` から `substrings` を含むcommandを除去する。

    エントリが空になったらmatcherエントリ自体も除去する。さらにevent配列ごと空に
    なったらeventキーごと除去する。
    """
    if not substrings:
        return
    hooks_root = data.get("hooks")
    if not isinstance(hooks_root, dict):
        return
    for event_name in list(hooks_root.keys()):
        matchers = hooks_root.get(event_name)
        if not isinstance(matchers, list):
            continue
        kept_matchers: list = []
        for matcher in matchers:
            if not isinstance(matcher, dict):
                kept_matchers.append(matcher)
                continue
            inner_hooks = matcher.get("hooks")
            if not isinstance(inner_hooks, list):
                kept_matchers.append(matcher)
                continue
            kept_inner = [
                h
                for h in inner_hooks
                if not (
                    isinstance(h, dict) and isinstance(h.get("command"), str) and any(s in h["command"] for s in substrings)
                )
            ]
            if not kept_inner:
                continue
            matcher["hooks"] = kept_inner
            kept_matchers.append(matcher)
        if kept_matchers:
            hooks_root[event_name] = kept_matchers
        else:
            del hooks_root[event_name]


def _merge(data: dict, managed: dict) -> None:
    """managedの設定をdataに再帰的にマージする。

    dictは再帰マージ、listはunionマージ（順序維持・重複排除）、
    それ以外はmanaged側で上書き。`_IGNORED_KEYS` はマージ対象外。
    """
    for key, value in managed.items():
        if key in _IGNORED_KEYS:
            continue
        if key in data and isinstance(data[key], dict) and isinstance(value, dict):
            _merge(data[key], value)
        elif key in data and isinstance(data[key], list) and isinstance(value, list):
            data[key] = _union_list(data[key], value)
        else:
            data[key] = value


def _union_list(existing: list, managed: list) -> list:
    """順序維持・重複排除で2つのlistを結合する。

    hashableな要素はそのまま集合判定に使い、dict/listなどの非hashable要素は
    JSON正規化文字列をキーにして重複判定する（hooks配列のマージで必要）。
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


if __name__ == "__main__":
    _main()
