"""`pyproject.toml`の`プロジェクト規範の指定`の条件が、`dotfiles-repo-layout`の規範と同じ対象範囲を持つことを確かめる。

`atk run-script pick-wi-check`は設定の条件だけを消費するため、スキルが定める規範の範囲と設定が一致しないと、
スキルの手順で書くべき指定の欠落を選定時に検出しなくなる。設定の削除、条件の縮小、スキル側の範囲の変更を
このテストの失敗として検出する。設定と規範の双方を読むため、両者を包含するリポジトリ直下へ置く。
"""

from __future__ import annotations

import pathlib
import re
import tomllib
import typing

_ROOT = pathlib.Path(__file__).resolve().parent
_SKILL = _ROOT / ".claude" / "skills" / "dotfiles-repo-layout" / "SKILL.md"
# スキルの「変更後の規範の自セッション適用」が規範の範囲を列挙する文。
_TARGET_SENTENCE = re.compile(r"対象となる規範は、(?P<targets>.+?)である。")


def _conditions() -> list[dict[str, typing.Any]]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    conditions = data["tool"]["agent-toolkit"]["pick-wi-check"]["norm-spec"]
    assert isinstance(conditions, list)
    return conditions


def test_norm_target_condition_matches_skill_scope() -> None:
    """対象ファイルのパスを求める条件の範囲が、スキルの列挙する規範の範囲と一致する。"""
    text = _SKILL.read_text(encoding="utf-8")
    matched = _TARGET_SENTENCE.search(text)
    assert matched is not None, f"{_SKILL}に規範の範囲を列挙する文がありません"
    scope = set(re.findall(r"`([^`]+)`", matched["targets"]))
    paths = [set(condition.get("paths", [])) for condition in _conditions() if condition.get("require-paths") is True]
    assert paths == [scope]


def test_read_request_condition_matches_skill_request() -> None:
    """エージェント向け文書の条件が、スキルの求める読む要求の文字列を本文に持つ。"""
    text = _SKILL.read_text(encoding="utf-8")
    requests = [condition for condition in _conditions() if condition.get("agent-doc") is True]
    assert len(requests) == 1
    required = requests[0].get("require-text")
    assert isinstance(required, list) and required
    assert all(isinstance(item, str) and item in text for item in required)
