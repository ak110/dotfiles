"""taskの生成本文から公開証拠抽出までの記録受渡しを検証する。

明示出所の選択は試験入力として固定する。文書を読む担当の意味判断は自動化しない。
fixtureの形式はsession_evidence_user_events_test.pyの実記録由来の形に従う。
Claude Code 2.1.295のoriginと、2026年8月〜10月のCodex rolloutのmessageを使う。
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import launch_requests, manager, mcp_tools, record_paths, state, status_file
from agent_toolkit._common import state_paths
from agent_toolkit._testing import agents_server_support


class _ObservedBackend(agents_server_support.FakeBackend):
    """モデルを呼ばず、taskの生成本文を受け取るbackend。"""

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model_output_observed = True
        return session


@pytest.fixture(autouse=True)
def _without_engine_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    # この試験はbackendを起動しない。外部uv/miseの事前確認も同じ境界で除く。
    async def accepted(_cwd: str, **_kwargs: Any) -> None:
        pass

    monkeypatch.setattr(launch_requests, "check_plugin_commands", accepted)


def _write_record(directory: pathlib.Path, engine: str, name: str) -> pathlib.Path:
    path = directory / f"{name}.jsonl"
    entries = []
    for index, text in enumerate((f"{name}の最初の依頼", f"{name}の対象発話\n改行も保持する。"), 1):
        if engine == "claude":
            entry = {
                "type": "user",
                "origin": {"kind": "human"},
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            }
        else:
            entry = {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
            }
        entry["timestamp"] = f"2026-10-13T00:00:0{index}Z"
        entries.append(entry)
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return path


def _evidence(
    arguments: list[str], capsys: pytest.CaptureFixture[str], directory: pathlib.Path
) -> tuple[pathlib.Path, list[dict[str, Any]]]:
    capsys.readouterr()
    with pytest.raises(SystemExit) as raised:
        atk.main(["run-script", "session-review-evidence", "--", *arguments])
    captured = capsys.readouterr()
    assert raised.value.code == 0, captured.err
    assert not captured.err
    if "--user-events" in arguments:
        saved = pathlib.Path(captured.out.splitlines()[0].removeprefix("保存先: "))
    else:
        # 保存済み位置の単発照会は直接出力する公開仕様なので、検証成果物へ保持する。
        saved = directory / "selected-event.jsonl"
        saved.write_text(captured.out, encoding="utf-8")
    assert saved.is_absolute() and saved.is_file()
    return saved, [json.loads(line) for line in saved.read_text(encoding="utf-8").splitlines()]


def _context(prompt: str, directory: pathlib.Path) -> dict[str, Any]:
    (directory / "generated-task.txt").write_text(prompt, encoding="utf-8")
    match = re.search(r'<atk-auto source="agents-server" kind="launcher-record">\n(.*?)\n</atk-auto>', prompt, re.DOTALL)
    assert match is not None
    context = json.loads(match[1])
    (directory / "launcher-record.json").write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
    return context


async def _start_task(directory: pathlib.Path, request: str) -> dict[str, Any]:
    role = pathlib.Path(__file__).parents[2] / "share" / "add-wi.subagent.md"
    return await mcp_tools.start(
        cwd=str(directory),
        subagent_md_path=str(role),
        model_type="claude:test/medium",
        extra_params={"投入する要求": request, "引き継ぎ記録先": str(directory / "handoff.md")},
    )


@pytest.mark.parametrize("engine", ("claude", "codex"))
@pytest.mark.parametrize("source", ("delivered", "ancestor", "other", "saved-position"))
@pytest.mark.asyncio
async def test_task_record_reaches_public_evidence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], engine: str, source: str
) -> None:
    """入れ子の直接親を配送し、固定した各出所から原文と元のrecord・lineを取得できる。"""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    records = {name: _write_record(tmp_path, engine, name) for name in ("direct-parent", "ancestor", "other", "self")}
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("ancestor", "nested-writer.json", "direct-parent"),
        state_root=tmp_path / "state",
        aggregate_seconds=0,
    )
    current = manager.AgentsServerManager(writer)
    backend = _ObservedBackend(current.sessions, "claude")
    agents_server_support.install_backend(current, "claude", backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", current)
    monkeypatch.setenv("CODEX_THREAD_ID", "self")
    requested: list[str] = []

    def find_record(session_id: str, **_kwargs: Any) -> record_paths.SessionRecord:
        requested.append(session_id)
        return record_paths.SessionRecord(engine, (records[session_id],))

    monkeypatch.setattr(record_paths, "find_session_record", find_record)
    request = "request=/repo=awi=対象発話の取得"
    selected = "direct-parent" if source == "delivered" else "ancestor" if source == "ancestor" else "other"
    saved_input: pathlib.Path | None = None
    if source == "saved-position":
        saved_input, _ = _evidence(["--transcript", str(records["other"]), "--user-events"], capsys, tmp_path)
        request += f"\n発話: {saved_input} record={engine}:other line=2"
    elif source != "delivered":
        request += f"\n発話: {records[selected]}"
    try:
        started = await _start_task(tmp_path, request)
        assert started["session_id"] == "claude-session"
        context = _context(backend.prompts[0], tmp_path)
        assert requested == ["direct-parent"]
        assert context["launcher_session_id"] == "direct-parent"
        assert context["status"] == "resolved"
        assert context["record_engine"] == engine
        assert context["record_path"] == str(records["direct-parent"])
        if source == "delivered":
            # この引数だけは生成本文から取得し、要求入力にはID・記録パスを含めない。
            arguments = ["--transcript", context["record_path"], "--user-events"]
        elif saved_input is not None:
            assert request in backend.prompts[0]
            arguments = ["--user-events-file", str(saved_input), "--user-event-at", f"{engine}:other:2"]
        else:
            assert str(records[selected]) in backend.prompts[0]
            arguments = ["--transcript", str(records[selected]), "--user-events"]
        saved, events = _evidence(arguments, capsys, tmp_path)
        users = [event for event in events if event["kind"] == "user"]
        expected_lines = [2] if saved_input is not None else [1, 2]
        assert [event["line"] for event in users] == expected_lines
        assert all(event["record"] == f"{engine}:{selected}" for event in users)
        assert users[-1]["text"] == f"{selected}の対象発話\n改行も保持する。"
        (tmp_path / "extraction.json").write_text(
            json.dumps({"arguments": arguments, "saved_output": str(saved), "events": events}, ensure_ascii=False),
            encoding="utf-8",
        )
    finally:
        await current.close()


def _write_observation_record(directory: pathlib.Path, engine: str) -> pathlib.Path:
    """既存tool_call_sessionが写した実記録の形で、発話を持たない観測記録を作成する。

    形式の出所は_testing/session_evidence_support.pyのclaude_call/claude_resultと、
    tool_call_sessionの2026年10月4日Codex rollout由来のfunction_call/outputである。
    """
    command = "rg --files /isolated/repo"
    result = "a.py\nb.py\n"
    if engine == "claude":
        entries = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "observation-call", "name": "Bash", "input": {"command": command}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "observation-call", "content": result}],
                },
            },
        ]
    else:
        entries = [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "observation-call",
                    "arguments": json.dumps({"cmd": command}),
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "observation-call", "output": result},
            },
        ]
    for index, entry in enumerate(entries, 1):
        entry["timestamp"] = f"2026-10-13T00:00:0{index}Z"
    path = directory / "observation-parent.jsonl"
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return path


@pytest.mark.parametrize("engine", ("claude", "codex"))
@pytest.mark.asyncio
async def test_task_record_reaches_public_observation_evidence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], engine: str
) -> None:
    """引用を持たない観測要求から、自動配送された記録のtool入力と結果の全文へ到達する。"""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CODEX_THREAD_ID", "observation-parent")
    transcript = _write_observation_record(tmp_path, engine)
    current = manager.AgentsServerManager(None)
    backend = _ObservedBackend(current.sessions, "claude")
    agents_server_support.install_backend(current, "claude", backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", current)
    requested: list[str] = []

    def find_record(session_id: str, **_kwargs: Any) -> record_paths.SessionRecord:
        requested.append(session_id)
        return record_paths.SessionRecord(engine, (transcript,))

    monkeypatch.setattr(record_paths, "find_session_record", find_record)
    try:
        started = await _start_task(
            tmp_path,
            "request=/repo=awi=観測の裏付け取得\n確定済みの観測: rg --filesの結果はa.pyとb.py。入力特徴は/isolated/repo。",
        )
        assert started["session_id"] == "claude-session"
        context = _context(backend.prompts[0], tmp_path)
        assert requested == ["observation-parent"]
        assert context["status"] == "resolved"
        assert context["launcher_session_id"] == "observation-parent"
        assert context["record_engine"] == engine
        arguments = ["--transcript", context["record_path"], "--tool-calls"]
        saved, events = _evidence(arguments, capsys, tmp_path)
        calls_path = saved.rename(tmp_path / "observed-tool-calls.jsonl")
        calls = [event for event in events if event["kind"] == "tool-call"]
        assert len(calls) == 1
        call = calls[0]
        expected_tool = "Bash" if engine == "claude" else "exec_command"
        assert (call["tool"], call["call_id"], call["text"]) == (expected_tool, "observation-call", "rg --files /isolated/repo")
        assert (call["record"], call["line"], call["result_line"]) == (f"{engine}:observation-parent", 1, 2)
        detail_arguments = [
            "--transcript",
            context["record_path"],
            "--detail",
            f"{call['record']}:{call['line']}",
            "--detail",
            f"{call['record']}:{call['result_line']}",
        ]
        detail_saved, details = _evidence(detail_arguments, capsys, tmp_path)
        details_path = detail_saved.rename(tmp_path / "observation-details.jsonl")
        assert [(event["record"], event["line"]) for event in details] == [
            (f"{engine}:observation-parent", 1),
            (f"{engine}:observation-parent", 2),
        ]
        if engine == "claude":
            assert details[0]["input"] == {"command": "rg --files /isolated/repo"}
            assert details[1]["tool"] == "observation-call"
            # Claudeのtool_result本文は公開照会で外側の空白を除いた形になる。
            assert details[1]["text"] == "a.py\nb.py"
        else:
            input_payload = json.loads(details[0]["text"])["payload"]
            assert json.loads(input_payload["arguments"]) == {"cmd": "rg --files /isolated/repo"}
            result_payload = json.loads(details[1]["text"])["payload"]
            assert result_payload == {"type": "function_call_output", "call_id": "observation-call", "output": "a.py\nb.py\n"}
        assert not any(event["kind"] == "user" for event in events)
        (tmp_path / "observation-extraction.json").write_text(
            json.dumps(
                {
                    "arguments": arguments,
                    "saved_output": str(calls_path),
                    "events": events,
                    "detail_arguments": detail_arguments,
                    "detail_output": str(details_path),
                    "details": details,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    finally:
        await current.close()


@pytest.mark.parametrize("engine", ("claude", "codex"))
@pytest.mark.parametrize("availability", ("unresolved", "missing", "ambiguous"))
@pytest.mark.asyncio
async def test_task_starts_with_record_shortage_without_substitute(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, availability: str
) -> None:
    """不足を起動本文へ残し、記録不要のtaskも開始し、祖先や別の記録をパスへ補わない。"""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    current = manager.AgentsServerManager(None)
    backend = _ObservedBackend(current.sessions, "claude")
    agents_server_support.install_backend(current, "claude", backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", current)
    _write_record(tmp_path, engine, "ancestor")
    _write_record(tmp_path, engine, "self")
    candidates = tuple(_write_record(tmp_path, engine, f"candidate-{index}") for index in range(2))
    requested: list[str] = []

    def find_record(session_id: str, **_kwargs: Any) -> record_paths.SessionRecord | None:
        requested.append(session_id)
        return record_paths.SessionRecord(engine, candidates) if availability == "ambiguous" else None

    monkeypatch.setattr(record_paths, "find_session_record", find_record)
    if availability != "unresolved":
        monkeypatch.setenv("CODEX_THREAD_ID", "direct-parent")
    try:
        started = await _start_task(tmp_path, "request=/repo=awi=記録を取得しない観測")
        assert started["session_id"] == "claude-session"
        context = _context(backend.prompts[0], tmp_path)
        assert context["status"] == availability
        assert "record_path" not in context
        assert requested == ([] if availability == "unresolved" else ["direct-parent"])
        if availability == "unresolved":
            assert "launcher_session_id" not in context
        else:
            assert context["launcher_session_id"] == "direct-parent"
        if availability == "ambiguous":
            assert context["record_candidates"] == [str(path) for path in candidates]
        assert len(backend.start_calls) == 1
    finally:
        await current.close()
