"""scripts/claude_hook_pretooluse.py の計画ファイル検査関連テスト。"""

import subprocess

import pytest
from claude_hook_pretooluse_test import _DOTFILES_ROOT, _HOME, _run


class TestPlanFileDotfilesNamesCheck:
    """計画ファイル`## 変更内容`配下のdiff +行へのdotfiles固有名混入のブロック判定。

    対象は Write のみ。agent-toolkit/ パスを示すH3配下のdiffブロック+行が検査対象。
    """

    _PLAN_PATH = str(_HOME / ".claude" / "plans" / "sample-plan.md")

    # block 対象名は文字列結合で組み立てる（本ファイル自身が hook の警告対象になるのを避けるため）。
    _BLOCK_SCRIPT = "agent" + "_toolkit_bump"
    _BLOCK_PROJECT = "glata" + "sks"

    @staticmethod
    def _write(file_path: str, content: str) -> subprocess.CompletedProcess[str]:
        return _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}})

    @staticmethod
    def _plan_with_changes(changes_body: str) -> str:
        """`## 変更内容` セクションのみを持つ計画ファイル本文を生成する。"""
        return f"# 計画\n\n## 変更内容\n\n{changes_body}\n"

    def test_block_name_in_diff_plus_line_under_at_h3(self):
        """配布物パスを示すH3配下のdiff +行にblock名→ exit 2。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n+ {name} を使って処理する\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 2
        assert name in result.stderr
        assert "[auto-generated: dotfiles/claude_hook_pretooluse]" in result.stderr
        assert "Auto-generated hook notice" in result.stderr

    def test_block_name_only_on_diff_minus_line_passes(self):
        """block名がdiff `-`行のみ→通過。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n- {name} を削除\n+ 後継処理へ移行\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 0

    def test_block_name_only_in_prose_passes(self):
        """block名がdiffコードブロック範囲外（地の文）のみ→通過。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n{name} に関する説明\n\n```diff\n+ 別の変更\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 0

    def test_block_name_under_non_distribution_h3_passes(self):
        """配布物外パス（例: `scripts/foo.py`）を示すH3配下のdiff +行にblock名→通過。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `scripts/foo.py` の修正\n\n```diff\n+ {name} を呼び出す\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 0

    def test_block_name_in_non_diff_code_block_passes(self):
        """通常コードブロック（`diff`以外の言語指定）内側にblock名→通過。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```python\n# {name}\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 0

    def test_four_backtick_diff_block_plus_line_blocks(self):
        """4バッククォートで開閉するdiffコードブロック内の+行にblock名→ exit 2。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n````diff\n+ {name} を参照する\n````\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 2
        assert name in result.stderr

    def test_background_section_with_block_name_passes(self):
        """`## 背景`原文転記領域にblock名・配布物パスが含まれてもブロックしない（セクション分割で除外）。"""
        name = self._BLOCK_SCRIPT
        content = (
            f"# 計画\n\n"
            f"## 背景\n\n"
            f"### `agent-toolkit/skills/plan-mode/SKILL.md`\n\n"
            f"```diff\n"
            f"+ {name} を呼ぶ\n"
            f"```\n\n"
            f"## 変更内容\n\n"
            f"変更なし\n"
        )
        result = self._write(self._PLAN_PATH, content)
        assert result.returncode == 0

    def test_non_plan_file_path_passes(self):
        """計画ファイル以外のパスへの Write→通過。"""
        name = self._BLOCK_SCRIPT
        target = str(_DOTFILES_ROOT / "docs" / "guide.md")
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n+ {name}\n```\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": target, "content": changes}})
        assert result.returncode == 0

    def test_edit_tool_passes(self):
        """計画ファイルへの Edit→通過（本文全域取得不可のため対象外）。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n+ {name}\n```\n"
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": self._PLAN_PATH,
                    "old_string": "x",
                    "new_string": changes,
                },
            }
        )
        assert result.returncode == 0

    def test_multiedit_tool_passes(self):
        """計画ファイルへの MultiEdit→通過（本文全域取得不可のため対象外）。"""
        name = self._BLOCK_SCRIPT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n+ {name}\n```\n"
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": self._PLAN_PATH,
                    "edits": [{"old_string": "x", "new_string": changes}],
                },
            }
        )
        assert result.returncode == 0

    def test_dynamic_names_detected(self):
        """動的取得対象（個人スキル名・pytoolsコマンド名・scripts名）も検出する。"""
        # sync-cross-project は .chezmoi-source/dot_claude/skills/ 配下の個人スキル名として動的取得される。
        # 文字列リテラルで直接書くと本ファイル自身が警告を発する原因になるため組み立てる。
        skill_name = "sync" + "-cross-project"
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n+ {skill_name} スキルを参照する\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 2
        assert skill_name in result.stderr

    def test_fixed_project_name_detected(self):
        """固定プロジェクト名（block対象: glatasks/gv/lc/smpr）も検出する。"""
        name = self._BLOCK_PROJECT
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n+ {name} との連携を追加\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 2
        assert name in result.stderr

    @pytest.mark.parametrize("name", ["pyfltr", "pytilpack"])
    def test_warn_only_names_not_blocked(self, name: str):
        """warn対象名（`pyfltr`・`pytilpack`）はブロック対象外。"""
        changes = f"### `agent-toolkit/skills/plan-mode/SKILL.md` の修正\n\n```diff\n+ {name} の参照を追加\n```\n"
        result = self._write(self._PLAN_PATH, self._plan_with_changes(changes))
        assert result.returncode == 0
