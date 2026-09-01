"""agents_serverの未観測作業を通知するStopフックを検証する。"""

import json
import os
import pathlib

import _fork_runner
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE

_HOOK = pathlib.Path(__file__).resolve().parent / "hook.py"
_WARNING_BODY = (
    "agents_serverのsessionに、観測を試みていない作業が残っている。"
    "wait(session_id)で観測するか、結果が不要ならkill(session_id)で破棄してから終了する。"
    "send_messageは新しい作業を配送するだけで観測しないため、この警告は解消しない。"
    "観測しないまま終了すると、当該作業の成果を回収する主体が残らない。"
)


def _environment(state_directory: pathlib.Path) -> dict[str, str]:
    """セッション状態を検体ごとに分離する環境を返す。"""
    env = os.environ.copy()
    env.update({"TMPDIR": str(state_directory), "TEMP": str(state_directory), "TMP": str(state_directory)})
    return env


def _record_operation(
    state_directory: pathlib.Path,
    local_session_id: str,
    operation: str,
    response: dict[str, object],
) -> None:
    """PostToolUseを通してagents_serverの公開操作応答を記録する。"""
    remote_session_id = str(response["session_id"])
    tool_input: dict[str, object] = {"session_id": remote_session_id}
    if operation in {"start", "start_explore"}:
        tool_input = {"cwd": str(state_directory), "prompt": "委譲する"}
    elif operation == "send_message":
        tool_input["prompt"] = "続行する"
    result = _fork_runner.run_script(
        _HOOK,
        argv=("posttooluse",),
        input=json.dumps(
            {
                "session_id": local_session_id,
                "hook_event_name": "PostToolUse",
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": tool_input,
                "tool_response": {"structuredContent": response},
            },
            ensure_ascii=False,
        ),
        env=_environment(state_directory),
    )
    assert result.returncode == 0


def _run_stop(
    state_directory: pathlib.Path,
    local_session_id: str,
    *,
    stop_hook_active: bool = False,
) -> str:
    """指定セッションでStopフックを実行しstdoutを返す。"""
    result = _fork_runner.run_script(
        _HOOK,
        argv=("agents_server_session_advisor",),
        input=json.dumps({"session_id": local_session_id, "stop_hook_active": stop_hook_active}),
        env=_environment(state_directory),
    )
    assert result.returncode == 0
    return result.stdout


def _record_start(state_directory: pathlib.Path, local_session_id: str, remote_session_id: str) -> None:
    _record_operation(
        state_directory,
        local_session_id,
        "start",
        {"session_id": remote_session_id, "status": "running"},
    )


def _record_running_wait(state_directory: pathlib.Path, local_session_id: str, remote_session_id: str) -> None:
    _record_operation(
        state_directory,
        local_session_id,
        "wait",
        {"session_id": remote_session_id, "status": "running"},
    )


def _record_send_message(state_directory: pathlib.Path, local_session_id: str, remote_session_id: str) -> None:
    _record_operation(
        state_directory,
        local_session_id,
        "send_message",
        {"session_id": remote_session_id, "status": "running", "delivery": "steered"},
    )


def test_pending_observation_emits_stop_hook_event_and_additional_context(tmp_path: pathlib.Path) -> None:
    """未観測作業の警告はStop用additionalContextだけを返す。"""
    local_session_id = "pending-output"
    state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=local_session_id)
    state_path.write_text(
        json.dumps(
            {
                "agents_server_sessions": {
                    "session-b": {"pending_observation": True},
                    "session-a": {"pending_observation": True},
                }
            }
        ),
        encoding="utf-8",
    )

    output = json.loads(_run_stop(tmp_path, local_session_id))

    assert "decision" not in output
    hook_output = output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "Stop"
    assert _WARNING_BODY in hook_output["additionalContext"]
    assert "対象session: session-a, session-b" in hook_output["additionalContext"]


def test_start_only_record_emits_warning(tmp_path: pathlib.Path) -> None:
    """startだけの操作列は未観測作業を警告する。"""
    _record_start(tmp_path, "start-only", "remote-start-only")
    assert _run_stop(tmp_path, "start-only")


def test_running_wait_satisfies_observation(tmp_path: pathlib.Path) -> None:
    """runningを返すwaitでも観測義務を解消する。"""
    _record_start(tmp_path, "running-wait", "remote-running-wait")
    _record_running_wait(tmp_path, "running-wait", "remote-running-wait")
    assert _run_stop(tmp_path, "running-wait") == ""


def test_send_message_after_wait_reopens_observation(tmp_path: pathlib.Path) -> None:
    """観測後のsend_messageが新しい観測義務を発生させる。"""
    _record_start(tmp_path, "send-after-wait", "remote-send-after-wait")
    _record_running_wait(tmp_path, "send-after-wait", "remote-send-after-wait")
    _record_send_message(tmp_path, "send-after-wait", "remote-send-after-wait")
    assert _run_stop(tmp_path, "send-after-wait")


def test_wait_alone_clears_pending_observation(tmp_path: pathlib.Path) -> None:
    """send_message後の未観測作業をwaitだけで解消する。"""
    _record_start(tmp_path, "wait-clears", "remote-wait-clears")
    _record_running_wait(tmp_path, "wait-clears", "remote-wait-clears")
    _record_send_message(tmp_path, "wait-clears", "remote-wait-clears")
    _record_running_wait(tmp_path, "wait-clears", "remote-wait-clears")
    assert _run_stop(tmp_path, "wait-clears") == ""


def test_kill_alone_clears_pending_observation(tmp_path: pathlib.Path) -> None:
    """send_message後の未観測作業をwaitを経由せずkillだけで解消する。"""
    local_session_id = "kill-clears"
    remote_session_id = "remote-kill-clears"
    _record_start(tmp_path, local_session_id, remote_session_id)
    _record_running_wait(tmp_path, local_session_id, remote_session_id)
    _record_send_message(tmp_path, local_session_id, remote_session_id)
    _record_operation(
        tmp_path,
        local_session_id,
        "kill",
        {"session_id": remote_session_id, "status": "interrupted", "kill_requested": True},
    )
    assert _run_stop(tmp_path, local_session_id) == ""


def test_no_pending_observation_emits_nothing(tmp_path: pathlib.Path) -> None:
    """記録不在と全記録が偽の状態では何も出力しない。"""
    assert _run_stop(tmp_path, "no-state") == ""
    local_session_id = "all-observed"
    state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=local_session_id)
    state_path.write_text(
        json.dumps({"agents_server_sessions": {"remote": {"pending_observation": False}}}),
        encoding="utf-8",
    )
    assert _run_stop(tmp_path, local_session_id) == ""


def test_stop_hook_active_emits_nothing(tmp_path: pathlib.Path) -> None:
    """Stop継続後の再呼び出しでは判定を繰り返さない。"""
    _record_start(tmp_path, "active-stop", "remote-active-stop")
    assert _run_stop(tmp_path, "active-stop", stop_hook_active=True) == ""


def test_malformed_payload_allows_stop(tmp_path: pathlib.Path) -> None:
    """payload不正時は何も出力せず終了を許可する。"""
    result = _fork_runner.run_script(
        _HOOK,
        argv=("agents_server_session_advisor",),
        input="not-json",
        env=_environment(tmp_path),
    )
    assert result.returncode == 0
    assert result.stdout == ""
