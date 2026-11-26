from __future__ import annotations

import json
import pathlib
import socket
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_toolkit._agents_server import backends, mcp_tools
from agent_toolkit._atk.wi import repo
from agent_toolkit._atk.wi.uwi_body import ANSWER_MARKER
from agent_toolkit._testing.agents_server_support import _complete, _manager_with_fake, _observed_input_params

pytestmark = pytest.mark.usefixtures("agents_server_isolation")

_MODEL = "codex:explicit/high"
_OWNER = "trusted-session"
_PROOF = f"session:{_OWNER}:1"
_WI = "20261126-224649-001.md"
_REPO = "github.com/example/project"


def _lane_inputs(root: pathlib.Path, *wis: str) -> dict[str, str]:
    params = _observed_input_params("exec.subagent.md", root)
    selection = root / "selection.yaml"
    rows = [{"WI": wi, "レーン": params["レーン識別子"]} for wi in wis]
    selection.write_text(json.dumps({"選定": rows}, ensure_ascii=False), encoding="utf-8")
    return {**params, "選定結果の出力先ファイル": str(selection)}


def _human(text: str) -> dict[str, Any]:
    return {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}


def _record(monkeypatch: pytest.MonkeyPatch, manager: Any, path: pathlib.Path, *, engine: str = "claude") -> Mock:
    context = Mock(
        return_value={
            "status": "resolved",
            "launcher_session_id": _OWNER,
            "record_engine": engine,
            "record_path": str(path),
        },
    )
    monkeypatch.setattr(manager, "launcher_record_context", context)
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    return context


def _save(path: pathlib.Path, *records: dict[str, Any]) -> None:
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


async def _instruction(inputs: dict[str, Any]) -> str:
    with pytest.raises(ToolError, match="委譲先は起動していない") as rejected:
        await mcp_tools.mcp.call_tool("start", inputs)
    return "モデル指定: " + str(rejected.value).rsplit("モデル指定: ", 1)[-1]


@pytest.mark.parametrize(
    "invalid",
    ["implicit", "negative", "mention", "other-session", "other-target", "agent-summary", "expanded", "cancelled"],
)
@pytest.mark.asyncio
async def test_user_override_refuses_nonmatching_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, invalid: str
) -> None:
    record = tmp_path / "session.jsonl"
    inputs: dict[str, Any] = {
        "cwd": str(tmp_path),
        "mode": "delegate",
        "prompt": "画面の品質を判定する",
        "model_type": _MODEL,
        "model_override": "user_instruction",
        "model_override_evidence": _PROOF,
    }
    async with _manager_with_fake("codex") as (manager, backend):
        _record(monkeypatch, manager, record)
        approved = await _instruction(inputs)
        entries = [_human(approved)]
        if invalid == "implicit":
            entries = [_human("作り手と異なるモデルで画面を判定する")]
        elif invalid == "negative":
            entries = [_human(approved + " は使わない")]
        elif invalid == "mention":
            entries = [_human("候補例: " + approved)]
        elif invalid == "other-session":
            inputs["model_override_evidence"] = "session:another-session:1"
        elif invalid == "other-target":
            inputs["prompt"] = "設定ファイルを調べる"
        elif invalid == "agent-summary":
            entries = [{"type": "assistant", "message": {"content": approved}}]
        elif invalid == "expanded":
            inputs["model_type"] += ",codex:unapproved/high"
        elif invalid == "cancelled":
            entries.append(_human("やはり上書きは取り消して、設定を使う。"))
        _save(record, *entries)
        with pytest.raises(ToolError, match="役割起動ではmodel_typeを省き、自由本文では目的に合う工程名"):
            await mcp_tools.mcp.call_tool("start", inputs)
        assert not backend.start_calls


