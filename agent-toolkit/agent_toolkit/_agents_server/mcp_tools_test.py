"""mcp_toolsの基本動作の契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, shared_roots, state, status_file, tool_names
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    UnavailableStartBackend,
    _complete,
    _install_backend,
    _manager_with_fake,
    _observed_input_lines,
    _start_tool,
)
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_public_tools_expose_single_start_with_modes() -> None:
    """公開ツールは起動の`start`1件と継続・中断・破棄・一覧・詳細の6件とし、`start`はmodeごとの入力を持つ。"""
    assert set(mcp_tools.mcp._tool_manager._tools) == {"start", "send_message", "kill", "list", "show", "stop"}
    start_tool = _start_tool()
    properties = start_tool.parameters["properties"]
    assert properties.keys() == {
        "cwd",
        "mode",
        "subagent_md_path",
        "extra_params",
        "prompt",
        "command",
        "summary_policy",
        "label",
        "model_type",
        "handoff_from",
        "whole_contract_path",
    }
    assert {"engine", "model", "effort"}.isdisjoint(properties)
    assert tuple(properties["mode"]["anyOf"][0]["enum"]) == tool_names.START_MODES
    assert properties["mode"]["default"] is None
    assert "省略時は`task`" in properties["mode"]["description"]
    assert "required" not in start_tool.parameters
    for name in properties.keys() - {"cwd", "mode"}:
        assert properties[name]["default"] is None, name
    kill_tool = mcp_tools.mcp._tool_manager.get_tool("kill")
    assert kill_tool is not None
    assert kill_tool.parameters["properties"]["stop"]["default"] is False
    stop_tool = mcp_tools.mcp._tool_manager.get_tool("stop")
    assert stop_tool is not None
    assert stop_tool.parameters["properties"].keys() == {"session_id"}


@pytest.mark.asyncio
async def test_public_start_returns_label_of_started_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """全modeの公開`start`の応答が、起動したsessionの保持する担当名を`label`で返す。

    応答に担当名が無いか別のsessionの値を返すと、複数の委譲先を起動した委譲元が`session_id`と担当を対応付けられず、
    追送を別の担当へ送る。明示・省略・空白の指定、taskの引き継ぎ記録付き応答、候補の切替と起動直後の失敗を含める。
    """
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "model", "high")]
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-state")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "caller-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    session_area = _managed_temp.resolve_session_temp("caller-session")
    (session_area / "work-contract.md").write_text("目的: 起動したセッションのラベルの返却を確かめる\n", encoding="utf-8")
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        cwd = str(tmp_path)
        task_params = {
            **dict(
                line.split(": ", 1)
                for line in _observed_input_lines("exec.subagent.md", tmp_path)
                if not line.startswith("引き継ぎ記録先:")
            ),
            "レーン識別子": "lane-07",
        }

        responses = {
            "lane-02": await mcp_tools.start(
                cwd, mode="delegate", prompt="対象の調査\n詳細", model_type="chores", label="lane-02"
            ),
            "別の調査": await mcp_tools.start(cwd, mode="delegate", prompt="別の調査\n詳細", model_type="chores"),
            "空の調査": await mcp_tools.start(cwd, mode="delegate", prompt="空の調査", model_type="chores", label=""),
            "空白の調査": await mcp_tools.start(cwd, mode="delegate", prompt="空白の調査", model_type="chores", label="   "),
            "explore": await mcp_tools.start(cwd, mode="explore", prompt="探索"),
            "write-awi": await mcp_tools.start(cwd, mode="write", prompt="定型変更", label="write-awi"),
            "shell-git": await mcp_tools.start(
                cwd, mode="shell", command="git status --short", summary_policy="終了コードだけ"
            ),
            "shell-named": await mcp_tools.start(
                cwd, mode="shell", command="git status --short", summary_policy="終了コードだけ", label="shell-named"
            ),
            "lane-07-exec": await mcp_tools.start(cwd, subagent_md_path="exec", extra_params=task_params),
        }

        for label in ("", "   "):
            shell = await mcp_tools.start(
                cwd, mode="shell", command="git status --short", summary_policy="終了コードだけ", label=label
            )
            assert shell["label"] == (await manager.show_session(shell["session_id"]))["label"] == "shell-git"
        assert "handoff_record_path" in responses["lane-07-exec"]
        assert len({response["session_id"] for response in responses.values()}) == len(responses)
        for expected, response in responses.items():
            assert response["label"] == expected
            assert (await manager.show_session(response["session_id"]))["label"] == expected

        candidates = [("codex", "first", "high"), ("claude", "second", "medium")]
        monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
        async with _manager_with_fake("claude") as (switching, claude):
            _install_backend(switching, "codex", UnavailableStartBackend(switching.sessions, "codex"))
            monkeypatch.setattr(mcp_tools, "_MANAGER", switching)
            switched = await mcp_tools.start(cwd, mode="delegate", prompt="切替", model_type="chores", label="switched")
            assert switched["excluded_candidates"]
            assert switched["label"] == (await switching.show_session(switched["session_id"]))["label"] == "switched"

            _install_backend(switching, "claude", UnavailableStartBackend(switching.sessions, "claude"))
            del claude
            failed = await mcp_tools.start(cwd, mode="delegate", prompt="失敗", model_type="chores", label="all-failed")
            assert failed["status"] == "failed"
            assert failed["label"] == (await switching.show_session(failed["session_id"]))["label"] == "all-failed"


@pytest.mark.asyncio
async def test_send_message_returns_label_of_delivered_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開`send_message`の応答が、配送した宛先sessionの担当名を`label`で返す。

    実行中turnへのsteer、終了後のreply、保持期限の経過後と破棄後の再開の各配送で、
    宛先以外のsessionや追送本文から担当名を返すと、委譲元は誤送を応答から検出できない。
    """
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "model", "high")]
    )
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        first = await manager.start("execute", "i568の投入", str(tmp_path), label="add-wi-568")
        second = await manager.start("execute", "i569の投入", str(tmp_path), label="add-wi-569")
        first_id, second_id = first["session_id"], second["session_id"]

        steered = await mcp_tools.send_message(second_id, "add-wi-568への追加情報")
        assert (steered["delivery"], steered["label"]) == ("steered", "add-wi-569")

        _complete(manager.sessions[first_id])
        replied = await mcp_tools.send_message(first_id, "add-wi-569への追加情報")
        assert (replied["delivery"], replied["label"]) == ("reply_started", "add-wi-568")

        _complete(manager.sessions[first_id])
        manager._expire_session(first_id)
        resumed_after_expiry = await mcp_tools.send_message(first_id, "続行")
        assert (resumed_after_expiry["delivery"], resumed_after_expiry["label"]) == ("reply_started", "add-wi-568")

        _complete(manager.sessions[second_id])
        await manager.stop(second_id)
        resumed_after_stop = await mcp_tools.send_message(second_id, "続行")
        assert (resumed_after_stop["delivery"], resumed_after_stop["label"]) == ("reply_started", "add-wi-569")


