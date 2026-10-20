"""導入済みApp Serverで探索の開始が通常コマンドへ到達することを検証する。"""

import pathlib
import shutil
import sys

import pytest

from agent_toolkit._agents_server import codex
from agent_toolkit._testing.isolation import restore_host_environment


@pytest.mark.asyncio
async def test_explore_start_reaches_normal_commands(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """モデルを呼ばず、実thread開始とcommand/execで得た本文を入力資料と比べる。

    保存済みthreadの再開は実turnの履歴を使うagents_server_live_test.pyの
    test_live_explore_common_investigationで検収する。
    開始・再開への設定配送の両経路はcodex_test.pyで検証する。
    """
    restore_host_environment(monkeypatch)
    if shutil.which("codex") is None:
        pytest.skip("codexコマンドが導入されていません")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex, "APP_SERVER_WORKING_DIRECTORY", str(tmp_path))
    source = tmp_path / "資料 空白.txt"
    source.write_text("探索対象の日本語本文", encoding="utf-8")
    async with codex.AppServerManager() as backend:
        observed = []

        async def investigate(session, prompt, client=None):
            del prompt
            assert client is not None
            result = await client.request(
                "command/exec",
                {
                    "command": [
                        sys.executable,
                        "-c",
                        "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))",
                        str(source),
                    ],
                    "cwd": session.cwd,
                    "sandboxPolicy": {"type": "dangerFullAccess"},
                    "timeoutMs": 5000,
                },
            )
            observed.append(result)
            session.status = "completed"
            session.turn_completed = True

        monkeypatch.setattr(backend, "_start_turn", investigate)
        session = await backend.start("同じ読取調査", str(tmp_path), launch_kind="explore")
        assert session.status == "completed", session.error
        assert len(observed) == 1
        for result in observed:
            assert result["exitCode"] == 0
            assert result["stdout"].strip() == "探索対象の日本語本文"
            assert not result["stderr"]
        assert source.read_text(encoding="utf-8") == "探索対象の日本語本文"
