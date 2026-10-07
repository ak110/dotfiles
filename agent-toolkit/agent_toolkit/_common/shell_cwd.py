"""Bashコマンドの`cd`・`pushd`による現在ディレクトリの変化を静的に追跡する処理。"""

from __future__ import annotations

import dataclasses
import os
import re

from agent_toolkit._common.shell_segments import skip_env_assignments

_PUSHD_STACK_ROTATION_PATTERN = re.compile(r"^[+-]\d+$")


@dataclasses.dataclass(frozen=True)
class CwdResolution:
    """シェルコマンドから得たcwdの解決結果を表す。"""

    path: str
    resolved: bool
    unresolved_expression: str | None = None


def resolve_cwd_change(tokens: list[str], current_cwd: CwdResolution) -> CwdResolution | None:
    """cwdを変更するセグメントの解決結果を返す。"""
    start = skip_env_assignments(tokens, 0)
    if start >= len(tokens):
        return None
    if tokens[start] in ("cd", "pushd"):
        return _apply_cd(tokens, start, current_cwd)
    if tokens[start] == "popd":
        return CwdResolution("", False)
    return None


def _apply_cd(tokens: list[str], start: int, current_cwd: CwdResolution) -> CwdResolution:
    """`cd`・`pushd`の引数を解釈して新しいcwdの解決結果を返す。

    引数なし・オプション（`-`等）・シェル展開を含む場合は解決不能とする。
    相対パスは解決済みの現在cwdを基点に`os.path.normpath`で正規化する。
    """
    if start + 1 >= len(tokens):
        return CwdResolution("", False)
    arguments = tokens[start + 1 :]
    terminators = [index for index, argument in enumerate(arguments) if argument == "--"]
    first_terminator = terminators[0] if terminators else len(arguments)
    option_arguments = arguments[:first_terminator]
    if tokens[start] == "pushd":
        if "-n" in option_arguments:
            return current_cwd
        if option_arguments and _PUSHD_STACK_ROTATION_PATTERN.fullmatch(option_arguments[0]):
            return CwdResolution("", False)
    if len(terminators) > 1:
        return CwdResolution("", False)
    if terminators:
        target_index = terminators[0] + 1
        if target_index >= len(arguments):
            return CwdResolution("", False)
        target = arguments[target_index]
    else:
        target = arguments[0]
    if not target or target.startswith("-"):
        return CwdResolution("", False)
    return _normalize_relative(target, current_cwd)


# 引用符・エスケープで保護されたリテラルなメタ文字を解決済みとして救済する試みは、
# `--`終端・`pushd`オプション以外の全シェル字句規則の再実装を要する。
# バックスラッシュ・部分引用・`git -C`引数等で継続的に穴が生じた実績があり、費用対効果に見合わない。
# メタ文字を含む対象は常に解決不能とし、安全側（過剰block/warn）で運用する。
def _contains_shell_expansion(value: str) -> bool:
    """静的解析で解決できないシェル展開の記号を含むか判定する。"""
    return any(marker in value for marker in ("$", "`", "~", "*", "?", "[", "{"))


def _normalize_relative(target: str, current_cwd: CwdResolution) -> CwdResolution:
    """相対パスを現在cwd基点で正規化し、解決結果を返す。"""
    if _contains_shell_expansion(target):
        return CwdResolution("", False, target)
    if os.path.isabs(target):
        return CwdResolution(os.path.normpath(target), True)
    if not current_cwd.resolved:
        return CwdResolution("", False, current_cwd.unresolved_expression)
    return CwdResolution(os.path.normpath(os.path.join(current_cwd.path, target)), True)
