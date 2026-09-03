"""計画書き込み後の案内と状態記録を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import _plan_fixture
import _plan_format
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _read_state
from quality_checkpoint import QUALITY_CHECKPOINT_NOTICE

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hook.py"


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path | None = None,
    home_dir: pathlib.Path | None = None,
    plan_mode_skill_invoked: bool = False,
) -> subprocess.CompletedProcess[str]:
    """PostToolUseフックを隔離環境で実行する。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    if plan_mode_skill_invoked and state_dir is not None and isinstance(payload, dict):
        sid = payload.get("session_id", "")
        if isinstance(sid, str) and sid:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
                json.dumps({"plan_mode_skill_invoked": True}, ensure_ascii=False),
                encoding="utf-8",
            )
    return _fork_runner.run_script(_SCRIPT, argv=("posttooluse",), input=text, env=env)


def _prepare_plan_home(home_dir: pathlib.Path) -> pathlib.Path:
    """計画ディレクトリを作成する。"""
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _codex_patch(*sections: str) -> str:
    """Codex apply_patch入力を組み立てる。"""
    return "*** Begin Patch\n" + "".join(sections) + "*** End Patch\n"


def _codex_add_section(path: pathlib.Path, content: str = "content\n") -> str:
    lines = "".join(f"+{line}\n" for line in content.splitlines())
    return f"*** Add File: {path}\n{lines}"