@pytest.mark.parametrize("kind", ["session", "codex", "wi", "answer", "write", "plain"])
@pytest.mark.asyncio
async def test_public_start_accepts_scoped_human_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, kind: str
) -> None:
    record = tmp_path / "session.jsonl"
    model = "write" if kind == "write" else _MODEL
    inputs: dict[str, Any] = {
        "cwd": str(tmp_path),
        "mode": "write" if kind == "write" else "explore",
        "prompt": "指定した対象を処理する",
        "label": pathlib.Path(_WI).stem,
        "model_type": model,
        "model_override": "user_instruction",
        "model_override_evidence": _PROOF,
    }
    if kind == "wi":
        inputs.pop("prompt")
        inputs.update(mode="task", subagent_md_path="exec", extra_params=_lane_inputs(tmp_path, _WI))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", "codex:draft/medium")
    async with _manager_with_fake("codex") as (manager, backend):
        _record(monkeypatch, manager, record, engine="codex" if kind == "codex" else "claude")
        approved = await _instruction(inputs)
        if kind in {"session", "write", "plain"}:
            if kind in {"write", "plain"}:
                specified = "write_model" if kind == "write" else _MODEL
                approved = f"{tmp_path}で{inputs['mode']}の「{inputs['prompt']}」は{specified}で。"
            _save(record, _human(approved))
        elif kind == "codex":
            _save(record, {"type": "event_msg", "payload": {"type": "user_message", "message": approved}})
        else:
            notes = tmp_path / "notes"
            entry = notes / "2026" / "11" / _WI
            entry.parent.mkdir(parents=True)
            monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
            monkeypatch.setattr(repo, "resolve_repo_id_or_raise", lambda *_args, **_kwargs: _REPO)
            inputs["model_override_evidence"] = f"{kind}:{_WI}"
            if kind == "wi":
                content = f"type: awi\nprocessing_owner: {{host: {socket.gethostname()}, session: {_OWNER}}}\n"
                body = f"## ユーザーコメント\n\n{approved}\n"
            else:
                content = f"type: uwi\nsubmitter_session: {_OWNER}\n"
                body = f"## 質問\n\n{approved}\n\n## 回答\n{ANSWER_MARKER}\n{approved}\n"
            entry.write_text(f"---\nstate: processing\ntarget_repo: {_REPO}\n{content}---\n\n{body}", encoding="utf-8")
        await mcp_tools.mcp.call_tool("start", inputs)
        launch_kind = "delegate" if kind == "wi" else "explore"
        expected = ("draft", "medium", "write") if kind == "write" else ("explicit", "high", launch_kind)
        assert backend.start_calls == [expected]


@pytest.mark.asyncio
async def test_same_config_copied_as_candidates_is_not_authorization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", _MODEL)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", "codex:reviewed/medium")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_FAST_MODEL", "codex:reviewed-fast/low")
    record = tmp_path / "session.jsonl"
    _save(record, _human("実装はexecuteを使う。"))
    inputs: dict[str, Any] = {"cwd": str(tmp_path), "mode": "delegate", "prompt": "この変更を担当する"}
    async with _manager_with_fake("codex") as (manager, backend):
        context = _record(monkeypatch, manager, record)
        with pytest.raises(ToolError, match="委譲先は起動していない"):
            await mcp_tools.mcp.call_tool(
                "start",
                {**inputs, "model_type": _MODEL, "model_override": "user_instruction", "model_override_evidence": _PROOF},
            )
        assert not backend.start_calls
        context.reset_mock()
        for phase in ("execute", "review", "review_fast"):
            await mcp_tools.mcp.call_tool("start", {**inputs, "model_type": phase})
        context.assert_not_called()
        assert backend.start_calls == [
            ("explicit", "high", "delegate"),
            ("reviewed", "medium", "delegate"),
            ("reviewed-fast", "low", "delegate"),
        ]


@pytest.mark.asyncio
async def test_handoff_override_is_bound_to_successor_before_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    record = tmp_path / "session.jsonl"
    inputs: dict[str, Any] = {
        "cwd": str(tmp_path),
        "mode": "explore",
        "prompt": "調査する",
        "model_type": _MODEL,
        "model_override": "user_instruction",
        "model_override_evidence": _PROOF,
    }
    async with _manager_with_fake("codex") as (manager, backend):
        _record(monkeypatch, manager, record)
        approved = await _instruction(inputs)
        _save(record, _human(approved))
        original = await mcp_tools.start(**inputs)
        child = manager.sessions[original["session_id"]]
        _complete(child, message="残作業")
        release = AsyncMock(wraps=backend.release_session)
        monkeypatch.setattr(backend, "release_session", release)
        with pytest.raises(ToolError, match="委譲先は起動していない"):
            await mcp_tools.mcp.call_tool("start", {"handoff_from": child.session_id})
        with pytest.raises(ToolError, match="委譲先は起動していない"):
            await mcp_tools.mcp.call_tool(
                "start",
                {
                    "handoff_from": child.session_id,
                    "model_type": _MODEL,
                    "model_override": "user_instruction",
                    "model_override_evidence": _PROOF,
                },
            )
        release.assert_not_awaited()
        assert backend.start_calls == [("explicit", "high", "explore")]


@pytest.mark.asyncio
async def test_role_override_requires_human_selection_before_the_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "codex:implementation/high")
    record = tmp_path / "session.jsonl"
    inputs = {
        "cwd": str(tmp_path),
        "subagent_md_path": "exec",
        "extra_params": _observed_input_params("exec.subagent.md", tmp_path),
    }
    override = {**inputs, "model_type": _MODEL, "model_override": "user_instruction", "model_override_evidence": _PROOF}
    async with _manager_with_fake("codex") as (manager, backend):
        _record(monkeypatch, manager, record)
        approved = await _instruction(override)
        assert " | execute | " in approved
        assert not backend.start_calls
        await mcp_tools.mcp.call_tool("start", inputs)
        _save(record, _human(approved))
        await mcp_tools.mcp.call_tool("start", override)
        assert backend.start_calls == [("implementation", "high", "delegate"), ("explicit", "high", "delegate")]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["delegate", "task", "explore", "handoff"])
