"""Code Modeにも届く設定hookから背景観測とCron装着要求を配送する。"""

import json

import pytest

from agent_toolkit._common import function_hook_requests
from agent_toolkit._hooks import background_tasks, posttooluse


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
@pytest.mark.parametrize("background_requested", [True, False])
def test_background_result_sends_original_command(monkeypatch, event, background_requested):
    """背景識別子とcommand原文を設定hookで一度だけ配送する。"""
    observed = []
    monkeypatch.setattr(function_hook_requests, "enqueue", lambda *args, **kwargs: observed.append(args))
    posttooluse.main(
        json.dumps(
            {
                "session_id": "bridge",
                "hook_event_name": event,
                "tool_name": "Bash",
                "cwd": "",
                "tool_input": {"command": "atk agents wait", "run_in_background": background_requested},
                "tool_response": {"backgroundTaskId": "background-id"},
            }
        )
    )
    assert observed == [("bridge", "background_task", {"id": "background-id", "command": "atk agents wait"})]


def _start_payload(status: str = "running", agent: str | None = None) -> dict:
    payload = {
        "session_id": "bridge",
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
        "tool_input": {},
        "cwd": "",
        "tool_response": {"session_id": "child-session", "status": status},
    }
    if agent:
        payload["agent_id"] = agent
    return payload


def _mod_marker(monkeypatch, tmp_path, content: str | None = "ready") -> None:
    """modの読込目印を一時領域へ置き、Cron装着要求の宛先が読み込まれた状態を作る。"""
    marker = tmp_path / "marker-bridge.txt"
    if content is not None:
        marker.write_text(content, encoding="utf-8")
    monkeypatch.setattr(posttooluse, "function_hook_paths", lambda _session: (marker, tmp_path / "request-bridge.txt"))


def _additional_context(capsys) -> str:
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


@pytest.mark.parametrize(
    "status,agent,expected", [("running", None, True), ("failed", None, False), ("running", "child", False)]
)
def test_only_successful_main_start_requests_cron(monkeypatch, tmp_path, status, agent, expected):
    """拒否・失敗とサブエージェントのstartでCron装着を要求しない。"""
    _mod_marker(monkeypatch, tmp_path)
    observed = []

    async def request(*args, **_kwargs):
        observed.append(args)
        return {"ok": True, "reason": ""}

    monkeypatch.setattr(function_hook_requests, "request", request)
    posttooluse.main(json.dumps(_start_payload(status, agent)))
    assert any(operation == "periodic_recheck" for _, operation, _ in observed) is expected


@pytest.mark.parametrize("reason", ["", '<atk-auto source="periodic-recheck" kind="warn">\n作成できなかった\n</atk-auto>'])
def test_start_result_carries_only_mount_failure(monkeypatch, tmp_path, capsys, reason):
    """装着の成功は何も添えず、装着失敗の警告だけを同じstartの結果へ添える。"""
    _mod_marker(monkeypatch, tmp_path)

    async def request(*_args, **_kwargs):
        return {"ok": True, "reason": reason}

    monkeypatch.setattr(function_hook_requests, "request", request)
    posttooluse.main(json.dumps(_start_payload()))
    context = _additional_context(capsys)
    assert ("作成できなかった" in context) is bool(reason)
    assert "taskを作成した" not in context


@pytest.mark.parametrize("marker", [None, "consumed"])
def test_start_without_loaded_mod_does_not_wait(monkeypatch, tmp_path, marker):
    """modの読込目印が無い会話では、応答の無い装着要求を待たない。"""
    _mod_marker(monkeypatch, tmp_path, marker)

    async def request(*_args, **_kwargs):
        raise AssertionError("modの無い会話で装着を待った")

    monkeypatch.setattr(function_hook_requests, "request", request)
    posttooluse.main(json.dumps(_start_payload()))


def test_mount_wait_timeout_keeps_start_result(monkeypatch, tmp_path, capsys):
    """modが期限内に応答しなくても、startの結果へ誤った警告や例外を加えない。"""
    _mod_marker(monkeypatch, tmp_path)

    async def request(*_args, **_kwargs):
        raise TimeoutError("会話内modの確認応答が期限内にない")

    monkeypatch.setattr(function_hook_requests, "request", request)
    assert posttooluse.main(json.dumps(_start_payload())) == 0
    assert "periodic-recheck" not in _additional_context(capsys)


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
def test_repl_bash_start_and_finish_without_background_task_id(monkeypatch, event):
    """内側tool IDで圧縮を追跡し、成功・失敗のPostで解除する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    observed = []
    monkeypatch.setattr(function_hook_requests, "enqueue", lambda *args, **kwargs: observed.append(args))
    monkeypatch.setattr(background_tasks.time, "time", lambda: 100)
    payload = {
        "session_id": "bridge",
        "tool_name": "Bash",
        "tool_use_id": "repl_actual-input",
        "tool_input": {"command": "atk agents wait", "timeout": 120000},
        "cwd": "",
    }
    background_tasks.observe_repl_bash(payload)
    posttooluse.main(json.dumps({**payload, "hook_event_name": event, "tool_response": {"stdout": "", "interrupted": False}}))
    assert observed == [
        ("bridge", "repl_bash_started", {"id": "repl_actual-input", "command": "atk agents wait", "expires": 225}),
        ("bridge", "background_complete", {"id": "repl_actual-input"}),
    ]


@pytest.mark.parametrize(
    "changes",
    [{"tool_use_id": "toolu_regular"}, {"agent_id": "child"}, {"tool_input": {"command": "atk agents wait", "timeout": False}}],
)
def test_repl_observation_rejects_other_actor_and_invalid_lifetime(monkeypatch, changes):
    """通常のツールID、子の呼出し、不正な期限をREPL待機にしない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    observed = []
    monkeypatch.setattr(function_hook_requests, "enqueue", lambda *args, **kwargs: observed.append(args))
    background_tasks.observe_repl_bash(
        {
            "session_id": "bridge",
            "tool_name": "Bash",
            "tool_use_id": "repl_actual-input",
            "tool_input": {"command": "atk agents wait", "timeout": 120000},
            **changes,
        }
    )
    assert not observed
