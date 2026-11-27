"""実際の検証・CIの失敗を観測し、同じ主体へ一度だけ診断を案内する。  pyfltr: allow-comment

発話の分類、スキルの取得方法、報告の書式は、送達と終了の条件にしない。
"""

import json
import re
lazy import os

lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._common.json_lines import split_lines
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._hooks import notice
lazy from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse.operation_skills import is_verification_operation

# フェンス付きコードブロック、インラインコードおよび`>`で始まる引用行は、貼り付けた素材として扱う。
QUOTED_MATERIAL_RE = re.compile(r"^[ \t]*(```|~~~)[\s\S]*?^[ \t]*\1[^\n]*$|`[^`\n]+`|^[ \t]*>[^\n]*$", re.MULTILINE)

_failure_notice = notice.formatter("verification-bugfix")
FAILURE_STATE_KEY = "verification_bugfix"


def _ci_exit_code(payload: dict) -> int | None:
    response = payload.get("tool_response")
    code = response.get("exit_code") if isinstance(response, dict) else None
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    path = payload.get("transcript_path")
    identifier = payload.get("tool_use_id")
    if not is_codex_payload(payload) or not isinstance(path, str) or not isinstance(identifier, str) or not identifier:
        return None
    # Codexの応答文字列には終了コードがない。wait-ciの完了時だけ末尾を読み、
    # 同じturn・実行IDのホスト記録を使う。長い会話全体を毎回解析しない。
    try:
        size = os.stat(path).st_size
    except OSError, ValueError:
        return None
    data = transcript.read_transcript_bytes(path, offset=max(0, size - 262144))
    for line in reversed(split_lines(data or b"")):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        event = record.get("payload") if isinstance(record, dict) and record.get("type") == "event_msg" else None
        if not isinstance(event, dict) or event.get("type") != "item_completed" or event.get("turn_id") != payload["turn_id"]:
            continue
        item = event.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "CommandExecution"
            and item.get("id") == identifier
            and event.get("thread_id") == payload["session_id"]
        ):
            code = item.get("exit_code")
            return code if isinstance(code, int) and not isinstance(code, bool) else None
    return None


def _wait_ci_operation(tool_name: str, tool_input: dict) -> bool:
    command = tool_input.get("command")
    return (
        tool_name == "Bash"
        and isinstance(command, str)
        and any(
            segment.resolved and is_atk_command(segment.tokens) and segment.tokens[1:3] == ("run-script", "wait-ci")
            for segment in extract_execution_segments(command)
        )
    )


def observe_verification(payload: dict) -> None:
    """現在の検証結果の失敗を、既存セッション状態へ対応付ける。  pyfltr: allow-comment"""
    session_id = payload.get("session_id")
    tool_input = payload.get("tool_input")
    if not isinstance(session_id, str) or not session_id or not isinstance(tool_input, dict):
        return
    actor = resolve_hook_agent_id(payload)
    tool_name = payload.get("tool_name", "")
    response = payload.get("tool_response")
    failed = payload.get("hook_event_name") == "PostToolUseFailure"
    code = response.get("exit_code") if isinstance(response, dict) else None
    if isinstance(response, dict):
        failed = failed or bool(
            response.get("isError") or response.get("is_error") or response.get("interrupted") or response.get("error")
        )
    if _wait_ci_operation(tool_name, tool_input):
        # wait-ciの1はCI失敗、6は途中の新規失敗。待機timeoutやrun未登録は別の状態である。
        failed = _ci_exit_code(payload) in (1, 6)
    elif is_verification_operation(tool_name, tool_input):
        failed = failed or isinstance(code, int) and not isinstance(code, bool) and code != 0
    else:
        return
    identifier = payload.get("tool_use_id")
    if not failed or not isinstance(identifier, str) or not identifier:
        return

    def remember(state: dict) -> dict | None:
        actors = state.setdefault(FAILURE_STATE_KEY, {})
        if actors.get(actor, {}).get("failure") == identifier:
            return None
        actors[actor] = {"failure": identifier, "pending": True}
        return state

    update_state(session_id, remember)


def verification_notice(payload: dict, _tool_name: str, _tool_input: dict, session_id: str) -> str:
    actor = resolve_hook_agent_id(payload)
    current = read_state(session_id).get(FAILURE_STATE_KEY, {}).get(actor)
    if not isinstance(current, dict) or not current.get("pending"):
        return ""
    failure = ""

    def consume(state: dict) -> dict | None:
        nonlocal failure
        current = state.get(FAILURE_STATE_KEY, {}).get(actor)
        if not isinstance(current, dict) or not current.get("pending"):
            return None
        failure = current["failure"]
        current["pending"] = False
        return state

    if not update_state(session_id, consume) or not failure:
        return ""
    return _failure_notice(
        "検証・CIの失敗を観測した。",
        tag="warn",
        removable_cause=True,
        subject=failure,
        fix="失敗の診断を確かめ、`agent-basics:bugfix`の適用条件に従って対処してください。",
    )