@pytest.mark.asyncio
async def test_public_start_variants_and_send_message_return_minimal_responses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開ラッパーは後続操作に必要な最小項目だけを返す。"""
    response = {
        "session_id": "session",
        "status": "running",
        "engine": "codex",
        "model": "model",
        "effort": "medium",
        "model_type": "execute",
        "root_session_id": "root",
        "label": "担当",
    }
    manager = SimpleNamespace(
        launcher_session_id=lambda: None,
        start=AsyncMock(return_value=response),
        start_explore=AsyncMock(return_value=response),
        start_write=AsyncMock(return_value=response),
        start_shell=AsyncMock(return_value=response),
        send_message=AsyncMock(
            return_value={"delivery": "replied", "label": "担当", "previous_result": {"status": "completed"}}
        ),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    identity_fields = {
        "launch_identity": {
            "engine": "codex",
            "model": "model",
            "effort": "medium",
            "source": "launch_candidate",
        },
        "observed_identity": None,
    }

    delegated = await mcp_tools.start(str(tmp_path), mode="delegate", prompt="本文", model_type="chores")
    assert pathlib.Path(delegated.pop("handoff_record_path")).is_absolute()
    assert delegated == {
        "session_id": "session",
        "status": "running",
        "label": "担当",
        "root_session_id": "root",
        **identity_fields,
    }
    assert await mcp_tools.start(str(tmp_path), mode="explore", prompt="探索") == {
        "session_id": "session",
        "status": "running",
        "label": "担当",
        "root_session_id": "root",
        **identity_fields,
    }
    assert await mcp_tools.start(str(tmp_path), mode="write", prompt="定型変更") == {
        "session_id": "session",
        "status": "running",
        "label": "担当",
        "root_session_id": "root",
        **identity_fields,
    }
    assert await mcp_tools.start(str(tmp_path), mode="shell", command="make test", summary_policy="終了状態") == {
        "session_id": "session",
        "status": "running",
        "label": "担当",
        "root_session_id": "root",
        **identity_fields,
    }
    assert await mcp_tools.send_message("session", "続行") == {
        "delivery": "replied",
        "label": "担当",
        "previous_result": {"status": "completed"},
    }


@pytest.mark.asyncio
async def test_success_response_key_sets_for_all_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """8ツールの成功応答を消費側が使うキー集合へ固定する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        _install_backend(manager, "codex", FakeBackend(manager.sessions, "codex"))
        monkeypatch.setattr(
            _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "model", "high")]
        )

        started = await manager.start("plan", "調査", str(tmp_path))
        explored = await manager.start_explore("探索", str(tmp_path))
        written = await manager.start_write("定型変更", str(tmp_path))
        shelled = await manager.start_shell("make test", str(tmp_path), "終了状態だけ")
        start_keys = {"session_id", "status", "label", "engine", "model", "effort", "model_type", "root_session_id"}
        assert started.keys() == start_keys
        assert explored.keys() == start_keys
        assert written.keys() == start_keys
        assert written["model_type"] == "write"
        assert shelled.keys() == start_keys

        session_id = str(started["session_id"])
        session = manager.sessions[session_id]
        assert (await manager.wait()).keys() == {
            "session_id",
            "status",
            "progress",
            "elapsed_seconds",
        }
        assert (await manager.send_message(session_id, "追加指示")).keys() == {"delivery", "label", "root_session_id"}
        session.turn_id = "turn-1"
        assert (await manager.kill(session_id, timeout=0)).keys() == {"status", "kill_requested"}

        terminal_id = str(explored["session_id"])
        terminal = manager.sessions[terminal_id]
        _complete(terminal, message="完了")
        assert (await manager.wait()).keys() == {
            "session_id",
            "status",
            "agent_message",
            "engine",
            "model",
            "effort",
            "model_type",
            "launch_identity",
            "observed_identity",
        }
        assert await manager.stop(terminal_id) == {}

        listed = await manager.list_sessions(include_terminated=True)
        assert listed.keys() == {"sessions", "root_session_id"}
        assert listed["root_session_id"] == "root-session"
        assert all(
            {"session_id", "status"}
            <= item.keys()
            <= {"session_id", "status", "engine", "model", "effort", "started_at", "updated_at", "seconds_since_activity"}
            for item in listed["sessions"]
        )


