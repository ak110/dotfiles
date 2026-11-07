"""保存した実行条件の差を、値を露出させず再利用可否へ対応付ける。"""

import json
import os
import pathlib
import sys

import pytest

from agent_toolkit._atk import run_command
from agent_toolkit._common import inherited_venv
from agent_toolkit._testing import git_repository


@pytest.mark.parametrize(
    "change",
    [
        "added",
        "removed",
        "changed",
        "launch",
        "python-alias",
        "legacy",
        "output",
        "session",
        "session-removed",
        "launch-kind",
        "path",
    ],
)
def test_saved_success_explains_conditions_and_preserves_normalization(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    """未知の入力差と復元済み起動情報を区別し、旧記録や欠落出力を成功としない。"""
    repo = git_repository.init_repository(tmp_path / "repo", files={"input": "same"}, commit_message="基準")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(run_command.managed_temp, "create_output_temp", lambda _prefix: output)
    monkeypatch.setenv("UNKNOWN_VERIFICATION_INPUT", "before-value")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "before-session")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "before-owner")
    monkeypatch.setenv("CODEX_THREAD_ID", "before-thread")
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND", "delegate")
    argv = [sys.executable, "-c", "print('verified')"]
    observed, code, failure = run_command.execute(argv, repo, 30)
    assert code == 0 and failure is None
    path = pathlib.Path(observed["record_path"])
    assert "before-value" not in path.read_text(encoding="utf-8")
    assert run_command.reusable_success(path, argv, repo) is not None
    if change == "added":
        monkeypatch.setenv("NEW_VERIFICATION_INPUT", "after-value")
    elif change == "removed":
        monkeypatch.delenv("UNKNOWN_VERIFICATION_INPUT")
    elif change == "changed":
        monkeypatch.setenv("UNKNOWN_VERIFICATION_INPUT", "after-value")
    elif change == "launch":
        monkeypatch.setenv("UNKNOWN_VERIFICATION_INPUT", "launch-value")
        monkeypatch.setenv(
            inherited_venv.LAUNCH_RESTORE_KEY,
            json.dumps({"UNKNOWN_VERIFICATION_INPUT": ["before-value", "launch-value"]}),
        )
    elif change == "python-alias":
        executable = pathlib.Path(sys.executable)
        monkeypatch.setattr(sys, "executable", str(executable.parent) + os.sep + "." + os.sep + executable.name)
    elif change == "session":
        # 会話・委譲の識別子の値だけが違う再開。検証結果に影響しないため成功を再利用する。
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "after-session")
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "after-owner")
        monkeypatch.setenv("CODEX_THREAD_ID", "after-thread")
    elif change == "session-removed":
        # 委譲元の識別子の有無は通知先の解決を切り替えるため、再利用しない。
        monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION")
    elif change == "launch-kind":
        # 委譲の種別は取得条件などの動作を切り替えるため、識別子と違って値の差でも再利用しない。
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND", "explore")
    elif change == "path":
        monkeypatch.setenv("PATH", str(tmp_path / "path-entry-value") + os.pathsep + os.environ["PATH"])
    elif change == "legacy":
        observed["execution_conditions"] = "old-aggregate-without-details"
        path.write_text(json.dumps(observed), encoding="utf-8")
    else:
        pathlib.Path(observed["stdout_path"]).unlink()
    reused = run_command.reusable_success(path, argv, repo)
    assert (reused is not None) == (change in {"launch", "python-alias", "session"})
    diagnostic = capsys.readouterr().err
    assert "before-value" not in diagnostic and "after-value" not in diagnostic and "launch-value" not in diagnostic
    if change in {"added", "removed", "changed"}:
        assert "VERIFICATION_INPUT" in diagnostic
    if change == "session-removed":
        assert "environment: 削除 AGENT_TOOLKIT_OWNER_SESSION" in diagnostic and "before-owner" not in diagnostic
    if change == "launch-kind":
        assert "environment: 変更 AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND" in diagnostic and "explore" not in diagnostic
    if change == "path":
        assert "environment: 変更 PATH" in diagnostic and "path-entry-value" not in diagnostic
    if change == "legacy":
        assert "内訳" in diagnostic
