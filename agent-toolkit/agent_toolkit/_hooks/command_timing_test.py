"""公開Pre/Postと背景通知の集約から、全体区間と見直し通知を確かめる。

入力の構造はClaude HooksのPostToolUse/Failure input、Codex HooksのTool coverageに従う。
時計だけを制御し、実コマンド・長時間待機・モデル呼出しは実行しない。
"""

import json
import types

import pytest

from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import command_timing, posttooluse, user_prompt_submit
from agent_toolkit._hooks.pretooluse import dispatch, operation_skills


@pytest.fixture
def _clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(command_timing, "time", types.SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])
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


@pytest.mark.parametrize(("milliseconds", "notified"), [(999, False), (1000, True)])
def test_measured_notice_omits_runs_under_one_second(milliseconds, notified, _clock, capsys):
    payload = _payload(f"short-{milliseconds}")
    _invoke(dispatch.main, payload, capsys)
    output = _invoke(posttooluse.main, {**payload, "duration_ms": milliseconds}, capsys)
    assert ("実行時間: 1.0秒" in output) is notified
    assert ("実行時間:" in output) is notified


def test_elapsed_notice_also_omits_runs_under_one_second(_clock, capsys):
    payload = _payload("short-elapsed", turn_id="turn")
    _invoke(dispatch.main, payload, capsys)
    _clock[0] += 0.5
    assert "観測経過時間" not in _invoke(posttooluse.main, payload, capsys)


def test_slow_runs_reset_and_notify_once_per_interval(_clock, capsys):
    outputs = []
    for index, seconds in enumerate((9.9, 10, 10, 10, 10, 0, 10, 10, 10)):
        payload = _payload(f"call-{index}")
        _invoke(dispatch.main, {**payload, "hook_event_name": "PreToolUse"}, capsys)
        outputs.append(_invoke(posttooluse.main, {**payload, "duration_ms": seconds * 1000}, capsys))
    assert all("3件続いた" not in outputs[index] for index in (0, 1, 2, 4, 5, 6, 7))
    assert "10.0秒、10.0秒、10.0秒" in outputs[3] and "合計" not in outputs[3]
    assert "3件続いた" in outputs[8]
    assert "無進捗" not in outputs[3]


@pytest.mark.parametrize(
    "command",
    [
        "atk agents wait",
        "/tools/atk agents wait --root-session-id parent",
        "atk run-script wait-ci --baseline before.json",
        "atk run-script wait-ci --wait-sha=abc",
        "atk run-script wait-ci -- --baseline before.json",
        "gh run watch 42",
        "gh -R owner/repo run watch 42",
        "gh --repo=owner/repo run watch 42",
        "gh pr checks 42 --watch",
        "gh pr checks 42 --watch=true",
        "gh pr checks 42 --watch=false --watch",
        "gh.exe run watch 42",
    ],
)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_wait_completions_keep_individual_times_but_do_not_trigger_review(command, tool, _clock):
    """外部の結果を待つ3件は、個別の測定を返しても作業の見直しを促さない。"""
    for index in range(3):
        payload = _payload(f"wait-{index}", tool, tool_input={"command": command}, duration_ms=120000)
        command_timing.start(payload)
        notice = "\n".join(command_timing.observe(payload))
        assert "実行時間: 120.0秒" in notice
        assert "3件続いた" not in notice


@pytest.mark.parametrize("path", ['"run (1).json"', "'run; (1).json'"])
def test_bash_direct_wait_with_quoted_literal_path(path, _clock):
    for index in range(3):
        payload = _payload(
            f"quoted-wait-{index}", tool_input={"command": f"atk run-script wait-ci --baseline {path}"}, duration_ms=10000
        )
        command_timing.start(payload)
        assert not any("3件続いた" in line for line in command_timing.observe(payload))


def test_background_wait_does_not_reset_the_measured_work_sequence(_clock):
    """背景待機の計測種別が異なっても、待機の前後の作業を同じ連続件数に含める。"""
    for index in range(2):
        payload = _payload(f"work-{index}", duration_ms=10000)
        command_timing.start(payload)
        assert not any("3件続いた" in line for line in command_timing.observe(payload))
    wait = _payload("wait-background", tool_input={"command": "atk agents wait"})
    command_timing.start(wait)
    assert not command_timing.observe(wait, background_task_id="task-wait")
    _clock[0] += 480
    result = command_timing.observe_notifications(
        {
            "session_id": wait["session_id"],
            "prompt": "<task-notification><task-id>task-wait</task-id><status>completed</status></task-notification>",
        }
    )
    assert any("480.0秒" in line for line in result)
    assert not any("3件続いた" in line for line in result)

    last = _payload("work-last", duration_ms=10000)
    command_timing.start(last)
    assert any("10.0秒、10.0秒、10.0秒" in line and "合計" not in line for line in command_timing.observe(last))