@pytest.mark.asyncio
async def test_send_message_tool_returns_same_root_as_list(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """MCPツール`send_message`の応答は、同じサーバーの`list`と同じ`root_session_id`を返す。

    再起動した会話の最初のMCP操作が`send_message`でも、PostToolUseがこの値で別名索引を書くため、
    欠けると`atk agents wait`が旧ルートを読んで結果を受け取れない。
    """
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        _install_backend(manager, "codex", FakeBackend(manager.sessions, "codex"))
        _complete(session, message="計画作成完了")
        manager.sessions[session.session_id] = session

        response = await mcp_tools.send_message(session.session_id, "実装開始")

        assert response["delivery"] == "reply_started"
        assert response["root_session_id"] == (await mcp_tools.list_sessions())["root_session_id"] == "root-session"


@pytest.mark.asyncio
async def test_list_returns_root_session_id_when_session_list_is_empty(tmp_path: pathlib.Path) -> None:
    """空一覧でもPostToolUseがルートsessionの索引を復旧できる値を返す。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        assert (await manager.list_sessions()) == {
            "sessions": [],
            "root_session_id": "root-session",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expects_next_action"), [("failed", True), ("running", False)])
async def test_start_response_adds_next_action_only_for_failed_start(
    status: str,
    expects_next_action: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動直後に失敗で終了した応答だけが、結果の受領と別候補での再起動を`next_action`で示す。"""
    manager = SimpleNamespace(
        launcher_session_id=lambda: None,
        start=AsyncMock(return_value={"session_id": "session", "status": status, "label": "担当"}),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    response = await mcp_tools.start(str(tmp_path), mode="delegate", prompt="調査", model_type="chores")

    if expects_next_action:
        assert "atk agents wait" in response["next_action"]
        assert "`model_type`" in response["next_action"]
    else:
        assert "next_action" not in response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delivery", "expects_next_action"),
    [("reply_failed", True), ("reply_ambiguous", True), ("reply_started", False), ("steered", False)],
)
async def test_send_message_response_adds_next_action_for_unconfirmed_reply(
    delivery: str,
    expects_next_action: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新しいturnを開始できなかったか確定できなかった配送は、`atk agents wait`での確認を`next_action`で示す。"""
    manager = SimpleNamespace(send_message=AsyncMock(return_value={"delivery": delivery, "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    response = await mcp_tools.send_message("3468feae-b2bf-4d67-ac55-3c40207e8b5b", "続行")

    assert response["delivery"] == delivery
    if expects_next_action:
        assert "atk agents wait" in response["next_action"]
    else:
        assert "next_action" not in response
