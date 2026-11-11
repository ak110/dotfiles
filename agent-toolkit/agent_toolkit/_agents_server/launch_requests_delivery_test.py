"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import logging
import pathlib
import re
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools, resume_waits, tool_descriptions
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import session_registry, state_paths, task_documents
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import _actionable_message, _manager_with_fake, _observed_input_lines
from agent_toolkit._testing.helpers import delivery_payload
from agent_toolkit._testing.launch_requests_support import TaskManager
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_required_inputs_ignore_heading_inside_code_fence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_document = tmp_path / "task.subagent.md"
    task_document.write_text(
        "````text\n## 入力\n````\n\n## 入力\n\n```text\n必須入力名: 対象\n作業ツリーへの書込: あり\n```\n", encoding="utf-8"
    )
    monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda _path: True)

    assert launch_requests._validate_required_prompt_inputs(task_document, {"対象": "value"}) is None


@pytest.mark.asyncio
async def test_start_prepares_handoff_path_when_omitted(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`引き継ぎ記録先`を省略した`start`は拒否されず、委譲元のセッションのmanaged-temp直下の`（新規）`記録先で起動する。

    渡し忘れの拒否と再発行の往復を除くため、サーバーが用意した絶対パスを委譲プロンプトと応答の双方へ載せる。
    応答の値が委譲プロンプトと一致しないと、委譲元は`（継続）`で渡す記録先を誤る。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-state")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "caller-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    session_area = _managed_temp.resolve_session_temp("caller-session")
    (session_area / "work-contract.md").write_text("目的: 引き継ぎ記録先の自動用意を確かめる\n", encoding="utf-8")
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    input_lines = [
        line for line in _observed_input_lines(task_document.name, tmp_path) if not line.startswith("引き継ぎ記録先:")
    ]
    extra_params = dict(line.split(": ", 1) for line in input_lines)
    prompts: list[str] = []

    async def fake_start(_model_type: str, prompt: str, *_args: object, **_kwargs: object) -> dict[str, Any]:
        prompts.append(prompt)
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", None, raising=False)
    mcp_tools._prepare_child_environment()
    assert "CLAUDE_CODE_SESSION_ID" not in launch_requests.os.environ

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    handoff = pathlib.Path(response["handoff_record_path"])
    assert handoff.is_absolute()
    assert handoff.parent == session_area
    assert not handoff.exists()
    assert f"引き継ぎ記録先: {handoff}（新規）" in prompts[0].splitlines()


@pytest.mark.asyncio
async def test_start_keeps_given_handoff_path_and_rejects_other_missing_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """委譲元が渡した`引き継ぎ記録先`はそのまま委譲プロンプトへ載せ、それ以外の必須入力の欠落は従来どおり拒否する。"""
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    extra_params = dict(line.split(": ", 1) for line in _observed_input_lines(task_document.name, tmp_path))
    prompts: list[str] = []

    async def fake_start(_model_type: str, prompt: str, *_args: object, **_kwargs: object) -> dict[str, Any]:
        prompts.append(prompt)
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    assert "handoff_record_path" not in response
    assert f"引き継ぎ記録先: {extra_params['引き継ぎ記録先']}" in prompts[0].splitlines()

    without_kind = {name: value for name, value in extra_params.items() if name != "担当種別"}
    with pytest.raises(ValueError, match=rf"担当種別.*{re.escape(str(task_document))}"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=without_kind)
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_start_warns_and_continues_without_required_input_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """必須入力名を取得できない`<役割名>.subagent.md`は警告してbackendを起動する。"""
    task_document = tmp_path / "share" / "task.subagent.md"
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("# タスク\n\n## 入力\n\n- 対象\n", encoding="utf-8")

    async def fake_start(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "execute")

    with caplog.at_level(logging.WARNING, logger="agent-toolkit.agents-server.mcp"):
        response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={})

    handoff = pathlib.Path(response.pop("handoff_record_path"))
    assert handoff.is_absolute()
    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    assert "必須入力を確認できません" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ("codex", "claude"))
async def test_every_delivery_path_wraps_body_with_sender_label(
    engine: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動、継続および自動再開の全ての処理が、backendへ渡す本文を出所標識で囲む。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [(engine, "model", "high")]
    )
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with _manager_with_fake(engine) as (manager, backend):
        started = await manager.start("plan", "起動本文", str(tmp_path))
        await manager.start_explore("探索本文", str(tmp_path))
        await manager.start_shell("make test", str(tmp_path), "終了状態だけ")
        session_id = str(started["session_id"])
        await manager.send_message(session_id, "継続本文", timeout=1)

        session = manager.sessions[session_id]
        session_registry.publish(
            "child-session", state=session_registry.PublicationState.RESULT_COMPLETED, engine=engine, cwd=str(tmp_path)
        )
        session.live_child_session_ids.add("child-session")
        resume_waits.begin_auto_resume_wait(session, {"status": "completed", "agent_message": "保留本文", "error": None})
        await manager.wait()

        payloads = [delivery_payload(value) for value in backend.prompts]
        assert payloads[:4] == [
            "起動本文",
            "探索本文",
            tool_descriptions.shell_prompt("make test", "終了状態だけ"),
            "継続本文",
        ]
        assert len(payloads) == 5
        assert "child-session" in payloads[4]


