"""F6: 計画ファイル本文の他ファイル参照箇所での絶対行番号検出を保証するテスト。

`posttooluse_plan_format_test.py`本体は1000行制限のため、本テストを別ファイルに分離する。
SSOTは`skills/plan-mode/references/plan-file-guidelines.md`「計画ファイル全体の遵守事項」節。
自ファイル本文の位置参照だけでなく、他ファイル参照箇所（改訂対象の節範囲・引用元位置など）の
行番号・行範囲・行数も検出対象に含むことを明示する。
"""

import pathlib

from posttooluse_plan_format_test import (
    _build_valid_plan,
    _parse_hook_output,
    _prepare_plan_home,
    _run,
    _write_plan,
)


class TestOtherFileLineReferenceCheck:
    """変更内容配下の他ファイル参照箇所での行番号も違反として検出されることを確認する。"""

    def test_change_section_other_file_line_reference_is_warned(self, tmp_path: pathlib.Path) -> None:
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        content = _build_valid_plan(
            overrides={
                "変更内容": (
                    "### 対象ファイル一覧\n\n- [ ] `path/to/other.md`\n\n"
                    "### 変更方針\n\n"
                    "`agent-toolkit/skills/foo/SKILL.md`の45行目を修正する。\n"
                )
            }
        )
        plan = _write_plan(plans, "other-file-ref.md", content)
        result = _run(
            {
                "session_id": "other-file-ref",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "plan file body contains absolute line-number references" in msg
