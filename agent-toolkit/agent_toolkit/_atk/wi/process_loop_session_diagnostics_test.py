import argparse
import datetime
import os
from unittest import mock

import pytest

from agent_toolkit._atk import config_migration, config_settings
from agent_toolkit._atk.wi import cli, process_loop, process_loop_session
from agent_toolkit._common import next_action, session_registry
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands


@pytest.mark.parametrize("failure", ["identity", "format", "unicode", "empty"])
def test_session_failure_diagnostics_reach_cli(tmp_path, monkeypatch, capsys, failure):
    """起動境界の失敗理由と設定の修正操作をCLI出力で確かめる。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    path = config_settings.config_dir() / "claude-payg.env"
    if failure != "identity":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes({"format": b"invalid line\n", "unicode": b"\xff", "empty": b"AWS_REGION=us-east-1"}[failure])
    if failure in {"identity", "empty"}:
        config_migration.migrate_legacy_settings()
    process = mock.MagicMock()
    process.pid = 1234567890
    process.poll.return_value = 0
    process.wait.return_value = 0
    popen = mock.MagicMock()
    popen.return_value.__enter__.return_value = process
    monkeypatch.setattr(process_loop_session.subprocess, "Popen", popen)
    # 診断専用の偽CLIはOS資源を所有しない。workerの子を実回収へ流さない。
    monkeypatch.setattr(process_loop_session.process_tree, "collect_descendants", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(process_loop_session.process_tree, "stop_owned_process", lambda *_args, **_kwargs: None)
    identity = None if failure == "identity" else session_registry.ProcessIdentity(process.pid, 1.0)
    monkeypatch.setattr(session_registry.ProcessIdentity, "of", lambda *_args, **_kwargs: identity)

    def launch(args):
        completed = process_loop_session.run_process_session(
            args,
            tmp_path,
            "作業する",
            dict(os.environ),
            orchestrator="claude",
            model="sonnet",
            effort="high",
            resume_pending=False,
        )
        return 0 if completed else 1

    monkeypatch.setattr(process_loop, "cmd_process_loop", launch)
    args = argparse.Namespace(command="wi", wi_subcommand="process-loop")
    assert cli.dispatch(args, home=tmp_path, now=datetime.datetime.now(datetime.UTC)) == (0 if failure == "empty" else 1)
    stderr = capsys.readouterr().err
    if failure == "identity":
        assert "統括CLIのPIDと起動時刻を取得できない" in stderr
        assert "起動するCLIの実体を確認する" in stderr
        assert "統括が未起動の場合だけ再試行する" in stderr
    elif failure == "empty":
        assert "API認証・接続方式の変数に値が無い" in stderr
        assert "設定画面またはatk config set claude_payg_env" in stderr
    else:
        assert "共有設定へ取り込めない" in stderr
        assert str(path.parent) in stderr
        assert "KEY=VALUE形式・読取可能な通常ファイル" in stderr
        assert path.is_file()
        assert not popen.called
    assert "次の操作:" in stderr
    assert "Traceback" not in stderr


def test_process_loop_keeps_specific_recovery_action(tmp_path, monkeypatch, capsys):
    session_id = "a64b97a3-4a65-431f-8b68-56201d97cc76"

    def launch(_args):
        raise next_action.ActionableError(
            "新しい compact_boundary を確認できない",
            next_action=f"`atk wi process-loop resume {session_id}` で予約する",
        )

    monkeypatch.setattr(process_loop, "cmd_process_loop", launch)
    args = argparse.Namespace(command="wi", wi_subcommand="process-loop")
    assert cli.dispatch(args, home=tmp_path, now=datetime.datetime.now(datetime.UTC)) == 1
    stderr = capsys.readouterr().err
    assert "新しい compact_boundary を確認できない" in stderr
    assert f"次の操作: `atk wi process-loop resume {session_id}`" in stderr
    assert stderr.count("次の操作:") == 1
