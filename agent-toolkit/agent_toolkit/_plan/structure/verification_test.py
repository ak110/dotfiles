"""共通の計画読込がフェンスと旧見出しを区別し、引用したargvを保存実行へ渡せる。"""

import pytest

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan.structure.verification import plan_commands


def test_plan_commands_ignores_fenced_table_and_preserves_quoted_arguments() -> None:
    content = (
        "```md\n## 検証\n| 区分 | 検証コマンド |\n| --- | --- |\n| 変更範囲の検証 | `bad` |\n```\n"
        "## 検証区分\n| 区分 | 検証コマンド |\n| --- | --- |\n"
        "| 近接検証 | `python tool.py 'with space' '$HOME'`<br>`pytest -v` |\n"
    )
    assert [argv for _text, argv in plan_commands(content)] == [["python", "tool.py", "with space", "$HOME"], ["pytest", "-v"]]
    with pytest.raises(ActionableError):
        plan_commands(content + "\n## 検証\n| 区分 | 検証コマンド |\n| --- | --- |\n| 変更範囲の検証 | `extra` |\n")
