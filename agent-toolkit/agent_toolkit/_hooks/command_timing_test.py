"""公開Pre/Postと背景通知の集約から、全体区間と見直し通知を確かめる。

入力の構造はClaude HooksのPostToolUse/Failure input、Codex HooksのTool coverageに従う。
時計だけを制御し、実コマンド・長時間待機・モデル呼出しは実行しない。
"""

import json
import types

import pytest

from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import command_timing, posttooluse, user_prompt_submit
from agent_toolkit._hooks.pretooluse import dispatch, read_delivery


@pytest.fixture
def _clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(command_timing, "time", types.SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [])
    return now


def _payload(identifier, tool="Bash", **fields):
    return {
        "session_id": "timing-tests",
        "tool_use_id": identifier,
        "tool_name": tool,
        "tool_input": {"command": "printf result"},
        **fields,
    }


def _invoke(main, payload, capsys):
    assert main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert not captured.err
    if not captured.out:
        return ""
    result = json.loads(captured.out)
    return result.get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
def test_shell_success_and_failure_use_host_duration(tool, event, _clock, capsys):
    payload = _payload("host-time", tool)
    _invoke(dispatch.main, {**payload, "hook_event_name": "PreToolUse"}, capsys)
    _clock[0] += 80
    output = _invoke(posttooluse.main, {**payload, "hook_event_name": event, "duration_ms": 1250}, capsys)
    assert "実行時間: 1.2秒" in output
    assert "観測経過時間" not in output
    repeated = _invoke(posttooluse.main, {**payload, "hook_event_name": event, "duration_ms": 1250}, capsys)
    assert "実行時間:" not in repeated


def test_codex_original_completion_uses_full_elapsed_interval(_clock, capsys):
    """write_stdinは再Preなしで元実行のPostを返す公開契約を再現する。"""
    first = _payload("exec-one", turn_id="turn")
    second = _payload("exec-two", turn_id="turn")
    for payload in (first, second):
        _invoke(dispatch.main, {**payload, "hook_event_name": "PreToolUse"}, capsys)
    running = {"session_id": 42, "wall_time_seconds": 0.25}
    assert "観測経過時間" not in _invoke(posttooluse.main, {**first, "tool_response": running}, capsys)
    _clock[0] += 65
    output = _invoke(posttooluse.main, {**first, "tool_response": {"exit_code": 0, "wall_time_seconds": 0}}, capsys)
    assert "観測経過時間（権限待ち等を含み得る）: 65.0秒" in output
    _clock[0] += 5
    output = _invoke(posttooluse.main, {**second, "tool_response": {"exit_code": 1}}, capsys)
    assert "70.0秒" in output
    assert "command_timing" in read_state("timing-tests")


def test_slow_runs_reset_and_notify_once_per_interval(_clock, capsys):
    outputs = []
    for index, seconds in enumerate((59.9, 60, 60, 60, 60, 0, 60, 60, 60)):
        payload = _payload(f"call-{index}")
        _invoke(dispatch.main, {**payload, "hook_event_name": "PreToolUse"}, capsys)
        outputs.append(_invoke(posttooluse.main, {**payload, "duration_ms": seconds * 1000}, capsys))
    assert all("3件続いた" not in outputs[index] for index in (0, 1, 2, 4, 5, 6, 7))
    assert "合計180.0秒" in outputs[3] and "3件続いた" in outputs[8]
    assert "無進捗" not in outputs[3]


def test_unknown_time_kind_change_and_actor_do_not_mix_runs(_clock, capsys):
    for index in range(2):
        payload = _payload(f"slow-{index}")
        _invoke(dispatch.main, payload, capsys)
        _invoke(posttooluse.main, {**payload, "duration_ms": 60000}, capsys)
    unknown = _invoke(posttooluse.main, _payload("no-start", duration_ms=120000), capsys)
    assert "未計測" in unknown and "0.0秒" not in unknown
    child = _payload("child", agent_id="worker", duration_ms=60000)
    assert "3件続いた" not in _invoke(posttooluse.main, child, capsys)
    payload = _payload("elapsed", turn_id="turn")
    _invoke(dispatch.main, payload, capsys)
    _clock[0] += 60
    assert "3件続いた" not in _invoke(posttooluse.main, payload, capsys)


def test_kind_change_and_other_actor_preserve_separate_intervals(_clock, capsys):
    outputs = []
    for index, actor, measured in (
        (0, "main", True),
        (1, "main", True),
        (2, "worker", True),
        (3, "main", False),
        (4, "main", False),
        (5, "main", False),
    ):
        payload = _payload(f"separate-{index}", agent_id=actor)
        _invoke(dispatch.main, payload, capsys)
        _clock[0] += 60
        fields = {"duration_ms": 60000} if measured else {}
        outputs.append(_invoke(posttooluse.main, {**payload, **fields}, capsys))
    assert all("3件続いた" not in output for output in outputs[:-1])
    assert "3件続いた" in outputs[-1] and "観測経過時間" in outputs[-1]


def test_background_notification_completes_original_once(_clock, capsys):
    payload = _payload("background")
    _invoke(dispatch.main, payload, capsys)
    started = _invoke(posttooluse.main, {**payload, "tool_response": {"backgroundTaskId": "task-one"}}, capsys)
    assert "観測経過時間" not in started
    _clock[0] += 90
    notification = {
        "session_id": "timing-tests",
        "prompt": "<task-notification><task-id>task-one</task-id><status>completed</status></task-notification>",
    }
    output = _invoke(user_prompt_submit.main, notification, capsys)
    assert "90.0秒" in output
    assert "「完了通知で始まったターンの対処」" in output
    repeated = _invoke(user_prompt_submit.main, notification, capsys)
    assert "90.0秒" not in repeated
