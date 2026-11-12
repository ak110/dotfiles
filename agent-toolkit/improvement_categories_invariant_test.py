"""改善点の区分を定義する文書と送達・Stopの検査の一致を確かめる。"""

import ast
import pathlib
import re

import pytest

from agent_toolkit._testing.normative_contract import read_contract

_ROOT = pathlib.Path(__file__).parent


def _bullet_categories(text: str) -> set[str]:
    return set(re.findall(r"^\s*- `([^`]+)`: ", text, re.MULTILINE))


def _status_categories(text: str, status: str) -> set[str]:
    bullet = re.search(rf"^- `{re.escape(status)} [^`]+`:(.*?)(?=^- |\Z)", text, re.MULTILINE | re.DOTALL)
    assert bullet is not None
    return set(re.findall(r"`([^`]+)`", bullet[1].split("区分は", 1)[1].splitlines()[0]))


def _sender_categories(text: str) -> dict[str, set[str]]:
    definitions = re.findall(r'value: "([^"]+)",\s*categories: (\[[^\]]+\])', text)
    return {status: set(ast.literal_eval(values)) for status, values in definitions}


def _assert_categories_match(sender: str, rules: str, prevention: str, waiting: str) -> None:
    checks = _sender_categories(sender)
    assert set(checks) == {"不要", "予定"}
    assert checks["不要"] == _status_categories(rules, "不要") == _bullet_categories(prevention)
    assert checks["予定"] == _status_categories(rules, "予定") == _bullet_categories(waiting)
    advisor = (_ROOT / "agent_toolkit/_hooks/improvement_status_advisor.py").read_text(encoding="utf-8")
    categories = next(
        node.value
        for node in ast.walk(ast.parse(advisor))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_UNNEEDED_CATEGORIES" for target in node.targets)
    )
    assert checks["不要"] == set(ast.literal_eval(categories))


@pytest.fixture(name="definitions", scope="module")
def _definitions() -> tuple[str, str, str, str]:
    sender = (_ROOT / "hooks/send_to_user.tsx").read_text(encoding="utf-8")
    rules = (_ROOT / "share/rules/rules-main.md").read_text(encoding="utf-8")
    prevention = read_contract(
        _ROOT.parent / "agent-basics/skills/bugfix/references/root-cause-prevention.md",
        _ROOT,
        ("root-cause-prevention-requirements.md", "再発防止策の必須性"),
    )
    # 他節の箇条書きを区分の定義へ混ぜない。
    prevention = prevention.split("理由を次の区分名で始める。", 1)[1].split("過去に採用して", 1)[0]
    waiting = read_contract(
        _ROOT / "skills/delegation/references/waiting-and-monitoring.md",
        _ROOT,
        ("waiting-and-monitoring-independent-work.md", "待機中の先行実施"),
    )
    waiting = waiting.split("## 待機中の先行実施", 1)[1]
    return sender, rules, prevention, waiting


def test_improvement_category_definitions_agree(definitions: tuple[str, str, str, str]) -> None:
    """5つの定義元から独立に抽出し、片側だけの区分改名を検出する。"""
    _assert_categories_match(*definitions)


@pytest.mark.parametrize("index", range(4))
def test_category_rename_is_detected(definitions: tuple[str, str, str, str], index: int) -> None:
    """検査自体が同じ名前の写しに固定されていないことを持ち込みで確かめる。"""
    modified = list(definitions)
    old = "認可待ち" if index == 3 else "非欠陥"
    assert old in modified[index]
    modified[index] = modified[index].replace(old, "検査用の改名")
    with pytest.raises(AssertionError):
        _assert_categories_match(*modified)
