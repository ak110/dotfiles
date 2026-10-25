"""`_agents_server/mcp_tools.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import agent_toolkit.agents_server_mcp as entry_script
from agent_toolkit._agents_server import (
    child_environment,
    launch_requests,
    logging_config,
    mcp_tools,
    session_handoff,
    shared_roots,
    state,
    status_file,
    tool_descriptions,
    tool_names,
)
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import mcp_environment, session_registry, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    _MODE_INPUT_CASES,
    FakeBackend,
    UnavailableStartBackend,
    _complete,
    _install_backend,
    _manager_with_fake,
    _observed_input_lines,
    _observed_input_params,
    _recording_candidates,
    _start_tool,
    _without_root,
)
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_new_request_and_handoff_preserve_required_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """新要求を配送し、後任にも原要求と追送を保持したまま定型継続できる。"""
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        conversation = tmp_path / "conversation.md"
        conversation.write_text("外部の会話抽出が返した記録\n", encoding="utf-8")
        record = tmp_path / "record.jsonl"
        record.write_text("{}\n", encoding="utf-8")

        def extract_context(source: state.SessionResumeState, **_kwargs: Any) -> str:
            # 外部CLIの会話抽出だけを代替し、保持情報からの引き継ぎ本文生成は実処理を使う。
            return session_handoff.compose_prompt(source, conversation, (record,))

        monkeypatch.setattr(session_handoff, "prepare_prompt", extract_context)
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path="exec-review",
            extra_params=_observed_input_params("exec-review.subagent.md", tmp_path),
        )
        await mcp_tools.send_message(started["session_id"], "追加要求: 新しい文書の差分も確認する")
        assert "新しい文書の差分" in backend.prompts[-1]
        for _ in range(2):
            await mcp_tools.send_message(started["session_id"], "現在の作業を続行する")
        assert backend.send_calls == 3
        document = launch_requests._SHARE_DIRECTORY / "exec-review.subagent.md"
        with pytest.raises(ValueError, match="役割文書が全文再掲"):
            await mcp_tools.send_message(started["session_id"], document.read_text(encoding="utf-8"))
        assert backend.send_calls == 3
        _complete(manager.sessions[started["session_id"]])
        successor = await mcp_tools.start(handoff_from=started["session_id"])
        received = manager.sessions[successor["session_id"]].prompt
        assert "レビュー基準: 計画" in received
        assert "追加要求: 新しい文書の差分も確認する" in received
        await mcp_tools.send_message(successor["session_id"], instruction="再レビューせよ", extra_params={"round": "2"})
        assert backend.prompts[-1] == launch_requests.wrap_delivery_body("再レビューせよ\nround: 2")


def test_main_strips_conversation_context_before_backend_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP起動は全backendの継承元を整え、接続設定と引き継ぎ元snapshotを保持する。"""
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", None, raising=False)
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    for name in mcp_environment._NON_INHERITABLE_NAMES:
        monkeypatch.setenv(name.upper(), "parent-session")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    observed: dict[str, str] = {}

    def check_dependencies() -> None:
        observed.update(os.environ)

    monkeypatch.setattr(claude_backend, "check_dependencies", check_dependencies)
    assert entry_script.main(["--check-dependencies"]) == 0
    assert not {name.casefold() for name in observed} & mcp_environment._NON_INHERITABLE_NAMES
    assert "ANTHROPIC_AUTH_TOKEN" not in observed
    inherited = child_environment.inherited(observed)
    assert inherited["ANTHROPIC_AUTH_TOKEN"] == "test-token"
    assert not {name.casefold() for name in inherited} & mcp_environment._NON_INHERITABLE_NAMES
    assert launch_requests._HANDOFF_SESSION == ("parent-session",)


