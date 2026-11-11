"""commandsの基本動作の契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import shared_layout, shared_roots, state, status_file
from agent_toolkit._atk import environment
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX


@pytest.mark.parametrize("operation", ["start", "send_message", "kill", "stop"])
def test_mcp_only_operations_are_actionable_errors(operation: str, capsys: pytest.CaptureFixture[str]) -> None:
    """MCP専用操作を実行せず、対応する公開ツールの説明へ案内する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", operation])
    captured = capsys.readouterr()
    assert not captured.out
    assert "MCP専用操作" in captured.err
    assert f"agents_serverの{operation}" in captured.err
    assert "公開説明で引数を確認" in captured.err
    assert f"tools.mcp__agents_server__{operation}" in captured.err
    assert "ALL_TOOLS" in captured.err


@pytest.mark.parametrize("operation", ["wait", "notify", "list", "show", "logs"])
def test_existing_agents_operations_keep_help(operation: str, capsys: pytest.CaptureFixture[str]) -> None:
    """既存CLIのヘルプは実行せず正常終了し、一般の誤入力は拒否する。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", operation, "--help"])
    assert "使い方:" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "unknown-operation"])
    assert "MCP専用操作" not in capsys.readouterr().err


@pytest.mark.usefixtures("session_environment")
def test_agents_show_selects_one_session(capsys: pytest.CaptureFixture[str]) -> None:
    """showは完全識別子で指定した1件だけを返す。"""
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "session-1"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "session-1"
    assert payload["prompt"] == "調査せよ"
    assert payload["cwd"] == "/worktree"
    assert payload["engine"] == "codex"
    assert payload["model"] == "model"
    assert payload["effort"] == "medium"
    assert payload["progress"] == "進捗"
    assert {"future_internal", "owner_status_file", "updated_at", "output_updated_at", "seconds_since_output"}.isdisjoint(
        payload
    )


@pytest.mark.usefixtures("session_environment")
def test_agents_show_finds_uncollected_result_after_status_expires(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """期限後に状態一覧から消えたsessionも保存済みの起動情報と結果を返す。"""
    root = shared_layout.status_directory("root-session", tmp_path)
    (root / "root.json").write_text('{"version": 1, "sessions": []}', encoding="utf-8")
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(exist_ok=True)
    (results / "nested.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "agent_message": "完了",
                "turn_seq": 3,
                "owner_status_file": "writer.json",
                "session": {"session_id": "nested", "prompt": "調査せよ", "cwd": "/worktree"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "nested"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "nested"
    assert payload["status"] == "completed"
    assert payload["prompt"] == "調査せよ"
    assert payload["agent_message"] == "完了"
    assert "owner_status_file" not in payload

    (results / "nested.json").write_text(
        json.dumps({"status": "completed", "agent_message": "完了", "owner_status_file": "writer.json"}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "nested"])
    prior_result = json.loads(capsys.readouterr().out)
    assert prior_result["session_id"] == "nested"
    assert prior_result["status"] == "completed"
    assert "prompt" not in prior_result

    (results / "nested.json").unlink()
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "show", "nested"])
    assert capsys.readouterr().err.startswith(f"unknown session: nested\n{NEXT_ACTION_PREFIX}")


@pytest.mark.asyncio
@pytest.mark.usefixtures("session_environment")
@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
@pytest.mark.parametrize("fast_mode", [True, False, None])
async def test_public_show_preserves_latest_speed_in_live_and_retained_results(
    engine: str,
    fast_mode: bool | None,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実際の共有状態と保持結果を公開showへ渡し、他engineと旧形式へ速度項目を加えない。"""
    session = state.SessionState(
        "fast-session",
        str(tmp_path),
        engine=engine,
        model="model",
        effort="medium",
        model_type="execute",
        fast_mode=fast_mode,
        announced=True,
    )
    writer = status_file.StatusFileWriter(
        {session.session_id: session},
        shared_roots.StatusFileIdentity("root-session", "fast.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    try:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "show", session.session_id])
        visible = json.loads(capsys.readouterr().out)
        if engine == "codex" and fast_mode is not None:
            assert visible["fast_mode"] is fast_mode
        else:
            assert "fast_mode" not in visible
        session.status = "completed"
        session.record_turn_completed()
        session.agent_message = "完了"
        session.touch()
        writer.flush()
        writer.deactivate()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "show", session.session_id])
        retained = json.loads(capsys.readouterr().out)
        assert retained.get("fast_mode") == visible.get("fast_mode")
        assert retained["agent_message"] == "完了"
    finally:
        writer.deactivate()


@pytest.mark.parametrize("environment_name", environment.AGENT_ENVIRONMENT_VARIABLES)
@pytest.mark.usefixtures("session_environment")
def test_agents_show_keeps_single_line_in_agent_environment(
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境では1件の状態を空白を含めない1行で書き、非ASCII文字をそのまま残す。"""
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(environment_name, "")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", "session-1"])

    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert '"session_id":"session-1"' in output
    assert "調査せよ" in output
    assert json.loads(output)["prompt"] == "調査せよ"


@pytest.mark.usefixtures("session_environment")
def test_agents_show_rejects_unknown_session(capsys: pytest.CaptureFixture[str]) -> None:
    """未知の識別子は非0と理由で拒否し、一覧で識別子を確かめる操作を示す。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "show", "missing"])

    error = capsys.readouterr().err
    assert error.startswith(f"unknown session: missing\n{NEXT_ACTION_PREFIX}")
    assert "atk agents list" in error
