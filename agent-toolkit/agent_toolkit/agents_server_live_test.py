"""agents_serverの実backendによるバックグラウンドタスク完了後の自動再開を検証する。"""

import os
import pathlib

import pytest

import agent_toolkit.agents_server_mcp as subject
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing import isolation

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_TOOLKIT_LIVE_AGENTS_TEST") != "1",
    reason="実際のagents_serverを起動するテストは明示指定時だけ実行する",
)

_PROMPT = """Bashツールで`sleep 2`を背景実行し、待たずにturnを終えよ。
バックグラウンドタスクの完了通知で自動的に再開したturnでは、最終応答を`AUTO_RESUME_COMPLETED`だけにせよ。"""


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["delegate", "explore", "shell"])
async def test_live_launch_waits_for_automatic_resume(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公開する起動ツール`start`の3つのmodeが、再開指示なしでバックグラウンドタスク完了後の結果を返す。"""
    manager = subject.AgentsServerManager()
    monkeypatch.setattr(subject, "_MANAGER", manager)
    cwd = str(pathlib.Path(__file__).parents[2])
    # 委譲先CLIが認証と設定を読み、PATHから起動できるよう、ホームと設定ディレクトリとPATHを戻す。
    isolation.restore_host_environment(monkeypatch)
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type: [("claude", "sonnet[1m]", "medium")],
    )
    try:
        if mode == "delegate":
            await subject.start(cwd, mode="delegate", prompt=_PROMPT, model_type="low_tier")
        elif mode == "explore":
            await subject.start(cwd, mode="explore", prompt=_PROMPT)
        else:
            await subject.start(
                cwd,
                mode="shell",
                command="sleep 2",
                summary_policy="Bashツールを背景実行し、完了通知で再開した後に`AUTO_RESUME_COMPLETED`だけを返す。",
            )

        result = await manager.wait()

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED"
    finally:
        await manager.close()


_GRANDCHILD_PROMPT = """`agents_server`の起動ツールで、コマンド`sleep 5`を実行して終了状態を要約させる委譲を1件起動せよ。
起動ツールが`mode`を受け取る場合は`start`へ`mode`の`shell`を渡し、受け取らない場合は`start_shell`を使う。
起動の直後は`atk agents wait`を実行せず、`待機中: <起動したsession_id>`の1行だけを出力してターンを終えよ。
自動的に再開したターンでは`atk agents wait`でその委譲の結果を回収し、最終応答を`AUTO_RESUME_COMPLETED`だけにせよ。"""


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", [("codex", "sol", "medium"), ("claude", "sonnet[1m]", "medium")])
async def test_live_grandchild_wait_resumes_same_session(
    candidate: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """委譲先が孫sessionを起動して待機を表明すると、孫の終端後に同じsessionが手動の指示なしに再開し完了報告を返す。

    待機表明の行を完了報告として配送せず、再開したturnの結果だけを委譲元へ返すことを確かめる。
    """
    manager = subject.AgentsServerManager()
    monkeypatch.setattr(subject, "_MANAGER", manager)
    cwd = str(pathlib.Path(__file__).parents[2])
    # 委譲先CLIが認証と設定を読み、PATHから起動できるよう、ホームと設定ディレクトリとPATHを戻す。
    isolation.restore_host_environment(monkeypatch)
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [candidate])
    try:
        started = await subject.start(cwd, mode="delegate", prompt=_GRANDCHILD_PROMPT, model_type="high_tier")
        session = manager.sessions[started["session_id"]]

        result = await manager.wait()
        # 待機上限へ達した応答は`running`を返すため、終端まで同じ待機を繰り返す。
        for _ in range(10):
            if result["status"] != "running":
                break
            result = await manager.wait()

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED", result
        assert "unobservedSessions" not in (result.get("error") or {}), result
        assert session.turn_seq == 2
    finally:
        await manager.close()