def test_backend_imports_survive_plugin_path_removal(tmp_path: pathlib.Path) -> None:
    """MCP初期化後にプラグイン配置を除去しても両backendを生成できる。"""
    source_dir = pathlib.Path(__file__).resolve().parents[1]
    script_dir = tmp_path / "plugin" / "scripts"
    script_dir.mkdir(parents=True)
    share_dir = tmp_path / "plugin" / "share"
    share_dir.mkdir()
    shutil.copyfile(source_dir.parent / "share" / "rules-subagent.md", share_dir / "rules-subagent.md")
    paths = (
        "agents_server_mcp.py",
        "_agents_server/mcp_tools.py",
        "_agents_server/mcp_transport.py",
        "_agents_server/manager.py",
        "_agents_server/launch_requests.py",
        "_agents_server/responses.py",
        "_agents_server/tool_descriptions.py",
        "_agents_server/engine_availability.py",
        "_agents_server/codex.py",
        "_agents_server/claude.py",
        "_agents_server/antigravity.py",
        "_common/process_tree.py",
        "_agents_server/state.py",
        "_agents_server/status_file.py",
        "_agents_server/file_operations.py",
        "_common/session_registry.py",
        "_common/atomic_file.py",
        "_atk/config.py",
        "_atk/help_text.py",
        "_common/inherited_venv.py",
        "_common/delegated_session.py",
        "_plan/locations.py",
        "_common/wait_schedule.py",
    )
    for relative_path in paths:
        destination = script_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_dir / relative_path, destination)
    for package in ("_agents_server", "_common", "_atk", "_plan"):
        (script_dir / package / "__init__.py").write_text("", encoding="utf-8")

    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import shutil, sys\n"
                "from pathlib import Path\n"
                "script_dir = Path(sys.argv[1])\n"
                "sys.path.insert(0, str(script_dir))\n"
                "import agents_server_mcp as entry_script\n"
                "assert 'claude_agent_sdk' not in sys.modules\n"
                "sys.path.remove(str(script_dir))\n"
                "shutil.rmtree(script_dir)\n"
                "assert entry_script.mcp_tools._MANAGER._backend('codex').__class__.__name__ == 'AppServerManager'\n"
                "assert entry_script.mcp_tools._MANAGER._backend('claude').__class__.__name__ == 'ClaudeServerManager'\n"
                "assert entry_script.mcp_tools._MANAGER._backend('agy').__class__.__name__ == 'AntigravityManager'\n"
            ),
            str(script_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


def test_start_operations_match_registered_start_tools() -> None:
    """子sessionを生成する登録ツールの集合が、判定箇所が参照する起動ツールの定義と一致する。

    起動ツールを追加して定義を更新しない変更では、`atk run-script session-review-evidence`やフックがその委譲を認識しない。
    未分類のツールを登録した変更も、子sessionを生成するかの分類を求めるため失敗させる。
    """
    non_start_operations = {"send_message", "kill", "list", "show", "stop"}
    registered = set(mcp_tools.mcp._tool_manager._tools)
    assert registered - non_start_operations == tool_names.START_OPERATIONS


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


def test_public_start_schema_delivers_explore_capabilities() -> None:
    """登録済みの公開schemaが起動前に共通の能力説明を届ける。"""
    start_tool = _start_tool()
    capabilities = tool_descriptions.EXPLORE_CAPABILITIES
    assert capabilities in start_tool.description[:4096]
    assert capabilities in start_tool.parameters["properties"]["mode"]["description"]


@pytest.mark.asyncio
async def test_session_label_prefers_argument_over_generated_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """識別名は引数の指定を優先し、省略時は依頼本文またはコマンド名から導く。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type, **_kwargs: [("codex", "model", "high")],
    )
    async with _manager_with_fake("codex") as (manager, _):
        named = await manager.start("plan", "対象の調査\n詳細", str(tmp_path), label="lane-02")
        unnamed = await manager.start("plan", "対象の調査\n詳細", str(tmp_path))
        shell_default = await manager.start_shell("git status --short", str(tmp_path), "終了コードだけを返す")
        shell_named = await manager.start_shell(
            "git status --short",
            str(tmp_path),
            "終了コードだけを返す",
            label="作業ツリーの差分",
        )

        for response, expected in (
            (named, "lane-02"),
            (unnamed, "対象の調査"),
            (shell_default, "shell-git"),
            (shell_named, "作業ツリーの差分"),
        ):
            assert response["label"] == expected
            assert (await manager.show_session(response["session_id"]))["label"] == expected


@pytest.mark.parametrize("label", ["", "   "])
@pytest.mark.asyncio
async def test_empty_session_label_falls_back_to_the_generated_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    label: str,
) -> None:
    """空になる識別名の指定は未指定と同じ扱いとし、識別名の列を空のままにしない。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type, **_kwargs: [("codex", "model", "high")],
    )
    async with _manager_with_fake("codex") as (manager, _):
        started = await manager.start("plan", "対象の調査\n詳細", str(tmp_path), label=label)
        shell = await manager.start_shell("git status --short", str(tmp_path), "終了コードだけを返す", label=label)

        assert started["label"] == (await manager.show_session(started["session_id"]))["label"] == "対象の調査"
        assert shell["label"] == (await manager.show_session(shell["session_id"]))["label"] == "shell-git"


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
                cwd, mode="delegate", prompt="対象の調査\n詳細", model_type="execute", label="lane-02"
            ),
            "別の調査": await mcp_tools.start(cwd, mode="delegate", prompt="別の調査\n詳細", model_type="execute"),
            "空白の調査": await mcp_tools.start(cwd, mode="delegate", prompt="空白の調査", model_type="execute", label="   "),
            "explore": await mcp_tools.start(cwd, mode="explore", prompt="探索"),
            "write-awi": await mcp_tools.start(cwd, mode="write", prompt="定型変更", label="write-awi"),
            "shell-git": await mcp_tools.start(
                cwd, mode="shell", command="git status --short", summary_policy="終了コードだけ"
            ),
            "lane-07-exec": await mcp_tools.start(cwd, subagent_md_path="exec", extra_params=task_params),
        }

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
            switched = await mcp_tools.start(cwd, mode="delegate", prompt="切替", model_type="execute", label="switched")
            assert switched["excluded_candidates"]
            assert switched["label"] == (await switching.show_session(switched["session_id"]))["label"] == "switched"

            _install_backend(switching, "claude", UnavailableStartBackend(switching.sessions, "claude"))
            del claude
            failed = await mcp_tools.start(cwd, mode="delegate", prompt="失敗", model_type="execute", label="all-failed")
            assert failed["status"] == "failed"
            assert failed["label"] == (await switching.show_session(failed["session_id"]))["label"] == "all-failed"


