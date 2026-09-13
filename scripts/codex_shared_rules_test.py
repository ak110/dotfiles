"""`codex_shared_rules`のテスト。"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import codex_shared_rules  # noqa: E402  # pylint: disable=wrong-import-position,import-error


class TestIsCodexSharedRule:
    """共有規範の判定を検査する。"""

    def test_rule_is_shared(self):
        """共有ルールファイルはTrueを返す。"""
        assert codex_shared_rules.is_codex_shared_rule("agent-toolkit/rules/01-agent.md")

    def test_accepts_path_object(self):
        """`pathlib.Path`も受理する。"""
        assert codex_shared_rules.is_codex_shared_rule(pathlib.Path("02-agent-operations.md"))
