from __future__ import annotations

import datetime
import json
import pathlib
import socket
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_toolkit._agents_server import backends, launch_requests, mcp_tools, session_handoff, state
from agent_toolkit._atk.wi import repo
from agent_toolkit._atk.wi.uwi_body import ANSWER_MARKER
from agent_toolkit._common.claude_usage_limit import UsageLimitState
from agent_toolkit._common.task_documents import task_prompt_header
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    _complete,
    _manager_with_fake,
    _observed_input_params,
    install_backend,
)

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


@pytest.mark.parametrize("kind", ["session", "codex", "wi", "answer", "plain"])
@pytest.mark.asyncio
async def test_public_start_accepts_scoped_human_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, kind: str
) -> None:
    record = tmp_path / "session.jsonl"
    inputs: dict[str, Any] = {
        "cwd": str(tmp_path),
        "mode": "explore",
        "prompt": "指定した対象を処理する",
        "label": pathlib.Path(_WI).stem,
        "model_type": _MODEL,
        "model_override": "user_instruction",
        "model_override_evidence": _PROOF,
    }
    if kind == "wi":
        inputs.pop("prompt")
        inputs.update(mode="task", subagent_md_path="exec", extra_params=_lane_inputs(tmp_path, _WI))
    async with _manager_with_fake("codex") as (manager, backend):
        _record(monkeypatch, manager, record, engine="codex" if kind == "codex" else "claude")
        approved = await _instruction(inputs)
        if kind in {"session", "plain"}:
            if kind == "plain":
                approved = f"{tmp_path}で{inputs['mode']}の「{inputs['prompt']}」は{_MODEL}で。"
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
        assert backend.start_calls == [("explicit", "high", launch_kind)]


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
@pytest.mark.parametrize("scope", ["current", "previous", "wrong-candidate", "no-codex"])
async def test_repository_handoff_rechecks_codex_direction_in_the_latest_human_input(monkeypatch, tmp_path, scope):
    setting = "claude:sonnet/high" if scope == "no-codex" else "codex:configured/high,claude:sonnet/high"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", setting)
    monkeypatch.setattr(session_handoff, "prepare_prompt", lambda predecessor: predecessor.prompt)
    async with _manager_with_fake("codex") as (manager, backend):
        record = tmp_path / "session.jsonl"
        _record(monkeypatch, manager, record)
        records = [_human("あとrepository-processはcodex使えって言ったでしょ")]
        if scope == "previous":
            records.append(_human("次はモデルを指定せず、別の依頼を進める"))
        _save(record, *records)
        past = FakeBackend(manager.sessions, "claude")
        install_backend(manager, "claude", past)
        role = launch_requests.resolve_task_document_path("repository-process")
        child = state.SessionState(
            "old-claude",
            str(tmp_path),
            engine="claude",
            model="sonnet",
            effort="high",
            model_type="execute",
            prompt=task_prompt_header(role),
        )
        _complete(child, message="前任の成果")
        manager.sessions[child.session_id] = child
        alternate = "codex:configured/high" if scope != "wrong-candidate" else _MODEL
        inputs = {
            "handoff_from": child.session_id,
            "model_type": alternate,
            "model_override": "user_instruction",
            "model_override_evidence": _PROOF,
        }
        if scope == "current":
            await mcp_tools.mcp.call_tool("start", inputs)
            assert backend.start_calls == [("configured", "high", "delegate")]
        else:
            with pytest.raises(ToolError, match="今回の対象・工程・全候補"):
                await mcp_tools.mcp.call_tool("start", inputs)
            assert not backend.start_calls
        assert not past.start_calls and not past.send_calls


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
        with pytest.raises(ToolError, match="週間上限.*起動していない"):
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
async def test_dependency_failure_does_not_authorize_override(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
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
        for phase in ("review", "review_fast"):
            with pytest.raises(ToolError, match="週間上限"):
                await mcp_tools.mcp.call_tool("start", {**fallback, "model_override_evidence": f"phase:{phase}"})
        assert not backend.start_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "phase", "another_phase"), [("delegate", "review", "execute"), ("explore", "chores_fast", "chores")]
)
async def test_failed_handoff_does_not_change_phase_without_authority(monkeypatch, tmp_path, mode, phase, another_phase):
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{phase.upper()}_MODEL", "codex:original/high,codex:configured/high")
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{another_phase.upper()}_MODEL", "codex:other/high")
    monkeypatch.setattr(session_handoff, "prepare_prompt", lambda predecessor: predecessor.prompt)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        started = await mcp_tools.start(cwd=str(tmp_path), mode=mode, prompt="前の作業", model_type=phase)
        child = manager.sessions[started["session_id"]]
        _complete(child, error={"codexErrorInfo": "serverOverloaded"})
        backend.start_calls.clear()
        release = AsyncMock(wraps=backend.release_session)
        monkeypatch.setattr(backend, "release_session", release)
        for wrong in (another_phase, _MODEL):
            with pytest.raises(ToolError, match="週間上限"):
                await mcp_tools.mcp.call_tool("start", {"handoff_from": child.session_id, "model_type": wrong})
        assert not backend.start_calls
        release.assert_not_awaited()
        await mcp_tools.mcp.call_tool("start", {"handoff_from": child.session_id, "model_type": phase})
        assert backend.start_calls == [("configured", "high", mode)]