@pytest.mark.asyncio
async def test_send_message_returns_label_of_delivered_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開`send_message`の応答が、配送した宛先sessionの担当名を`label`で返す。

    実行中turnへのsteer、終端後のreply、保持期限の経過後と破棄後の再開の各配送で、
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

    delegated = await mcp_tools.start(str(tmp_path), mode="delegate", prompt="本文", model_type="execute")
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
            <= {"session_id", "status", "started_at", "updated_at", "seconds_since_activity"}
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


def test_main_strips_launcher_venv_from_child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動処理が後続工程より前に、起動元ツールの仮想環境を`VIRTUAL_ENV`と`PATH`から取り除く。"""
    venv_root = "/tmp/agents-server-environment"
    monkeypatch.setenv("VIRTUAL_ENV", venv_root)
    monkeypatch.setenv("PATH", os.pathsep.join([f"{venv_root}/bin", "/usr/local/bin", "", "/usr/bin"]))
    observed: dict[str, str | None] = {}

    def check_dependencies() -> None:
        observed["virtual_env"] = os.environ.get("VIRTUAL_ENV")
        observed["path"] = os.environ.get("PATH")

    monkeypatch.setattr(claude_backend, "check_dependencies", check_dependencies)
    assert entry_script.main(["--check-dependencies"]) == 0
    assert observed["virtual_env"] is None
    assert observed["path"] == os.pathsep.join(["/usr/local/bin", "", "/usr/bin"])


def test_dependency_check_cli_does_not_start_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """依存の確認を指定した場合はMCP stdioを起動しない。"""
    calls: list[bool] = []
    monkeypatch.setattr(claude_backend, "check_dependencies", lambda: calls.append(True))
    monkeypatch.setattr(mcp_tools.mcp, "run", lambda **_kwargs: pytest.fail("MCPを起動してはいけない"))

    assert entry_script.main(["--check-dependencies"]) == 0
    assert calls == [True]


def test_dependency_check_cli_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """依存の確認処理で発生した例外を捕捉せず呼び出し元へ伝える。"""

    def fail_check() -> None:
        raise ImportError("claude-agent-sdk is unavailable")

    monkeypatch.setattr(claude_backend, "check_dependencies", fail_check)
    with pytest.raises(ImportError, match="claude-agent-sdk is unavailable"):
        entry_script.main(["--check-dependencies"])


def test_main_persists_startup_and_exit_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """依存の確認を含む起動初期の診断を状態ディレクトリへ永続化する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(claude_backend, "check_dependencies", lambda: None)

    assert entry_script.main(["--check-dependencies"]) == 0

    log_path = tmp_path / "agents-server.log"
    content = log_path.read_text(encoding="utf-8")
    assert "agents_serverを起動します: mode=check-dependencies" in content
    assert "agents_serverが正常終了しました: mode=check-dependencies" in content
    handlers = logging.getLogger("agent-toolkit.agents-server").handlers
    assert not any(isinstance(handler, logging_config._LogFileHandler) for handler in handlers)  # pylint: disable=protected-access  # noqa: SLF001


