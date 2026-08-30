"""agent-toolkit/scripts/posttooluse.pyの`session_edited_files`蓄積機構のテスト。

Write / Edit / MultiEditで編集したファイルパスを`session_edited_files`へ蓄積する挙動を検証する。
pretooluse.pyの一括ステージ警告（`_check_bash_bulk_stage_with_unedited_files`）が
「自セッション編集済み集合」として本キーを参照する。
`posttooluse_test.py`のpylint too-many-lines回避のため独立ファイルへ配置する。
"""

# pylint: disable=duplicate-code  # 独立したフックシナリオ間で状態ディレクトリ初期化を同形に保つ。

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest
from _test_helpers import _read_state

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hook.py"


def _run(payload: dict, *, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("posttooluse",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


class TestSessionEditedFilesAccumulation:
    """Write / Edit / MultiEditで編集したファイルパスを`session_edited_files`へ蓄積する。"""

    def test_write_appends_to_session_edited_files(self, tmp_path: pathlib.Path) -> None:
        sid = "edited-write"
        target = str(tmp_path / "a.py")
        _run(
            {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("session_edited_files") == [target]

    def test_edit_appends_to_session_edited_files(self, tmp_path: pathlib.Path) -> None:
        sid = "edited-edit"
        target = str(tmp_path / "b.py")
        _run(
            {"session_id": sid, "tool_name": "Edit", "tool_input": {"file_path": target}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("session_edited_files") == [target]

    def test_duplicate_edits_not_duplicated(self, tmp_path: pathlib.Path) -> None:
        sid = "edited-dup"
        target = str(tmp_path / "c.py")
        for _ in range(3):
            _run(
                {"session_id": sid, "tool_name": "Edit", "tool_input": {"file_path": target}},
                state_dir=tmp_path,
            )
        assert _read_state(tmp_path, sid).get("session_edited_files") == [target]

    def test_multiedit_records_single_file(self, tmp_path: pathlib.Path) -> None:
        sid = "edited-multi"
        target = str(tmp_path / "d.py")
        _run(
            {"session_id": sid, "tool_name": "MultiEdit", "tool_input": {"file_path": target, "edits": []}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("session_edited_files") == [target]

    def test_absolute_and_relative_paths_stored_verbatim(self, tmp_path: pathlib.Path) -> None:
        sid = "edited-paths"
        abs_path = str(tmp_path / "abs.py")
        rel_path = "rel/foo.py"
        _run(
            {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": abs_path, "content": "x"}},
            state_dir=tmp_path,
        )
        _run(
            {"session_id": sid, "tool_name": "Edit", "tool_input": {"file_path": rel_path}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("session_edited_files") == [abs_path, rel_path]

    def test_empty_file_path_not_stored(self, tmp_path: pathlib.Path) -> None:
        sid = "edited-empty"
        _run(
            {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": "", "content": "x"}},
            state_dir=tmp_path,
        )
        _run(
            {"session_id": sid, "tool_name": "Edit", "tool_input": {}},
            state_dir=tmp_path,
        )
        assert "session_edited_files" not in _read_state(tmp_path, sid)


def _patch(*sections: str) -> str:
    """Codexの`apply_patch`入力本文を組み立てる。"""
    return "*** Begin Patch\n" + "".join(sections) + "*** End Patch\n"


def _codex_payload(patch_text: str, cwd: pathlib.Path, session_id: str) -> dict:
    """成功した`apply_patch`のPostToolUse payloadを組み立てる。"""
    return {
        "session_id": session_id,
        "tool_name": "apply_patch",
        "tool_input": {"command": patch_text},
        "tool_response": "applied",
        "cwd": str(cwd),
        "turn_id": "turn-1",
    }


class TestCodexApplyPatchRecording:
    """成功したCodex `apply_patch`の全対象を編集状態へ記録する。"""

    def test_all_operations_are_recorded_without_reading_removed_contents(self, tmp_path: pathlib.Path) -> None:
        """追加・更新・削除の対象と移動元・移動先を全て記録する。"""
        sid = "codex-apply-patch"
        repo = tmp_path / "repo"
        (repo / "docs").mkdir(parents=True)
        (repo / "docs" / "keep.md").write_text("本文\n", encoding="utf-8")
        patch_text = _patch(
            "*** Add File: docs/new.md\n+追加本文\n",
            "*** Update File: docs/keep.md\n@@\n-本文\n+更新本文\n",
            "*** Delete File: docs/gone.md\n",
            "*** Update File: docs/from.md\n*** Move to: docs/to.md\n@@\n-旧\n+新\n",
        )

        result = _run(_codex_payload(patch_text, repo, sid), state_dir=tmp_path)

        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("session_edited_files") == [
            "docs/new.md",
            "docs/keep.md",
            "docs/gone.md",
            "docs/to.md",
            "docs/from.md",
        ]

    def test_bash_payload_is_not_treated_as_edit(self, tmp_path: pathlib.Path) -> None:
        """CodexのBashは編集記録の対象にしない。"""
        sid = "codex-bash"

        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "uvx pyfltr run ."},
                "cwd": str(tmp_path),
                "turn_id": "turn-1",
            },
            state_dir=tmp_path,
        )

        assert "session_edited_files" not in _read_state(tmp_path, sid)

    def test_added_plan_file_returns_check_guidance(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """追加した計画ファイルだけが検査案内を返し、更新は返さない。"""
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        added = plans / "added.md"
        updated = plans / "updated.md"
        updated.write_text("# 旧\n", encoding="utf-8")
        env_state = {"plan_mode_skill_invoked": True}
        for session_id, patch_text, expected in (
            ("codex-plan-add", _patch(f"*** Add File: {added}\n+# 計画\n"), True),
            ("codex-plan-update", _patch(f"*** Update File: {updated}\n@@\n-# 旧\n+# 新\n"), False),
        ):
            state_path = tmp_path / f"claude-agent-toolkit-{session_id}.json"
            state_path.write_text(json.dumps(env_state), encoding="utf-8")
            result = _run(_codex_payload(patch_text, tmp_path, session_id), state_dir=tmp_path)
            assert ("Run the post-write checks" in result.stdout) is expected
            assert _read_state(tmp_path, session_id).get("current_plan_file_path") is not None

    def test_conditional_prohibition_check_targets_existing_files_only(self, tmp_path: pathlib.Path) -> None:
        """適用後に存在する対象だけを事後文書検査へ渡す。"""
        sid = "codex-prohibition"
        repo = tmp_path / "repo"
        rules = repo / "agent-toolkit" / "rules"
        rules.mkdir(parents=True)
        (rules / "present.md").write_text("検証した状態でcommitしない\n", encoding="utf-8")
        patch_text = _patch(
            "*** Update File: agent-toolkit/rules/present.md\n@@\n-旧\n+新\n",
            "*** Delete File: agent-toolkit/rules/removed.md\n",
        )

        result = _run(_codex_payload(patch_text, repo, sid), state_dir=tmp_path)

        assert result.returncode == 0
        assert "条件付き禁止形" in result.stdout
        assert "removed.md" not in result.stdout