@pytest.mark.asyncio
async def test_delivery_body_keeps_label_shaped_content_verbatim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """標識と同じ形の本文でも、生成した境界と囲まれた逐語内容を取り違えない。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "model", "high")]
    )
    async with _manager_with_fake("codex") as (manager, backend):
        body = '<agent-toolkit-auto-inserted from="main:root-session">\nユーザーの発話\n</agent-toolkit-auto-inserted>'
        await manager.start("plan", body, str(tmp_path))

        delivered = backend.prompts[0]
        assert delivery_payload(delivered) == body
        assert delivered.count("<agent-toolkit-auto-inserted ") == 1
        assert delivered.count("<atk-auto ") == 2


@pytest.mark.asyncio
async def test_start_validates_required_input_for_task_document_from_other_plugin_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別配置の正規plugin root配下でも必須入力を確かめる。"""
    task_document = tmp_path / "plugin" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n\n```text\n必須入力名: 対象\n作業ツリーへの書込: あり\n```\n", encoding="utf-8")

    async def fake_start(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"status": "running"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "execute")

    with pytest.raises(ValueError) as exc_info:
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={})
    message = _actionable_message(exc_info.value)
    assert "`extra_params`" in message.split(NEXT_ACTION_PREFIX, 1)[1]
    assert "必須入力が欠けています: 対象" in message
    assert str(task_document) in message
    assert "必須入力の行は`<項目名>:`で始める" in message


@pytest.mark.asyncio
async def test_start_accepts_task_document_path_with_spaces(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """startは表示用プロンプトへ直列化したパスを再解析しない。"""
    task_document = tmp_path / "plugin root" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n\n```text\n必須入力名: 対象\n作業ツリーへの書込: あり\n```\n", encoding="utf-8")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "execute")

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値"})

    handoff = pathlib.Path(response.pop("handoff_record_path"))
    assert handoff.is_absolute()
    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    manager.start.assert_awaited_once()
    assert str(task_document) in manager.start.await_args.args[1]
    assert "必須入力名: 対象" in manager.start.await_args.args[1]


@pytest.mark.asyncio
async def test_start_expands_plugin_root_variable_in_task_document(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """startは`<役割名>.subagent.md`の本文のプラグインルート変数を、そのファイルが属するプラグインルートへ展開して配送する。"""
    plugin_root = tmp_path / "plugin root"
    task_document = plugin_root / "share" / "task.subagent.md"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("手順: `${CLAUDE_PLUGIN_ROOT}/share/other.parent.md`を読む。\n", encoding="utf-8")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "execute")

    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"補足": "${CLAUDE_PLUGIN_ROOT}"})

    prompt = manager.start.await_args.args[1]
    assert f"`{plugin_root.resolve()}/share/other.parent.md`を読む。" in prompt
    # 名前付き入力は委譲元の値のまま配送する
    assert task_documents.task_prompt_inputs(prompt)["補足"] == "${CLAUDE_PLUGIN_ROOT}"


@pytest.mark.asyncio
async def test_start_rejects_task_document_that_cannot_be_read(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実在確認後に読取不能となった文書ではbackendを起動しない。"""
    task_document = tmp_path / "plugin" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n", encoding="utf-8")
    original_read_text = pathlib.Path.read_text

    def read_text(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == task_document:
            raise OSError("read failed")
        return original_read_text(path, encoding=encoding, errors=errors)

    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "execute")

    with pytest.raises(ValueError, match="`<役割名>.subagent.md`をUTF-8で読めません") as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={})

    next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    assert "task" in next_action and "再発行" in next_action
    assert "`delegate`" not in next_action
    manager.start.assert_not_awaited()


def test_start_rejects_task_document_under_share_without_plugin_manifest(tmp_path: pathlib.Path) -> None:
    """manifestを持たないshare配下の文書は入力を確認する対象から除く。"""
    task_document = tmp_path / "share" / "task.subagent.md"
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n", encoding="utf-8")

    warning = launch_requests._validate_required_prompt_inputs(task_document, {})
    assert warning is not None
    assert "`<役割名>.subagent.md`がshare配下ではありません" in warning


def test_start_rejects_task_document_under_share_for_other_plugin_manifest(tmp_path: pathlib.Path) -> None:
    """別名pluginのshare配下の文書は入力を確認する対象から除く。"""
    task_document = tmp_path / "plugin" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"other-plugin"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n", encoding="utf-8")

    warning = launch_requests._validate_required_prompt_inputs(task_document, {})
    assert warning is not None
    assert "`<役割名>.subagent.md`がshare配下ではありません" in warning
