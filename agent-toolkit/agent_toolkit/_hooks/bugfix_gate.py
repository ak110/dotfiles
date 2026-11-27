"""実際の検証・CIの失敗を観測し、同じ主体へ一度だけ診断を案内する。  pyfltr: allow-comment

発話の分類、スキルの取得方法、報告の書式は、送達と終了の条件にしない。
"""

import json
import re
lazy import os
lazy from pathlib import PurePath

lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._common.bash_invocations import extract_bash_effects
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
# pyfltr: allow-comment -- Codex 0.172.0の保存出力64KiBがJSONで最大6倍に広がる分と、後続記録の余裕を持つ。
_COMMAND_TRANSCRIPT_TAIL_BYTES = 1024 * 1024


def _shell_exit_code(payload: dict) -> int | None:
    response = payload.get("tool_response")
    code = response.get("exit_code") if isinstance(response, dict) else None
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    path = payload.get("transcript_path")
    identifier = payload.get("tool_use_id")
    turn_id = payload.get("turn_id")
    if not (
        is_codex_payload(payload)
        and isinstance(path, str)
        and isinstance(identifier, str)
        and identifier
        and isinstance(turn_id, str)
        and turn_id
    ):
        return None
    # pyfltr: allow-comment -- Codexのシェル応答は出力本文だけで、終了コードは会話の実行記録に残る。
    try:
        size = os.stat(path).st_size
    except OSError, ValueError:
        return None
    offset = max(0, size - _COMMAND_TRANSCRIPT_TAIL_BYTES)
    data = transcript.read_transcript_bytes(path, offset=max(0, offset - 1))
    if offset and data is not None:
        data = data.partition(b"\n")[2]
    for line in reversed(split_lines(data or b"")):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        event = record.get("payload") if isinstance(record, dict) and record.get("type") == "event_msg" else None
        if not isinstance(event, dict) or event.get("type") != "item_completed" or event.get("turn_id") != turn_id:
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


def _has_single_verification_exit(tool_input: dict) -> bool:
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    segments = extract_execution_segments(command)
    if len(segments) != 1 or not segments[0].tokens:
        return False
    effects = extract_bash_effects(command)
    if len(effects) != 1:
        return False
    effect = effects[0]
    if not effect.execution_known or not effect.arguments_known or not effect.assignments_definite:
        return False
    if effect.background or effect.captured or effect.cwd_scope:
        return False
    tokens = effect.segment.tokens
    if not tokens or PurePath(tokens[0]).name not in {"pyfltr", "pytest", "py.test"}:
        return False
    # pyfltr: allow-comment -- 実行前置語とファイルを開くリダイレクトは、検証前の失敗を返しうる。
    return segments[0].raw_tokens in {tokens, (*tokens, "2>&1")}


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
        failed = _shell_exit_code(payload) in (1, 6)
    elif is_verification_operation(tool_name, tool_input):
        if (
            not failed
            and tool_name == "Bash"
            and not isinstance(code, int)
            and is_codex_payload(payload)
            and _has_single_verification_exit(tool_input)
        ):
            recorded_code = _shell_exit_code(payload)
            # pyfltr: allow-comment -- 126・127はシェルの起動失敗、128以上はシグナルとの区別がつかない。
            failed = recorded_code is not None and 0 < recorded_code < 126
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
