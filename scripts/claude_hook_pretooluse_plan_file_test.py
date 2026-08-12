"""個人PreToolUseフックが計画本文を配布物固有名で拒否しないことを検証する。"""

import subprocess

from claude_hook_pretooluse_test import _DOTFILES_ROOT, _HOME, _run


class TestPlanFileDotfilesNamesCheck:
    """計画diff専用の固有名拒否分岐が撤去済みであることを検証する。"""

    _PLAN_PATH = str(_HOME / ".claude" / "plans" / "sample-plan.md")
    _BLOCK_SCRIPT = "agent" + "_toolkit_bump"

    @staticmethod
    def _write(file_path: str, content: str) -> subprocess.CompletedProcess[str]:
        """Write payloadをフックへ渡す。"""
        return _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}})

    def test_block_name_in_diff_plus_line_under_agent_toolkit_h3_passes(self) -> None:
        """旧拒否分岐へ到達したdiff追加行を受理する。"""
        content = (
            "## 実装資料\n\n"
            "### `agent-toolkit/skills/plan-mode/SKILL.md`\n\n"
            f"```diff\n+ {self._BLOCK_SCRIPT} を使って処理する\n```\n"
        )
        result = self._write(self._PLAN_PATH, content)
        assert result.returncode == 0

    def test_block_name_in_prose_and_plain_code_block_passes(self) -> None:
        """地の文と通常コードブロックにある固有名も受理する。"""
        content = f"## 実装資料\n\n{self._BLOCK_SCRIPT}\n\n```text\n{self._BLOCK_SCRIPT}\n```\n"
        result = self._write(self._PLAN_PATH, content)
        assert result.returncode == 0

    def test_non_plan_file_still_uses_distribution_check(self) -> None:
        """実ファイル編集時の既存固有名検査は維持する。"""
        target = str(_DOTFILES_ROOT / "agent-toolkit" / "skills" / "example" / "SKILL.md")
        result = self._write(target, self._BLOCK_SCRIPT)
        assert result.returncode == 2
