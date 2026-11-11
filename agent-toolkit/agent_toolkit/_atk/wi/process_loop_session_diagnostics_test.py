"""統括CLIの識別とAPI環境ファイルの診断を最終出力で検証する。"""

import argparse
import datetime
import os
from unittest import mock

import pytest

from agent_toolkit._atk.wi import cli, process_loop, process_loop_session, sync
from agent_toolkit._common import session_registry
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands


@pytest.mark.parametrize("failure", ["identity", "missing", "format", "unicode", "empty"])
def test_session_failure_diagnostics_reach_cli(tmp_path, monkeypatch, capsys, failure):
    """起動境界の失敗理由と設定の修正操作をCLI出力で確かめる。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    path = tmp_path / "api.env"
    if failure != "identity":
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(path))
        if failure != "missing":
            path.write_bytes({"format": b"invalid line\n", "unicode": b"\xff", "empty": b""}[failure])
    process = mock.MagicMock()
    process.pid = os.getpid()
    process.poll.return_value = 0
    process.wait.return_value = 0
    popen = mock.MagicMock()
    popen.return_value.__enter__.return_value = process
    monkeypatch.setattr(process_loop_session.subprocess, "Popen", popen)
    if failure == "identity":
        monkeypatch.setattr(session_registry.ProcessIdentity, "of", lambda *_args, **_kwargs: None)

    def launch(_args, _notes, **_kwargs):
        process_loop_session.run_process_session(
            argparse.Namespace(resume=None, no_update=True, worktree="custom"),
            tmp_path,
            "作業する",
            dict(os.environ),
            orchestrator="claude",
            model="sonnet",
            effort="high",
            resume_pending=False,
        )

    monkeypatch.setattr(process_loop, "cmd_process_loop", launch)
    monkeypatch.setattr(sync, "ensure_environment", lambda *_args, **_kwargs: tmp_path)
    args = argparse.Namespace(command="wi", wi_subcommand="process-loop")
    assert cli.dispatch(args, home=tmp_path, now=datetime.datetime.now(datetime.UTC)) == (1 if failure == "identity" else 0)
    stderr = capsys.readouterr().err
    if failure == "identity":
        assert "統括CLIのPIDと起動時刻を取得できない" in stderr
        assert "同じコマンドを再実行し、続く場合は" in stderr
    else:
        expected = {
            "missing": "FileNotFoundError",
            "format": "ValueError: API envファイルは1行1つのKEY=VALUEで指定する",
            "unicode": "UnicodeDecodeError",
            "empty": "空、またはAPI認証・接続方式の変数に値が無い",
        }
        assert expected[failure] in stderr
        assert str(path) in stderr
        assert "atk config set claude_payg_env_file" in stderr
    assert "次の操作:" in stderr
    assert "Traceback" not in stderr
    print(stderr)