def _run_codex_patch(
    session_id: str,
    command: str,
    *,
    state_dir: pathlib.Path,
    home_dir: pathlib.Path,
    plan_mode_skill_invoked: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run(
        {
            "session_id": session_id,
            "tool_name": "apply_patch",
            "tool_input": {"command": command},
            "cwd": "/repo",
            "turn_id": "turn-1",
        },
        state_dir=state_dir,
        home_dir=home_dir,
        plan_mode_skill_invoked=plan_mode_skill_invoked,
    )


def _plan_content() -> str:
    """人間向け計画ファイル（メイン）の正規形の計画を返す。"""
    return _plan_fixture.human_main()


def _detail_content() -> str:
    """人間向け計画ファイル（詳細）の正規形を返す。"""
    return _plan_fixture.human_detail()


class TestPlanPostWrite:
    """計画ファイルへの書き込み後処理を検証する。"""

    def test_write_emits_check_notice_and_records_current_path(self, tmp_path: pathlib.Path) -> None:
        """Write後にstandalone checker案内と現在パスを記録する。"""
        home = tmp_path / "home"
        state_dir = tmp_path / "state"
        plan = _prepare_plan_home(home) / "sample.md"
        content = _plan_content()
        plan.write_text(content, encoding="utf-8")
        plan.with_name("sample.detail.md").write_text(_detail_content(), encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-write",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": "/repo",
            },
            state_dir=state_dir,
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "post-write checks" in result.stdout
        assert "does not conform" not in result.stdout
        assert _read_state(state_dir, "plan-write")["current_plan_file_path"] == str(plan)

    def test_implementation_material_h3_is_not_constrained(self, tmp_path: pathlib.Path) -> None:
        """実装資料配下のH3を変えてもPostToolUseは形式警告を返さない。"""
        home = tmp_path / "home"
        state_dir = tmp_path / "state"
        plan = _prepare_plan_home(home) / "flexible.md"
        content = _plan_content()
        plan.write_text(content, encoding="utf-8")
        detail = plan.with_name("flexible.detail.md")
        units_h3 = f"### {_plan_format.PLAN_IMPLEMENTATION_UNITS_H3}"
        detail_content = _detail_content().replace(units_h3, f"### 実行方法\n\n手順。\n\n{units_h3}")
        detail.write_text(detail_content, encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-flexible",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": "/repo",
            },
            state_dir=state_dir,
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout
        assert "post-write checks" in result.stdout

    def test_read_textlint_reference_does_not_record_plan_state(self, tmp_path: pathlib.Path) -> None:
        """文章lint資料のReadは計画用状態フラグを記録しない。"""
        result = _run(
            {
                "session_id": "read-reference",
                "tool_name": "Read",
                "tool_input": {"file_path": "/repo/references/textlint-violations.md"},
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert not _read_state(tmp_path, "read-reference").get("textlint_violations_read", False)

    def test_non_plan_and_sidecar_paths_are_skipped(self, tmp_path: pathlib.Path) -> None:
        """計画外パスと副次ファイルには案内を返さない。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        for target in (
            tmp_path / "other.md",
            plans / "sample.bugs.md",
            plans / "sample.review.md",
            plans / "sample.codex.log",
        ):
            target.write_text("content\n", encoding="utf-8")
            result = _run(
                {
                    "session_id": f"skip-{target.suffix}",
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(target), "content": "content\n"},
                },
                state_dir=tmp_path / "state",
                home_dir=home,
                plan_mode_skill_invoked=True,
            )
            assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        ("command_builder", "plan_mode_skill_invoked"),
        [
            (lambda main: _codex_patch(_codex_add_section(main)), False),
            (lambda main: _codex_patch(f"*** Update File: {main}\n"), True),
            (lambda main: _codex_patch(f"*** Delete File: {main}\n"), True),
        ],
    )
    def test_codex_non_completion_operations_are_silent(
        self,
        tmp_path: pathlib.Path,
        command_builder,
        plan_mode_skill_invoked: bool,
    ) -> None:
        """計画モード外、部分更新、削除では品質通知を出力しない。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "existing.md"
        detail = plans / "existing.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-silent",
            command_builder(main),
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=plan_mode_skill_invoked,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout

    def test_apply_patch_without_codex_turn_id_does_not_emit_quality_notice(self, tmp_path: pathlib.Path) -> None:
        """Codex識別情報が欠落したapply_patchは品質通知経路への追加対象外とする。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "without-turn.md"
        detail = plans / "without-turn.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run(
            {
                "session_id": "without-turn",
                "tool_name": "apply_patch",
                "tool_input": {"command": _codex_patch(_codex_add_section(main))},
                "cwd": "/repo",
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout

    def test_codex_sidecar_is_silent(self, tmp_path: pathlib.Path) -> None:
        """計画の副次ファイルはペア完成の対象外とする。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "sample.md"
        detail = plans / "sample.detail.md"
        sidecar = plans / "sample.review.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        sidecar.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-sidecar",
            _codex_patch(_codex_add_section(sidecar)),
            state_dir=tmp_path / "state",
            home_dir=home,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout

    def test_codex_plan_path_with_spaces_records_plan_file(self, tmp_path: pathlib.Path) -> None:
        """空白を含む計画パスでもapply_patchの対象を計画ファイル（メイン）として記録する。"""
        home = tmp_path / "home"
        state_dir = tmp_path / "state"
        plans = _prepare_plan_home(home)
        main = plans / "plan with spaces.md"
        detail = plans / "plan with spaces.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-spaces",
            _codex_patch(_codex_add_section(main)),
            state_dir=state_dir,
            home_dir=home,
        )

        assert result.returncode == 0
        assert _read_state(state_dir, "codex-spaces")["current_plan_file_path"] == str(main)

    def test_plan_write_never_emits_quality_notice(self, tmp_path: pathlib.Path) -> None:
        """計画ファイル（メイン）と計画ファイル（詳細）がそろったwhole-writeでも品質通知は出力しない。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "no-notice.md"
        detail = plans / "no-notice.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-no-notice",
            _codex_patch(_codex_add_section(main)),
            state_dir=tmp_path / "state",
            home_dir=home,
        )

        assert result.returncode == 0
        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout

    @pytest.mark.parametrize("tool_name", ["Write", "Edit", "MultiEdit"])
    def test_claude_edit_tools_do_not_emit_codex_quality_notice(
        self,
        tmp_path: pathlib.Path,
        tool_name: str,
    ) -> None:
        """Claude Codeの既存編集経路は品質通知を追加しない。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "claude.md"
        detail = plans / "claude.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        if tool_name == "Write":
            tool_input = {"file_path": str(main), "content": "content\n"}
        elif tool_name == "Edit":
            tool_input = {"file_path": str(main), "old_string": "content", "new_string": "changed"}
        else:
            tool_input = {
                "file_path": str(main),
                "edits": [{"old_string": "content", "new_string": "changed"}],
            }
        result = _run(
            {
                "session_id": f"claude-{tool_name}",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "cwd": "/repo",
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout


def test_plan_format_exposes_permanence_destination_column() -> None:
    """通常変更の恒久化表は反映先列を持つ。"""
    assert "反映先" in _plan_format.PLAN_PERMANENCE_TABLE_HEADER
