"""`_tracked_subagent_types.py`のテスト。"""

from __future__ import annotations

import pytest
from _tracked_subagent_types import (
    SUBAGENT_TYPE_FLAGS,
    TRACKED_SUBAGENT_TYPES,
    is_explicit_review_purpose,
    is_review_purpose,
)


@pytest.mark.parametrize(
    "prompt",
    [
        "用途: 計画レビュー\n\n対象: /path/to/plan.md",
        "用途: 実装差分レビュー\n\n対象コミット: abc1234",
        "用途：計画レビュー",
        "先頭に説明文がある場合\n用途: 実装差分レビュー\n以降の本文",
    ],
)
def test_review_purpose_detected(prompt: str) -> None:
    """レビュー2用途の起動プロンプトを真と判定する。"""
    assert is_review_purpose(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "用途: 実装\n\n計画ファイル: /path/to/plan.md",
        "用途：実装",
        "説明文\n用途: 実装\n以降の本文",
    ],
)
def test_implementation_purpose_not_review(prompt: str) -> None:
    """実装用途の起動プロンプトを偽と判定する。"""
    assert is_review_purpose(prompt) is False


@pytest.mark.parametrize("prompt", ["", "用途の記述が無い本文", "目的: 実装"])
def test_missing_purpose_falls_back_to_review(prompt: str) -> None:
    """用途の記述が無い場合は記録側へ倒して真を返す。"""
    assert is_review_purpose(prompt) is True


@pytest.mark.parametrize(
    "purpose",
    [
        "用途: 計画レビュー",
        "用途: 実装差分レビュー",
        "用途：計画レビュー\n\n対象: /path/to/plan.md",
        "計画レビュー",
        "実装差分レビュー",
    ],
)
def test_explicit_review_purpose_detected(purpose: str) -> None:
    """レビュー2用途を明示する用途行と値単体を真と判定する。"""
    assert is_explicit_review_purpose(purpose) is True


@pytest.mark.parametrize(
    "purpose",
    [
        "用途: 実装",
        "実装",
    ],
)
def test_explicit_review_purpose_rejects_non_review(purpose: str) -> None:
    """実装の用途を偽と判定する。"""
    assert is_explicit_review_purpose(purpose) is False


@pytest.mark.parametrize("purpose", ["", "用途の記述が無い本文", "目的: 計画レビュー", "用途: レビュー", None, 1])
def test_explicit_review_purpose_rejects_unknown(purpose: object) -> None:
    """用途行が無い場合とレビュー2用途以外の値を偽と判定する。"""
    assert is_explicit_review_purpose(purpose) is False


def test_flag_map_covers_tracked_review_types() -> None:
    """フラグマップの全キーが追跡対象種別に含まれる。"""
    assert set(SUBAGENT_TYPE_FLAGS) <= TRACKED_SUBAGENT_TYPES