@pytest.mark.parametrize(
    "command",
    [
        "atk run-script wait-ci --write-baseline before.json",
        "gh pr checks 42",
        "gh pr checks 42 --watch=false",
        "gh pr checks 42 --watch --watch=false",
        "gh pr checks 42 -- --watch",
        "gh pr checks 42 > --watch",
        "gh pr checks 42 '--' --watch",
        "gh run watch --help",
        "printf 'atk agents wait'",
        'atk agents wait --root-session-id "$(make test)"',
        'gh run watch "$(gh run list --json databaseId -q .[0].databaseId)"',
        """sh -c 'gh run watch "$(make test)"'""",
        'atk agents wait --root-session-id "`make test`"',
        "git status && atk agents wait",
    ],
)
def test_calls_not_limited_to_waiting_still_trigger_review(command, _clock):
    """コマンド文字列が待機の形に部分一致しても、通常作業は時間を集計する。"""
    for index in range(3):
        payload = _payload(f"not-wait-{index}", tool_input={"command": command}, duration_ms=10000)
        command_timing.start(payload)
        notice = "\n".join(command_timing.observe(payload))
        assert ("3件続いた" in notice) is (index == 2)


@pytest.mark.parametrize(
    ("command", "tool"),
    [
        ("sh -c 'atk agents wait'", "Bash"),
        ("gh pr checks 42 --watch > out", "Bash"),
        ("gh pr checks 42 --watch>out", "Bash"),
        ("atk agents wait>out", "Bash"),
        ('atk agents wait >"$(printf out)"', "Bash"),
        ("atk agents wait > >(cat)", "Bash"),
        ("""sh>/dev/null -c 'atk agents wait <<EOF\n$(printf active)\nEOF'""", "Bash"),
        ("function atk { printf marker; }; atk agents wait", "Bash"),
        ("atk agents wait <<'EOF'\n'$(make test)'\nEOF", "Bash"),
        ("atk agents wait <<EOF\n'$(make test)'\nEOF", "Bash"),
        ("atk agents wait | cat", "Bash"),
        ("atk agents wait; atk agents wait", "Bash"),
        ("atk agents wait\natk agents wait", "Bash"),
        ("""sh -c 'atk agents wait "$(printf active)"'""", "PowerShell"),
        ("sh -c 'atk agents wait'", "PowerShell"),
        ("atk agents wait --root-session-id (Invoke-Check)", "PowerShell"),
        (r"atk agents wait \; Get-Location", "PowerShell"),
        (r'atk agents wait --root-session-id "C:\out\"; Get-Location', "PowerShell"),
        ("atk agents wait --root-session-id 'latest’; Get-Location #'", "PowerShell"),
    ],
)
def test_wait_inside_shell_syntax_still_counts_as_work(command, tool, _clock):
    """直接の一呼び出しに限って除外し、複合構文は内容を推測しない。"""
    for index in range(3):
        payload = _payload(f"shell-argv-{index}", tool, tool_input={"command": command}, duration_ms=10000)
        command_timing.start(payload)
        notice = "\n".join(command_timing.observe(payload))
        assert "実行時間: 10.0秒" in notice
        assert ("3件続いた" in notice) is (index == 2)


@pytest.mark.parametrize(
    "command",
    [
        r"C:\tools\atk.cmd agents wait --root-session-id parent",
        r"atk run-script wait-ci -- --baseline 'C:\ci\baseline.json'",
        r'atk run-script wait-ci --baseline "C:\O’Brien\before.json"',
    ],
)
def test_powershell_wait_outside_literal_subset_remains_in_review(command, _clock):
    """PowerShellの複合構文と同じ字形を含む入力は、純粋な待機でも判定しない。"""
    for index in range(3):
        payload = _payload(f"ps-path-{index}", "PowerShell", tool_input={"command": command}, duration_ms=10000)
        command_timing.start(payload)
        notice = "\n".join(command_timing.observe(payload))
        assert "実行時間: 10.0秒" in notice
        assert ("3件続いた" in notice) is (index == 2)


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
    assert "通知が示す保存出力と終了状態を回収" in output
    repeated = _invoke(user_prompt_submit.main, notification, capsys)
    assert "90.0秒" not in repeated
