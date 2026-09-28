"""Codex共有規範の除外規則。

実行時のフックと生成器の双方が参照する判定を、依存を持たない形で保持する。
生成器はプロジェクト環境の依存関係を必要とするため、隔離実行のフックからimportすると
その依存関係を解決できない。判定だけを本モジュールへ置いて分離する。
"""

from __future__ import annotations

import pathlib

# Codexへ埋め込む共有規範から除外するルールファイルの名前。
CODEX_EXCLUDED_RULE_NAMES: frozenset[str] = frozenset()


def is_codex_shared_rule(path: pathlib.Path | str) -> bool:
    """ルールファイルがCodexへ埋め込む共有規範ならTrueを返す。"""
    return pathlib.Path(path).name not in CODEX_EXCLUDED_RULE_NAMES
