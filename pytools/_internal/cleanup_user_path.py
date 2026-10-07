"""ユーザー側 PATH を整理する（Windows のみ）。

WindowsのPATHはシステム（HKLM）側とユーザー（HKCU）側に分かれて格納される。
本ステップでは以下3点を担う:

- フルパスを `%LOCALAPPDATA%` / `%APPDATA%` / `%USERPROFILE%` プレースホルダーへ置換する
- システム側 PATH と重複するエントリーをユーザー側から除外する
- ユーザー側 PATH 内で重複するエントリーを除外する
- 存在しないディレクトリを指すエントリーを検出した際に警告ログを出力する（自動削除はしない）
"""

import logging
import ntpath
import os
from collections.abc import Iterable, Mapping
from pathlib import Path, PureWindowsPath

from pytools._internal import log_format, post_apply_outcome, winutils

logger = logging.getLogger(__name__)

_PATH_SEPARATOR = ";"
_LOG_LABEL = "PATH 整理"

# winreg.REG_SZ / REG_EXPAND_SZ の値。Linux で winreg をインポートできないため値を埋め込む。
_REG_SZ = 1
_REG_EXPAND_SZ = 2

# 最長一致優先で評価する環境変数名。長いパス (LOCALAPPDATA / APPDATA) を
# USERPROFILE より先に評価し、前方一致が成立した時点で確定する順序とする。
_PLACEHOLDER_VAR_ORDER: tuple[str, ...] = ("LOCALAPPDATA", "APPDATA", "USERPROFILE")


def run() -> post_apply_outcome.PostApplyOutcome:
    """ユーザー側 PATH を整理する。

    プレースホルダー化・システム側およびユーザー側の重複除外・存在チェック警告を実施する。
    存在しないエントリーは警告ログを出力するのみで自動削除はしない。
    PATH の読み書きの失敗は失敗と数える。
    """
    user_value, reg_type = winutils.read_user_env_var("Path")
    if not user_value:
        return post_apply_outcome.PostApplyOutcome()
    try:
        system_value, _ = winutils.read_system_env_var("Path")
    except OSError as e:
        return post_apply_outcome.PostApplyOutcome(failure=f"システム側 PATH の読み込みに失敗: {e}")

    env_map = _collect_userprofile_env(os.environ)

    # (1) プレースホルダー化
    original_entries = _split(user_value)
    replacements: list[tuple[str, str]] = []
    placeholder_entries: list[str] = []
    for entry in original_entries:
        new_entry = _replace_placeholders(entry, env_map)
        placeholder_entries.append(new_entry)
        if new_entry != entry:
            replacements.append((entry, new_entry))

    # (2) システム側との重複除外
    new_value, removed = _filter_user_path(_PATH_SEPARATOR.join(placeholder_entries), system_value or "")

    # (3) 存在チェック警告
    missing, unresolved = _find_missing_paths(_split(new_value))
    for original, expanded in missing:
        logger.warning(
            log_format.format_status(
                _LOG_LABEL,
                f"ユーザー PATH に存在しないエントリーを検出: {original} (展開: {expanded})",
            )
        )
    for original, expanded, error in unresolved:
        logger.warning(
            log_format.format_status(
                _LOG_LABEL,
                f"ユーザー PATH のエントリーを検査できないため保持: {original} (展開: {expanded}): {error}",
            )
        )

    # (4) 値型判定: 書き戻し値に % を含み、かつ既存が REG_SZ なら REG_EXPAND_SZ へ昇格する。
    needs_type_promotion = reg_type == _REG_SZ and "%" in new_value
    new_reg_type = _REG_EXPAND_SZ if needs_type_promotion else reg_type

    # (5) 書き戻し
    if not (replacements or removed or needs_type_promotion):
        return post_apply_outcome.PostApplyOutcome()
    try:
        winutils.write_user_env_var("Path", new_value, new_reg_type)
    except OSError as e:
        return post_apply_outcome.PostApplyOutcome(failure=f"ユーザー PATH の書き込みに失敗: {e}")
    for original, new_entry in replacements:
        logger.info(log_format.format_status(_LOG_LABEL, f"ユーザー PATH を整理: {original} → {new_entry}"))
    for entry in removed:
        logger.info(log_format.format_status(_LOG_LABEL, f"ユーザー PATH から除外: {entry}"))
    winutils.broadcast_environment_change()
    return post_apply_outcome.PostApplyOutcome(changed=True)


