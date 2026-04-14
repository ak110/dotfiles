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

加えて、配布元から削除された hook エントリ (JSON 内の command 文字列マッチ) を
settings.json から後追いで除去するクリーンアップも行う。union マージ方式の都合上、
share 側から消した hook がユーザー側に残り続けるのを防ぐため。

配布元から消えたファイル/ディレクトリの削除は汎用的な処理のため pytools._cleanup_paths と
pytools.post_apply に分離した (本モジュールは hook 内部の command 文字列マッチのみ担う)。
"""

import copy
import json
import logging
import sys
from pathlib import Path

from pytools import _log_format

logger = logging.getLogger(__name__)

_DOTFILES_DIR = Path.home() / "dotfiles"
_MANAGED_SETTINGS_PATH = _DOTFILES_DIR / "share" / "claude_settings_json_managed.json"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_MANAGED_CONFIG_PATH = _DOTFILES_DIR / "share" / "claude_json_managed.json"
_CONFIG_PATH = Path.home() / ".claude.json"

# settings.json の hooks 配下から除去したい command 部分文字列。
# 過去に share/claude_settings_json_managed.* で配布していたが廃止したエントリを書く。
# union マージは削除を反映しないため、ここで明示的に除去する。
_REMOVED_HOOK_COMMAND_SUBSTRINGS: tuple[str, ...] = (
    "claude_hook_call_formatter.py",
    # 2026-04: 統合フック (claude_hook_pretooluse.py) へ移行したため旧エントリを除去
    "claude_hook_check_mojibake.py",
    "claude_hook_check_ps1_eol.py",
)


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
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
    """managed_path の設定を settings_path にマージして書き込む。

    overrides が与えられた場合は、managed_path の内容に上乗せしてからマージする。
    マージ前に settings_path から removed_hook_substrings に該当する hook エントリを
    除去することで、配布元から消えた hook が残り続けるのを防ぐ。

    Returns:
        実際にファイルを書き換えたかどうか。
    """
    managed = json.loads(managed_path.read_text(encoding="utf-8"))
    for override_path in overrides or []:
        _merge(managed, json.loads(override_path.read_text(encoding="utf-8")))

    data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}

    original = copy.deepcopy(data)
    _strip_removed_hooks(data, removed_hook_substrings)
    _merge(data, managed)

    short = _log_format.home_short(settings_path)
    if data == original:
        logger.info(_log_format.format_status(short, "変更なし"))
        return False
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(_log_format.format_status(short, "更新しました"))
    for line in _diff_lines(original, data):
        logger.info(line)
    return True


def _diff_lines(before: dict, after: dict, path: str = "") -> list[str]:
    """2つの dict の差分を人間が読める行リストにして返す。

    dict は再帰的に差分を取り、list は件数差のサマリーを表示する。
    差分行は6スペースのインデントを持ち、basicConfig の2スペースと合わせて合計8スペースになる。
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


_MAX_VALUE_LEN = 60
_MAX_INLINE_DIFF = 3


def _list_diff_summary(before: list, after: list) -> str:
    """リストの件数差と追加・削除アイテムを文字列化する。

    全要素が文字列かつ差分が _MAX_INLINE_DIFF 件以下の場合のみ内容を表示し、それ以外は件数のみ。
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
    """値を短い文字列に変換する。dict/list はサマリー、その他は JSON 文字列（60文字上限）。"""
    if isinstance(value, dict):
        return f"{{...}} ({len(value)} keys)"
    if isinstance(value, list):
        return f"[...] ({len(value)} 件)"
    s = json.dumps(value, ensure_ascii=False)
    return s[:_MAX_VALUE_LEN] + "..." if len(s) > _MAX_VALUE_LEN else s


def _strip_removed_hooks(data: dict, substrings: tuple[str, ...]) -> None:
    """data["hooks"][event][*]["hooks"] から substrings を含む command を除去する。

    エントリが空になったら matcher エントリ自体も除去する。さらに event 配列ごと空に
    なったら event キーごと除去する。
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


#: マージ時に無視するトップレベルキー。
#: `$schema` は配布元 JSON の IDE 補完用メタ情報であり、ユーザー設定に伝播させない。
_IGNORED_KEYS: frozenset[str] = frozenset({"$schema"})


def _merge(data: dict, managed: dict) -> None:
    """Managed の設定を data に再帰的にマージする。

    dict は再帰マージ、list は union マージ (順序維持・重複排除)、
    それ以外は managed 側で上書き。`_IGNORED_KEYS` はマージ対象外。
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


if __name__ == "__main__":
    _main()
