"""agent-toolkit/scripts/posttooluse.pyの`session_edited_files`蓄積機構のテスト。

Write / Edit / MultiEditで編集したファイルパスを`session_edited_files`へ蓄積する挙動を検証する。
pretooluse.pyの一括ステージ警告（`_check_bash_bulk_stage_with_unedited_files`）が
「自セッション編集済み集合」として本キーを参照する。
`posttooluse_test.py`のpylint too-many-lines回避のため独立ファイルへ配置する。
"""

import json
import os
import pathlib
import subprocess

import _fork_runner

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"


def _run(payload: dict, *, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, input=json.dumps(payload, ensure_ascii=False), env=env)


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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