async def test_unrecorded_fallback_does_not_start_or_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, route: str
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", "codex:configured/high")
    inputs = {"cwd": str(tmp_path), "mode": "delegate", "prompt": "調査"}
    phase = {"task": "execute", "explore": "chores_fast"}.get(route, "review")
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        match route:
            case "task":
                inputs = {
                    "cwd": str(tmp_path),
                    "mode": "task",
                    "subagent_md_path": "exec",
                    "extra_params": _observed_input_params("exec.subagent.md", tmp_path),
                }
            case "explore":
                inputs["mode"] = "explore"
            case "handoff":
                started = await mcp_tools.start(cwd=str(tmp_path), mode="delegate", prompt="調査", model_type="review")
                child = manager.sessions[started["session_id"]]
                _complete(child, message="残作業")
                inputs = {"handoff_from": child.session_id}
                backend.start_calls.clear()
        release = AsyncMock(wraps=backend.release_session)
        monkeypatch.setattr(backend, "release_session", release)
        with pytest.raises(ToolError, match="失敗記録.*起動していない"):
            await mcp_tools.mcp.call_tool(
                "start",
                {
                    **inputs,
                    "model_type": _MODEL,
                    "model_override": "fallback_failure",
                    "model_override_evidence": f"phase:{phase}",
                },
            )
        assert not backend.start_calls
        release.assert_not_awaited()


@pytest.mark.asyncio
async def test_recorded_start_failure_allows_recovery_only_for_the_failed_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", "codex:configured/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_FAST_MODEL", "codex:configured/high")
    inputs = {"cwd": str(tmp_path), "mode": "delegate", "prompt": "画面の品質を判定する"}
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        original_start = backend.start
        monkeypatch.setattr(
            backend, "start", AsyncMock(side_effect=backends.StartCandidateUnavailable("candidateDependencyUnavailable"))
        )
        with pytest.raises(ToolError, match="no available model candidates"):
            await mcp_tools.mcp.call_tool("start", {**inputs, "model_type": "review"})
        monkeypatch.setattr(backend, "start", original_start)
        fallback = {**inputs, "model_type": _MODEL, "model_override": "fallback_failure"}
        with pytest.raises(ToolError, match="失敗記録"):
            await mcp_tools.mcp.call_tool("start", {**fallback, "model_override_evidence": "phase:review_fast"})
        assert not backend.start_calls
        await mcp_tools.mcp.call_tool("start", {**fallback, "model_override_evidence": "phase:review"})
        assert backend.start_calls == [("explicit", "high", "delegate")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "author,comment,assignment",
    [
        ("human", '担当モデル: {実装担当: "codex:explicit/high"}', "same"),
        ("human", "実装担当: `codex:explicit/high`", "same"),
        ("agent", "実装担当: `codex:explicit/high`", "same"),
        ("human", "> 実装担当: `codex:explicit/high`", "same"),
        ("human", '担当モデル: {実装担当: "codex:other/high", 実装担当: "codex:explicit/high"}', "same"),
        ("human", "実装担当: `codex:explicit/high`", "unassigned"),
        ("human", "実装担当: `codex:explicit/high`", "other"),
        ("human", "実装担当: `codex:explicit/high`", "multiple"),
    ],
)
async def test_existing_wi_model_requires_a_matching_user_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, author: str, comment: str, assignment: str
) -> None:
    notes = tmp_path / "notes"
    entry = notes / "2026" / "11" / _WI
    entry.parent.mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setattr(repo, "resolve_repo_id_or_raise", lambda *_args, **_kwargs: _REPO)
    section = "ユーザーコメント" if author == "human" else "実装案"
    entry.write_text(
        f"---\nstate: processing\ntarget_repo: {_REPO}\ntype: awi\n"
        f"processing_owner: {{host: {socket.gethostname()}, session: {_OWNER}}}\n---\n\n## {section}\n\n{comment}\n",
        encoding="utf-8",
    )
    other = "20261126-224649-002.md"
    assignments = {"same": (_WI,), "unassigned": (), "other": (other,), "multiple": (_WI, other)}
    inputs = {
        "cwd": str(tmp_path),
        "subagent_md_path": "exec",
        "label": entry.stem,
        "extra_params": _lane_inputs(tmp_path, *assignments[assignment]),
        "model_type": _MODEL,
        "model_override": "user_instruction",
        "model_override_evidence": f"wi:{_WI}",
    }
    async with _manager_with_fake("codex") as (manager, backend):
        _record(monkeypatch, manager, tmp_path / "session.jsonl")
        if assignment != "same" or author != "human" or comment.startswith(">") or "codex:other" in comment:
            with pytest.raises(ToolError, match="起動していない"):
                await mcp_tools.mcp.call_tool("start", inputs)
            assert not backend.start_calls
        else:
            await mcp_tools.mcp.call_tool("start", inputs)
            assert backend.start_calls == [("explicit", "high", "delegate")]
