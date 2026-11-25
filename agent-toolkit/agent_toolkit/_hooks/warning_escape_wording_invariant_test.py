"""警告の通知の文面に、処置の要否を受け手の判断へ預けて終える結びを置かないことを確かめる。

「当たるかは担当が判断せよ」のような結びは、警告を黙って捨てても違反でないと読める逃げ道になり、
警告を無視する挙動の原因の1つだった（2026年11月25日の分析）。
当たらないと判断した時は、根拠を`hook通知への対応:`の行に書かせる文面にする。 pyfltr: allow-comment
"""

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SOURCES = (_ROOT / "agent_toolkit" / "_hooks", _ROOT / "agent_toolkit" / "_agents_server", _ROOT / "hooks")
_ESCAPE = re.compile(
    r"(?:当たるか|妥当性|採否と内容|内容が足りるか)[^。\n]{0,20}は担当が判断|担当が判断せよ|担当が判断してください"
)


def _sources() -> list[pathlib.Path]:
    return [
        path
        for base in _SOURCES
        for path in sorted(base.rglob("*"))
        if path.suffix in {".py", ".ts", ".tsx"} and not path.name.endswith(("_test.py", ".test.ts", ".test.tsx"))
    ]


def test_sources_are_found() -> None:
    assert len(_sources()) >= 50


def test_warnings_do_not_leave_the_decision_to_the_receiver() -> None:
    found = [
        f"{path.relative_to(_ROOT)}:{number}"
        for path in _sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _ESCAPE.search(line)
    ]
    assert found == [], "当たらないと判断した時は根拠を`hook通知への対応:`の行に書かせる文面にする"


def test_pattern_detects_known_escape_wording() -> None:
    assert _ESCAPE.search("逸脱の報告に当たるか、行の内容が足りるかは担当が判断せよ。")
    assert _ESCAPE.search("候補と結論の妥当性は担当が判断してください。")
    assert not _ESCAPE.search("当たらないと判断した時は、根拠を`hook通知への対応:`の行に書け。")
