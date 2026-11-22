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
    definitions = re.findall(r'statusCheck\(\s*"([^"]+)",\s*(\[[^\]]+\])', text)
    return {status: set(ast.literal_eval(values)) for status, values in definitions}


def _assert_categories_match(sender: str, rules: str, prevention: str) -> None:
    checks = _sender_categories(sender)
    assert set(checks) == {"不要", "予定"}
    assert checks["不要"] == _status_categories(rules, "不要") == _bullet_categories(prevention)
    assert checks["予定"] == _status_categories(rules, "予定")
    advisor = (_ROOT / "agent_toolkit/_hooks/improvement_status_advisor.py").read_text(encoding="utf-8")
    categories = next(
        node.value
        for node in ast.walk(ast.parse(advisor))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_UNNEEDED_CATEGORIES" for target in node.targets)
    )
    assert checks["不要"] == set(ast.literal_eval(categories))


def _section(path: pathlib.Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    assert f"\n## {heading}\n" in text, (path, heading)
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


@pytest.fixture(name="definitions", scope="module")
def _definitions() -> tuple[str, str, str]:
    sender = (_ROOT / "hooks/send_to_user.tsx").read_text(encoding="utf-8")
    rules = _section(_ROOT / "skills/atk-mode/playbooks/main-reporting.md", "進捗報告")
    prevention = read_contract(
        _ROOT.parent / "agent-basics/skills/bugfix/SKILL.md",
        _ROOT,
        ("references/prevention.md", "再発防止を検討する"),
    )

    prevention = prevention.split("理由を次の区分名で始める。", 1)[1].split("\n## ", 1)[0]
    return sender, rules, prevention


def test_improvement_category_definitions_agree(definitions: tuple[str, str, str]) -> None:
    _assert_categories_match(*definitions)


@pytest.mark.parametrize(("index", "old"), [(0, "非欠陥"), (0, "認可待ち"), (1, "非欠陥"), (1, "認可待ち"), (2, "非欠陥")])
def test_category_rename_is_detected(definitions: tuple[str, str, str], index: int, old: str) -> None:
    modified = list(definitions)
    assert old in modified[index]
    modified[index] = modified[index].replace(old, "検査用の改名")
    with pytest.raises(AssertionError):
        _assert_categories_match(*modified)
