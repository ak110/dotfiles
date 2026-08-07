"""Codex共有規範の除外規則。

実行時のフックと生成器の双方が参照する判定を、依存を持たない形で保持する。
生成器はプロジェクト環境の依存を持つため、隔離実行のフックから生成器を
importすると当該依存の解決に失敗する。判定だけを本モジュールへ置いて分離する。
"""

from __future__ import annotations

import pathlib

# Codexへ埋め込む共有規範から除外するルールファイルの名前。
CODEX_EXCLUDED_RULE_NAMES = frozenset({"99-claude-code.md"})


def is_codex_shared_rule(path: pathlib.Path | str) -> bool:
    """ルールファイルがCodexへ埋め込む共有規範ならTrueを返す。"""
    return pathlib.Path(path).name not in CODEX_EXCLUDED_RULE_NAMES