def _collect_userprofile_env(environ: Mapping[str, str]) -> dict[str, str]:
    """プレースホルダー化に使う環境変数を最長一致順で収集する。

    `_PLACEHOLDER_VAR_ORDER` の順で `environ` を引き、空値（未定義含む）は除外した
    `"%NAME%" -> 展開値` の順序付き辞書を返す。
    """
    result: dict[str, str] = {}
    for name in _PLACEHOLDER_VAR_ORDER:
        value = environ.get(name, "")
        if value:
            result[f"%{name}%"] = value
    return result


def _replace_placeholders(entry: str, env_map: Mapping[str, str]) -> str:
    r"""前方一致でユーザー系プレースホルダーへ置換する。

    元エントリーを `ntpath.expandvars` で展開した結果を `PureWindowsPath.parts` で分解し、
    `env_map` の各環境変数展開値の `parts` と小文字化キーで先頭一致するか判定する。
    最初にマッチしたプレースホルダーで置換した文字列を返す。マッチしなければ元エントリーを返す。

    マッチ後の残部成分は元の大文字小文字を保持し `\\` 区切りで連結する。
    """
    expanded = ntpath.expandvars(entry)
    if not expanded:
        return entry
    entry_parts = PureWindowsPath(expanded).parts
    if not entry_parts:
        return entry
    entry_keys = tuple(part.lower() for part in entry_parts)
    for placeholder, env_value in env_map.items():
        env_parts = PureWindowsPath(env_value).parts
        if not env_parts:
            continue
        env_keys = tuple(part.lower() for part in env_parts)
        if len(env_keys) > len(entry_keys):
            continue
        if entry_keys[: len(env_keys)] != env_keys:
            continue
        remainder = entry_parts[len(env_keys) :]
        if not remainder:
            return placeholder
        return placeholder + "\\" + "\\".join(remainder)
    return entry


def _filter_user_path(user_value: str, system_value: str) -> tuple[str, list[str]]:
    """ユーザー側PATHからシステム側またはユーザー側で重複するエントリーを除く。

    比較は各エントリーを `ntpath.expandvars` で環境変数展開し `PureWindowsPath`
    で表記正規化したキーで行う。ユーザー側の重複は先頭を残す。残すエントリーは
    元の生値（プレースホルダーを含む）のまま保持する。

    Returns:
        `(整理後のPATH文字列, 削除した元エントリー一覧)` のタプル。
    """
    system_keys = {key for key in (winutils.path_key(entry) for entry in _split(system_value)) if key}
    seen_user_keys: set[str] = set()
    kept: list[str] = []
    removed: list[str] = []
    for entry in _split(user_value):
        key = winutils.path_key(entry)
        if key and (key in system_keys or key in seen_user_keys):
            removed.append(entry)
            continue
        if key:
            seen_user_keys.add(key)
        kept.append(entry)
    return _PATH_SEPARATOR.join(kept), removed


def _find_missing_paths(
    entries: Iterable[str],
) -> tuple[list[tuple[str, str]], list[tuple[str, str, OSError]]]:
    """存在しないエントリーと検査できないエントリーを返す。

    展開後にも `%` が残るエントリー（未定義変数残留）は判定不能として除外する。
    判定は `pathlib.Path(p).exists()` で行う。
    """
    missing: list[tuple[str, str]] = []
    unresolved: list[tuple[str, str, OSError]] = []
    for entry in entries:
        expanded = ntpath.expandvars(entry)
        if not expanded or "%" in expanded:
            continue
        try:
            exists = Path(expanded).exists()
        except OSError as error:
            unresolved.append((entry, expanded, error))
            continue
        if not exists:
            missing.append((entry, expanded))
    return missing, unresolved


def _split(path_value: str) -> list[str]:
    """`;` 区切りの PATH 文字列をエントリー配列に分解する。空エントリーは除く。"""
    return [entry for entry in path_value.split(_PATH_SEPARATOR) if entry]