@pytest.mark.asyncio
async def test_observation_can_use_configured_phase_but_not_override_it(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:configured/high")
    inputs = {"cwd": str(tmp_path), "mode": "explore", "prompt": "実機確認"}
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        with pytest.raises(ToolError, match="週間上限.*起動していない"):
            await mcp_tools.mcp.call_tool(
                "start",
                {**inputs, "model_type": "codex:luna/low", "model_override": "observation", "model_override_evidence": "観測"},
            )
        assert not backend.start_calls
        await mcp_tools.mcp.call_tool("start", inputs)
        assert backend.start_calls == [("configured", "high", "explore")]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["weekly", "available", "text", "five_hour", "expired", "other_phase", "other_cwd"])
async def test_public_fallback_checks_weekly_evidence_for_every_candidate(monkeypatch, tmp_path, scenario):
    inputs = {"cwd": str(tmp_path), "mode": "delegate", "prompt": "同じ作業の起動"}
    alternate = tmp_path / "other"
    alternate.mkdir()
    now = datetime.datetime.now(datetime.UTC)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        for candidate in ("configured-a", "configured-b"):
            monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", f"codex:{candidate}/high")
            monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_FAST_MODEL", f"codex:{candidate}/high")
            second = candidate.endswith("-b")
            phase = "review_fast" if second and scenario == "other_phase" else "review"
            cwd = str(alternate) if second and scenario == "other_cwd" else str(tmp_path)
            started = await mcp_tools.start(cwd=cwd, mode="delegate", prompt=inputs["prompt"], model_type=phase)
            if second and scenario == "available":
                continue
            session = manager.sessions[started["session_id"]]
            kind = "five_hour" if second and scenario == "five_hour" else "seven_day"
            reset = now + datetime.timedelta(hours=-1 if second and scenario == "expired" else 1)
            session.status = "failed"
            session.error = (
                {"message": "weekly usage limit"}
                if second and scenario == "text"
                else {"codexErrorInfo": "usageLimitExceeded", "usageLimit": {"type": kind, "resetsAt": reset.isoformat()}}
            )
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", "codex:configured-a/high,codex:configured-b/high")
        backend.start_calls.clear()
        fallback = {
            **inputs,
            "model_type": _MODEL,
            "model_override": "fallback_failure",
            "model_override_evidence": "phase:review",
        }
        if scenario == "weekly":
            await mcp_tools.mcp.call_tool("start", fallback)
            assert backend.start_calls == [("explicit", "high", "delegate")]
        else:
            with pytest.raises(ToolError, match="週間上限.*起動していない"):
                await mcp_tools.mcp.call_tool("start", fallback)
            assert not backend.start_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("api_status", [429, 529])
async def test_claude_fallback_combines_rejection_and_weekly_report(monkeypatch, tmp_path, api_status):
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", "claude:sonnet/high")
    inputs = {"cwd": str(tmp_path), "mode": "delegate", "prompt": "作業を再開"}
    async with _manager_with_fake("claude") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        result = await mcp_tools.start(cwd=str(tmp_path), mode="delegate", prompt=inputs["prompt"], model_type="review")
        session = manager.sessions[result["session_id"]]
        session.status = "failed"
        session.error = {"apiErrorStatus": api_status}
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
        session.usage_limit = UsageLimitState("rejected", "seven_day_sonnet", int(future.timestamp()))
        backend.start_calls.clear()
        fallback = {
            **inputs,
            "model_type": "claude:haiku/low",
            "model_override": "fallback_failure",
            "model_override_evidence": "phase:review",
        }
        if api_status == 429:
            await mcp_tools.mcp.call_tool("start", fallback)
            assert backend.start_calls == [("haiku", "low", "delegate")]
        else:
            with pytest.raises(ToolError, match="週間上限"):
                await mcp_tools.mcp.call_tool("start", fallback)
            assert not backend.start_calls


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


