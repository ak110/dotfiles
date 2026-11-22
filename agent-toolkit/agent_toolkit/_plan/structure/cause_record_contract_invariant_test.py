import pathlib
import re

from agent_toolkit._plan.structure import constants

_BUGFIX = pathlib.Path(__file__).resolve().parents[4] / "agent-basics" / "skills" / "bugfix"


def _documented_required_lines() -> list[tuple[str, str]]:
    """`SKILL.md`「記録」の箇条`- <行頭語>: <書く内容>`を順に読む。"""
    text = (_BUGFIX / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("\n## 記録\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- (\S+?): <(.+)>$", section, re.MULTILINE)


def _documented_optional_lines() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name in ("recurrence.md", "prevention.md"):
        text = (_BUGFIX / "references" / name).read_text(encoding="utf-8")
        found.extend(re.findall(r"^`(\S+?):`の行には、(.+)を書く。$", text, re.MULTILINE))
    return found


def test_record_labels_match_bugfix_documents() -> None:
    """検査が使う行頭語・順序・書く内容は、定義元の文書の文言と一致する。片側だけの改訂はここで失敗する。"""
    assert list(constants.CAUSE_RECORD_REQUIRED_LABELS.items()) == _documented_required_lines()
    assert list(constants.CAUSE_RECORD_OPTIONAL_LABELS.items()) == _documented_optional_lines()