@pytest.mark.asyncio
async def test_mcp_lifespan_persists_activate_and_close_milestones(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """managerのactivate前後とclose前後を永続化する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    mcp_tools._configure_logging()
    monkeypatch.setattr(mcp_tools._MANAGER, "activate_async", AsyncMock())
    monkeypatch.setattr(mcp_tools._MANAGER, "close", AsyncMock())

    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        pass

    content = (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert "manager activateを開始します" in content
    assert "manager activateが完了しました" in content
    assert "manager closeを開始します" in content
    assert "manager closeが完了しました" in content


@pytest.mark.asyncio
async def test_mcp_lifespan_persists_activate_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """manager activateの開始と失敗を例外診断とともに永続化する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    mcp_tools._configure_logging()

    async def fail_activate() -> None:
        raise RuntimeError("activate failed")

    monkeypatch.setattr(mcp_tools._MANAGER, "activate_async", fail_activate)

    with pytest.raises(RuntimeError, match="activate failed"):
        async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
            pytest.fail("activate失敗後にlifespanへ入ってはいけない")

    content = (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert "manager activateを開始します" in content
    assert "manager activateに失敗しました: stage=manager_activate" in content
    assert "RuntimeError: activate failed" in content


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expects_next_action"), [("failed", True), ("running", False)])
async def test_start_response_adds_next_action_only_for_failed_start(
    status: str,
    expects_next_action: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動直後に失敗で終端した応答だけが、結果の受領と別候補での再起動を`next_action`で示す。"""
    manager = SimpleNamespace(
        launcher_session_id=lambda: None,
        start=AsyncMock(return_value={"session_id": "session", "status": status, "label": "担当"}),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    response = await mcp_tools.start(str(tmp_path), mode="delegate", prompt="調査", model_type="execute")

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


@pytest.mark.asyncio
async def test_start_tools_accept_model_type_override(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`model_type`を渡した起動は工程別設定の代わりにその値を使い、各ツール固有の起動区分を保つ。"""
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        override = "codex:model/high"
        task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"

        responses = {
            "delegate": await mcp_tools.start(
                str(tmp_path),
                subagent_md_path=str(task_document),
                extra_params=_observed_input_params(task_document.name, tmp_path),
                model_type=override,
            ),
            "explore": await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査", model_type=override),
            "shell": await mcp_tools.start(
                str(tmp_path), mode="shell", command="make test", summary_policy="終了状態", model_type=override
            ),
            "write": await mcp_tools.start(str(tmp_path), mode="write", prompt="起草", model_type=override),
        }

        assert requested == [override] * 4
        for launch_kind, response in responses.items():
            shown = await manager.show_session(response["session_id"])
            assert shown["model_type"] == override
            assert shown["launch_kind"] == launch_kind


@pytest.mark.asyncio
async def test_start_tools_use_task_settings_without_override(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`model_type`を省略した起動は各ツールの工程別設定を使う。"""
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

        await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査")
        await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査", model_type="review")
        await mcp_tools.start(str(tmp_path), mode="shell", command="make test", summary_policy="終了状態")
        await mcp_tools.start(str(tmp_path), mode="write", prompt="起草")

        assert requested == ["chores_fast", "review", "chores_fast", "write"]


@pytest.mark.asyncio
async def test_start_label_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """label省略時は凡例どおりの識別名を生成し、明示したlabelはその値を使う。"""
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        exec_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
        exec_params = {**_observed_input_params(exec_document.name, tmp_path), "レーン識別子": "lane-01"}
        explain_document = launch_requests._SHARE_DIRECTORY / "pick-wi-explain.subagent.md"
        explain_params = _observed_input_params(explain_document.name, tmp_path)

        labels = {
            "lane": await mcp_tools.start(str(tmp_path), subagent_md_path=str(exec_document), extra_params=exec_params),
            "explain": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params
            ),
            "named": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params, label="選定理由の説明"
            ),
            "blank": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params, label="   "
            ),
            "shell": await mcp_tools.start(str(tmp_path), mode="shell", command="make test", summary_policy="終了状態"),
            "shell_path": await mcp_tools.start(
                str(tmp_path), mode="shell", command="/usr/bin/git status", summary_policy="終了状態"
            ),
            "explore": await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査\n詳細"),
            "write": await mcp_tools.start(str(tmp_path), mode="write", prompt="起草\n詳細"),
        }

        shown = {key: (await manager.show_session(response["session_id"]))["label"] for key, response in labels.items()}
        assert shown == {
            "lane": "lane-01-exec",
            "explain": "pick-wi-explain",
            "named": "選定理由の説明",
            "blank": "pick-wi-explain",
            "shell": "shell-make",
            "shell_path": "shell-git",
            "explore": "explore",
            "write": "write",
        }


@pytest.mark.parametrize(("mode", "inputs", "named"), _MODE_INPUT_CASES)
@pytest.mark.asyncio
async def test_start_rejects_missing_and_mixed_mode_inputs_before_creating_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    mode: str,
    inputs: dict[str, Any],
    named: str,
) -> None:
    """modeが必要とする入力の欠落と受理しない入力の混在を、委譲先の起動前に修正可能な診断で拒否する。"""
    manager = SimpleNamespace(
        start=AsyncMock(),
        start_explore=AsyncMock(),
        start_write=AsyncMock(),
        start_shell=AsyncMock(),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ToolError) as raised:
        await mcp_tools.mcp.call_tool("start", {"cwd": str(tmp_path), "mode": mode, **inputs})

    body = str(raised.value)
    assert named in body
    assert "委譲先は起動していない" in body
    assert NEXT_ACTION_PREFIX in body
    assert "最小の呼び出し例" in body
    for method in (manager.start, manager.start_explore, manager.start_write, manager.start_shell):
        method.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_routes_each_mode_to_launch_kind_model_and_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開境界から各modeの`launch_kind`、省略時のmodel_type、用途およびlabelがManagerへ届く。"""
    requested: list[str] = []

    def candidates(model_type: str) -> list[tuple[str, str, str]]:
        requested.append(model_type)
        return [("codex", "model", "high")]

    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", candidates)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
        calls: dict[str, dict[str, Any]] = {
            "task": {
                "subagent_md_path": str(task_document),
                "extra_params": _observed_input_params(task_document.name, tmp_path),
            },
            "delegate": {"mode": "delegate", "prompt": "監査\n詳細", "model_type": "execute"},
            "explore": {"mode": "explore", "prompt": "調査", "label": "explore-対象"},
            "write": {"mode": "write", "prompt": "起草"},
            "shell": {"mode": "shell", "command": "make test", "summary_policy": "終了状態"},
        }

        shown = {}
        for mode, arguments in calls.items():
            response = await mcp_tools.start(str(tmp_path), **arguments)
            shown[mode] = await manager.show_session(response["session_id"])

        assert requested == [
            launch_requests._TASK_MODEL_TYPES[task_document.name],
            "execute",
            "chores_fast",
            "write",
            "chores_fast",
        ]
        assert [call[2] for call in backend.start_calls] == ["delegate", "delegate", "explore", "write", "shell"]
        assert {mode: item["label"] for mode, item in shown.items()} == {
            "task": "lane-01-exec",
            "delegate": "監査",
            "explore": "explore-対象",
            "write": "write",
            "shell": "shell-make",
        }


@pytest.mark.asyncio
async def test_send_message_tool_returns_previous_result(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """未回収の結果を持つ終端済みsessionへ送ると、MCPの応答がその結果を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        _complete(session, message="計画作成完了")
        manager.sessions[session.session_id] = session

        response = await mcp_tools.send_message(session.session_id, "実装開始")

        assert _without_root(response) == {
            "delivery": "reply_started",
            "label": "",
            "previous_result": {"status": "completed", "agent_message": "計画作成完了"},
        }


async def _start_exec_review_session(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    """実行レビュー担当の役割文書で偽のcodex sessionを公開`start`から起動し、継続入力の検証に使う。"""
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path="exec-review",
            extra_params=_observed_input_params("exec-review.subagent.md", tmp_path),
        )
        return manager, backend, started


@pytest.mark.asyncio
async def test_followup_rejects_extra_lines_before_delivery(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """定型追送へ説明と初回入力を加えると、公開send_messageは配送を拒否し履歴を保つ。"""
    manager, backend, started = await _start_exec_review_session(monkeypatch, tmp_path)
    session = manager.sessions[started["session_id"]]
    count = len(backend.prompts)
    try:
        for extra in ("検証結果: 成功", "担当別の根拠を説明します", "レビュー基準: 計画"):
            with pytest.raises(ToolError, match="入力|行") as raised:
                await mcp_tools.mcp.call_tool(
                    "send_message",
                    {
                        "session_id": session.session_id,
                        "prompt": "再レビューせよ\nround: 2\n" + extra,
                    },
                )
            assert extra.partition(": ")[0] in str(raised.value)
            assert "最小の再実行形" in str(raised.value)
            assert len(backend.prompts) == count
            assert not session.followup_prompts
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_continuation_input_errors_return_reason_and_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """継続入力の形式不成立は、配送せずに理由と次の操作をツールのエラー応答で返す。

    PreToolUseのhookは入力形式を判定しないため、修正案内はこの応答だけが担う。
    """
    manager, backend, started = await _start_exec_review_session(monkeypatch, tmp_path)
    session_id = started["session_id"]
    count = len(backend.prompts)
    cases = [
        ("send_message", {"session_id": session_id, "prompt": "続行する", "instruction": "再レビューせよ"}, "排他的"),
        ("send_message", {"session_id": session_id}, "排他的"),
        ("send_message", {"session_id": session_id, "instruction": "宣言されていない指示"}, "宣言された継続指示ではありません"),
        ("send_message", {"session_id": session_id, "prompt": "   "}, "prompt must be a non-empty string"),
        ("send_message", {"session_id": "", "prompt": "続行する"}, "invalid session_id"),
        ("kill", {"session_id": ""}, "invalid session_id"),
    ]
    try:
        for tool, arguments, reason in cases:
            with pytest.raises(ToolError) as raised:
                await mcp_tools.mcp.call_tool(tool, arguments)
            message = str(raised.value)
            assert reason in message, (tool, arguments, message)
            assert NEXT_ACTION_PREFIX in message, (tool, arguments, message)
            assert len(backend.prompts) == count
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_followup_preserves_required_and_updated_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """構造化・自由本文は必要な制御値と同じパスの更新を保持する。"""
    manager, backend, started = await _start_exec_review_session(monkeypatch, tmp_path)
    contract = tmp_path / "更新 契約.yaml"
    contract.write_text("clauses: []\n", encoding="utf-8")
    try:
        for round_number in ("2", "3"):
            contract.write_text(f"round: {round_number}\n", encoding="utf-8")
            await mcp_tools.mcp.call_tool(
                "send_message",
                {
                    "session_id": started["session_id"],
                    "instruction": "再レビューせよ",
                    "extra_params": {"round": round_number, "review_contract": str(contract)},
                },
            )
            assert f"round: {round_number}" in backend.prompts[-1]
            assert str(contract) in backend.prompts[-1]
        await mcp_tools.send_message(started["session_id"], "再レビューせよ\nround: 4")
        assert "round: 4" in backend.prompts[-1]
        await mcp_tools.send_message(
            started["session_id"], instruction="再レビューせよ", extra_params={"round": "5", "rewrite_map": str(contract)}
        )
        assert f"rewrite_map: {contract}" in backend.prompts[-1]
        direct_contract = "目的: 必要な入力を保持する\n\n対象: 現在の差分"
        await mcp_tools.send_message(
            started["session_id"],
            instruction="再レビューせよ",
            extra_params={"round": "6", "review_contract": direct_contract},
        )
        expected = "再レビューせよ\nround: 6\nreview_contract:\n  目的: 必要な入力を保持する\n  \n  対象: 現在の差分"
        assert backend.prompts[-1] == launch_requests.wrap_delivery_body(expected)
        await mcp_tools.send_message(started["session_id"], expected.replace("round: 6", "round: 7"))
        assert backend.prompts[-1] == launch_requests.wrap_delivery_body(expected.replace("round: 6", "round: 7"))
        before = backend.send_calls
        for params in (
            {},
            {"round": "8", "rewrite_map": str(contract) + " 説明"},
            {"round": "8", "review_contract": str(contract) + " 説明"},
        ):
            with pytest.raises(ToolError):
                await mcp_tools.mcp.call_tool(
                    "send_message",
                    {
                        "session_id": started["session_id"],
                        "instruction": "再レビューせよ",
                        "extra_params": params,
                    },
                )
        assert backend.send_calls == before
    finally:
        await manager.close()


@pytest.mark.parametrize(
    ("role", "instruction", "params"),
    [
        ("exec", "実装開始", {}),
        ("exec", "実装なし", {}),
        ("exec", "レビュー指摘の対応をせよ", {"レビュー指摘管理表": "@table"}),
        ("exec", "レビュー指摘の対応をせよ", {"レビュー指摘管理表": "@tables"}),
        (
            "exec",
            "統合開始",
            {
                "統合区分": "マージなし",
                "レーン開始時刻": "2026-10-18T19:30:00+09:00",
                "実行レビュー済みHEAD": "なし",
                "統合先worktree": "/repo",
                "統合先branch": "develop",
                "計画ファイル名一覧": "[]",
                "AWI終端区分": "adopt",
            },
        ),
        ("add-wi", "保存開始", {"要求識別子": "request-1"}),
    ],
)
@pytest.mark.asyncio
async def test_declared_role_continuations_keep_required_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    role: str,
    instruction: str,
    params: dict[str, str],
) -> None:
    """各役割の継続指示は必要入力だけを配送し、書込担当一覧は起動応答の実sessionで更新する。"""
    table = tmp_path / "review.tsv"
    if params.get("レビュー指摘管理表") in ("@table", "@tables"):
        table.write_text("指摘の記録\n", encoding="utf-8")
        other_table = tmp_path / "別計画の review.tsv"
        other_table.write_text("別計画の指摘\n", encoding="utf-8")
        value = str(table) if params["レビュー指摘管理表"] == "@table" else json.dumps([str(table), str(other_table)])
        params = {**params, "レビュー指摘管理表": value}
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=role,
            extra_params=_observed_input_params(f"{role}.subagent.md", tmp_path),
        )
        if role == "exec":
            initial_writers = _observed_input_params("exec.subagent.md", tmp_path)["書込担当一覧"]
            assert f"書込担当一覧: {initial_writers}" in backend.prompts[-1]
            writers = json.dumps([*json.loads(initial_writers), started["session_id"]])
            await mcp_tools.send_message(started["session_id"], f"書込担当一覧: {writers}")
            assert f"書込担当一覧: {writers}" in backend.prompts[-1]
            if instruction == "統合開始":
                params = {**params, "書込担当一覧": writers}
                count = len(backend.prompts)
                with pytest.raises(ToolError, match="書込担当一覧"):
                    await mcp_tools.mcp.call_tool(
                        "send_message",
                        {
                            "session_id": started["session_id"],
                            "instruction": instruction,
                            "extra_params": {key: value for key, value in params.items() if key != "書込担当一覧"},
                        },
                    )
                assert len(backend.prompts) == count
        await mcp_tools.mcp.call_tool(
            "send_message",
            {
                "session_id": started["session_id"],
                "instruction": instruction,
                "extra_params": params,
            },
        )
        expected = "\n".join([instruction, *(f"{name}: {value}" for name, value in params.items())])
        assert backend.prompts[-1] == launch_requests.wrap_delivery_body(expected)
        if role == "exec" and instruction == "レビュー指摘の対応をせよ":
            await mcp_tools.mcp.call_tool("send_message", {"session_id": started["session_id"], "prompt": expected})
            assert backend.prompts[-1] == launch_requests.wrap_delivery_body(expected)
            session = manager.sessions[started["session_id"]]
            received = len(backend.prompts)
            history = session.followup_prompts
            missing = str(tmp_path / "存在しない表.tsv")
            for invalid_params in (
                {**params, "レビュー指摘管理表": json.dumps([str(table), missing])},
                {**params, "round": "1"},
            ):
                with pytest.raises(ToolError) as raised:
                    await mcp_tools.mcp.call_tool(
                        "send_message",
                        {"session_id": started["session_id"], "instruction": instruction, "extra_params": invalid_params},
                    )
                assert "最小の再実行形" in str(raised.value)
                assert missing in str(raised.value) if "round" not in invalid_params else "round" in str(raised.value)
                assert len(backend.prompts) == received
                assert session.followup_prompts == history
            for extra in ("検証結果: 成功", "担当別根拠を補足する"):
                with pytest.raises(ToolError) as raised:
                    await mcp_tools.mcp.call_tool(
                        "send_message", {"session_id": started["session_id"], "prompt": expected + "\n" + extra}
                    )
                assert extra.partition(": ")[0] in str(raised.value)
                assert "最小の再実行形" in str(raised.value)
                assert len(backend.prompts) == received
                assert session.followup_prompts == history


def test_serve_records_exception_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """stdio起動が例外で終わると、起動単位の終了診断へ例外型とメッセージを記録し、例外を呼出元へ伝える。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", None)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", None)
    for name in ("CLAUDE_CODE_SESSION_ID", logging_config.LAUNCH_RECORD_ENV):
        monkeypatch.delenv(name, raising=False)

    def fail(**_kwargs: Any) -> None:
        raise RuntimeError("transport broke")

    monkeypatch.setattr(mcp_tools.mcp, "run", fail)
    with pytest.raises(RuntimeError, match="transport broke"):
        mcp_tools.serve()

    [path] = logging_config.instance_directory(tmp_path).glob("*.jsonl")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["started", "exited"]
    assert events[1] | {"at": None} == {
        "at": None,
        "event": "exited",
        "writer": "server",
        "outcome": "exception",
        "exception_type": "RuntimeError",
        "message": "transport broke",
    }
    # 終えた起動単位は同じプロセスの後続の記録へ書かない。
    assert logging_config.current_server_instance() is None
    assert session_registry.owner_server_instance() is None
