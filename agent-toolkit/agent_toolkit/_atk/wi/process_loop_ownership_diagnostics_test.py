"""処理所有の失敗がCLIで原因と復旧操作を表示することを検証する。"""

import argparse
import datetime

import pytest

from agent_toolkit._atk.wi import cli, process_loop, process_loop_ownership, sync
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository


@pytest.mark.parametrize("failure", ["git", "identity"])
def test_ownership_failure_reaches_cli_stderr(tmp_path, monkeypatch, capsys, failure):
    """Gitエラーと識別取得失敗をCLIの捕捉境界まで伝え、tracebackにしない。"""
    repository = tmp_path
    if failure == "identity":
        repository = git_repository.init_repository(tmp_path / "repo")
        monkeypatch.setattr(session_registry.ProcessIdentity, "of", lambda *_args, **_kwargs: None)

    def acquire(_args, _notes, **_kwargs):
        return process_loop_ownership.try_acquire("example.test/team/repo", worktree=repository)

    monkeypatch.setattr(process_loop, "cmd_process_loop", acquire)
    monkeypatch.setattr(sync, "ensure_environment", lambda *_args, **_kwargs: tmp_path)
    args = argparse.Namespace(command="wi", wi_subcommand="process-loop")
    assert cli.dispatch(args, home=tmp_path, now=datetime.datetime.now(datetime.UTC)) == 1
    stderr = capsys.readouterr().err
    if failure == "git":
        assert "処理所有のGit共通ディレクトリを取得できない" in stderr
        assert "not a git repository" in stderr
        assert "対象がGit作業ツリーかとGitの状態を確かめ" in stderr
    else:
        assert "処理所有者のPIDと起動時刻を取得できない" in stderr
        assert "同じコマンドを再実行し、続く場合は" in stderr
    assert "次の操作:" in stderr
    assert "Traceback" not in stderr
    print(stderr)