@pytest.mark.parametrize("route", ["handoff", "reply", "restored-reply", "restored-handoff"])
@pytest.mark.parametrize(
    "change",
    [
        "same",
        "configured",
        "legacy",
        "legacy-cancelled",
        "other-wi",
        "unspecified",
        "cancelled",
        "other-role",
        "other-candidate",
        "free-body",
    ],
)
@pytest.mark.asyncio
async def test_wi_model_for_handoff_and_completed_reply(tmp_path, monkeypatch, route, change):
    requested = "codex:astra/high"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "codex:configured/high")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "notes"))
    monkeypatch.setattr(repo, "resolve_repo_id_or_raise", lambda *_args, **_kwargs: _REPO)
    entry = tmp_path / "notes" / "2026" / "11" / _WI
    entry.parent.mkdir(parents=True)

    def save_comment(comment, *, target=entry):
        target.write_text(
            f"---\nstate: processing\ntarget_repo: {_REPO}\ntype: awi\n"
            f"processing_owner: {{host: {socket.gethostname()}, session: {_OWNER}}}\n---\n\n{comment}\n",
            encoding="utf-8",
        )

    saved_comment = f"## ユーザーコメント\n\n実装担当: {requested}"
    save_comment(saved_comment)
    inputs = {
        "model_type": requested,
        "model_override": "user_instruction",
        "model_override_evidence": f"wi:{_WI}",
    }
    async with _manager_with_fake("codex") as (manager, backend):
        record = tmp_path / "session.jsonl"
        _save(record, _human("保存済みのWIを続ける。"))
        _record(monkeypatch, manager, record)
        monkeypatch.setattr(manager, "launcher_session_id", lambda: _OWNER)
        monkeypatch.setattr(session_handoff, "prepare_prompt", lambda original: original.prompt)
        catalog = await backend.list_models()
        monkeypatch.setattr(launch_requests.codex_models, "list_models", lambda: catalog)
        params = _lane_inputs(tmp_path, _WI)
        if change in {"configured", "free-body"}:
            monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", requested)
        if change == "free-body":
            _, prompt, _, _ = launch_requests.task_document_request("exec", params, launcher_session_id=_OWNER)
            first = await mcp_tools.start(
                cwd=str(tmp_path), mode="delegate", label=entry.stem, model_type="execute", prompt=prompt
            )
        else:
            first = await mcp_tools.start(
                cwd=str(tmp_path),
                subagent_md_path="exec",
                label=entry.stem,
                extra_params=params,
                model_type=None if change == "configured" else requested,
                model_override=None if change == "configured" else "user_instruction",
                model_override_evidence=None if change == "configured" else inputs["model_override_evidence"],
            )
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "codex:configured/high")
        child = manager.sessions[first["session_id"]]
        if change.startswith("legacy"):
            child.task_role = None
        if route.startswith("restored"):
            child.publish_registry = True
        _complete(child, message="計画を保存した")
        if route.startswith("restored"):
            await child.publish_registration_async()
            del manager.sessions[child.session_id]
        assert backend.start_calls == [("gpt-6-astra", "high", "delegate")]

        if change == "other-wi":
            other = entry.with_name("20261126-224649-002.md")
            save_comment(saved_comment, target=other)
            _lane_inputs(tmp_path, other.name)
            inputs["model_override_evidence"] = f"wi:{other.name}"
        elif change == "unspecified":
            save_comment("## 実装案\n\n" + saved_comment.splitlines()[-1])
        elif change.endswith("cancelled"):
            save_comment("## ユーザーコメント\n\n実装担当: 取消")
        elif change == "other-role":
            save_comment(saved_comment.replace("実装担当", "実行レビュー担当"))
        elif change == "other-candidate":
            save_comment(saved_comment.replace("astra/high", "astra/medium"))

        if route.endswith("handoff"):
            operation = mcp_tools.mcp.call_tool("start", {"handoff_from": child.session_id, **inputs})
        else:
            operation = mcp_tools.mcp.call_tool("send_message", {"session_id": child.session_id, "prompt": "実装開始"})

        if change in {"same", "legacy", "configured"}:
            await operation
            if route.endswith("handoff"):
                assert backend.start_calls == [("gpt-6-astra", "high", "delegate")] * 2
                successor = manager.sessions["codex-session-2"]
                _complete(successor)
                save_comment("## ユーザーコメント\n\n実装担当: 取消")
                with pytest.raises(ToolError, match="追送は配送していない"):
                    await mcp_tools.mcp.call_tool("send_message", {"session_id": successor.session_id, "prompt": "実装開始"})
                assert backend.send_calls == 0
            elif route == "restored-reply":
                assert backend.resume_calls == [child.session_id]
            else:
                assert backend.send_calls == 1
        else:
            with pytest.raises(ToolError, match="委譲先は起動していない|追送は配送していない"):
                await operation
            assert len(backend.start_calls) == 1
            assert not backend.release_calls
            assert backend.send_calls == 0
            assert not backend.resume_calls
