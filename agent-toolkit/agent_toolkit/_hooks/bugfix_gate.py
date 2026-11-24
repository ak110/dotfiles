"""ユーザーの欠陥指摘を受けたターンで、`agent-basics:bugfix`を起動せずに報告する操作を遮断する。  pyfltr: allow-comment

送達前とStopで、最新の実ユーザー発話に届いた最初の分類を使う。
未分類時は分類の送達だけを許可し、是正なら同じ発話に対するbugfixの起動と種類付きの再発防止を求める。
起動に失敗したことをセッション記録で確認できる時は、その事実を報告できるようにする。

通知を遮断にする根拠: 警告だけでは、欠陥指摘の後にbugfixを起動せず原因の推測をユーザーへ報告した事例があった
（2026年11月12日の統括セッション）。起動の有無は機械的に確定でき、起動で回復できるため、
`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`の遮断の条件に当たる。

委譲先での実行可否: ユーザー発話に対する遮断は、`send_to_user`を持つClaude Codeのメインだけを対象とする。
検証失敗後の起動案内は、失敗を観測したメインと委譲先へ、修正・commit・再検証の前に警告する。
こちらは構造化した失敗と同じ主体の起動成功を使い、自然文の中継だけから失敗を断定しない。
"""

import json
import re
lazy import os

from agent_toolkit._hooks import notice
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._common.json_lines import split_lines
lazy from agent_toolkit._common.prevention_kinds import starts_with_kind
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse import operation_skills
lazy from agent_toolkit._hooks.pretooluse.shell_checks import git_subcommand_tokens
lazy from agent_toolkit._hooks.user_response_advisor import latest_human_turn_context

# フェンス付きコードブロック、インラインコードおよび`>`で始まる引用行は、貼り付けた素材として扱う。
# posttooluseがbugfix_gateを取り込む向きに保つため、共有する定義はこちらへ置く。
QUOTED_MATERIAL_RE = re.compile(r"^[ \t]*(```|~~~)[\s\S]*?^[ \t]*\1[^\n]*$|`[^`\n]+`|^[ \t]*>[^\n]*$", re.MULTILINE)
# 見出しが`再発防止`（`再発防止策`）で終わる行。`**ご指摘への再発防止**:`のような飾りと前置きを含む。  pyfltr: allow-comment
_PREVENTION_LINE_RE = re.compile(
    r"^[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?(?P<head>[^:：\n。、]{0,20}?再発防止策?)[ \t]*[:：](?P<text>[^\n]*)$",
    re.MULTILINE,
)

BUGFIX_SKILL = "agent-basics:bugfix"
_BUGFIX_SKILLS = frozenset({BUGFIX_SKILL, "bugfix"})
_UNDERSTANDING = re.compile(r"^(?:#{1,6}\s+)?[*_]*発話の理解(?:[*_]*[:：]|[*_]*$)")
_CLASSIFICATION = re.compile(r"\s*(?:[-*+]\s+)?分類[:：]\s*(是正|非是正)\s*")


def is_classification_line(text: str) -> bool:
    return _CLASSIFICATION.fullmatch(text) is not None


def _understanding_kind(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    lines = QUOTED_MATERIAL_RE.sub("", text).strip().splitlines()
    if not lines or not _UNDERSTANDING.match(lines[0]):
        return None
    match = _CLASSIFICATION.fullmatch(lines[1]) if len(lines) > 1 else None
    return match[1] if match else ""


def _turn_classification(entries: list[dict]) -> str | None:
    calls: dict[str, str] = {}
    for entry in entries:
        message = entry.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                continue
            if (
                entry.get("type") == "assistant"
                and block.get("type") == "tool_use"
                and str(block.get("name", "")).endswith(transcript.SEND_TO_USER_TOOL_SUFFIX)
                and isinstance(identifier := block.get("id"), str)
                and isinstance(args := block.get("input"), dict)
                and (classification := _understanding_kind(args.get("message")))
            ):
                calls[identifier] = classification
            elif (
                entry.get("type") == "user"
                and block.get("type") == "tool_result"
                and block.get("is_error") is not True
                and isinstance(identifier := block.get("tool_use_id"), str)
                and identifier in calls
            ):
                # 最初に届いた分類を固定し、後から報告を通すための分類変更を使わない。  pyfltr: allow-comment
                return calls[identifier]
    return None


_block_notice = notice.block_formatter("bugfix_gate")
_failure_notice = notice.formatter("verification-bugfix")
FAILURE_STATE_KEY = "verification_bugfix"
_REPL_SKILL_TURN_KEY = "bugfix_repl_human_turn"
_REPL_SKILL_CALL_KEY = "bugfix_repl_skill_call"


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


def mark_started(session_id: str, actor: str) -> None:
    """失敗の後に同じ主体が成功させた起動だけで未対処の印を解除する。"""

    def clear(state: dict) -> dict | None:
        current = state.get(FAILURE_STATE_KEY, {}).get(actor)
        if not isinstance(current, dict) or not current.get("pending"):
            return None
        current["pending"] = False
        return state

    update_state(session_id, clear)


def observe_verification(payload: dict) -> None:
    """現在のツール結果の失敗とSkillの起動成功を、既存セッション状態へ対応付ける。"""
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
    if tool_name == "Skill" and tool_input.get("skill") in _BUGFIX_SKILLS:
        if response and not failed and code in (None, 0):
            mark_started(session_id, actor)
            _observe_repl_skill_success(payload, session_id)
        return
    if _wait_ci_operation(tool_name, tool_input):
        # wait-ciの1はCI失敗、6は途中の新規失敗。待機timeoutやrun未登録は別の状態である。
        failed = _ci_exit_code(payload) in (1, 6)
    elif operation_skills.is_verification_operation(tool_name, tool_input):
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


def verification_notice(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> str:
    current = read_state(session_id).get(FAILURE_STATE_KEY, {}).get(resolve_hook_agent_id(payload), {})
    if not isinstance(current, dict) or not current.get("pending"):
        return ""
    command = tool_input.get("command")
    commit = (
        tool_name == "Bash"
        and isinstance(command, str)
        and any(
            (parsed := git_subcommand_tokens(segment)) is not None
            and parsed[0] == "commit"
            and not {"--dry-run", "--help", "-h"}.intersection(parsed[1])
            for segment in extract_execution_segments(command)
        )
    )
    if not (
        commit
        or _wait_ci_operation(tool_name, tool_input)
        or operation_skills.is_verification_operation(tool_name, tool_input)
        or operation_skills.is_body_writing(tool_name, tool_input, str(payload.get("cwd", "")))
    ):
        return ""
    return _failure_notice(
        "検証・CIの失敗を観測した後、この主体のbugfix起動成功を確認できない。",
        tag="warn",
        removable_cause=True,
        subject=current["failure"],
        fix="`agent-basics:bugfix`を起動してください。反復する失敗は同スキルの「書き出す場面」と`references/recurrence.md`に従ってください。",
    )


def _bugfix_calls(entries: list[dict]) -> tuple[bool, bool]:
    failed = False
    calls: dict[str, bool] = {}
    for entry in entries:
        message = entry.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        receipt = entry.get("toolUseResult")
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                continue
            if entry.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") == "Skill":
                args, identifier = block.get("input"), block.get("id")
                skill = args.get("skill") if isinstance(args, dict) else None
                if isinstance(identifier, str) and identifier and isinstance(skill, str) and skill in _BUGFIX_SKILLS:
                    calls[identifier] = entry.get("isVirtual") is not True
            elif entry.get("type") == "user" and block.get("type") == "tool_result":
                identifier = block.get("tool_use_id")
                if not isinstance(identifier, str) or identifier not in calls:
                    continue
                success = receipt.get("success") if isinstance(receipt, dict) else None
                name = receipt.get("commandName") if isinstance(receipt, dict) else None
                if block.get("is_error") is True or success is False:
                    calls[identifier] = False
                    failed = True
                elif (
                    entry.get("isVirtual") is True
                    and block.get("is_error") is False
                    and success is True
                    and isinstance(name, str)
                    and name in _BUGFIX_SKILLS
                ):
                    calls[identifier] = True
    return any(calls.values()), failed


def _read_human_lines(payload: dict) -> list[str] | None:
    if is_codex_payload(payload) or not agent_id.is_main_agent_context(payload):
        return None
    path = payload.get("transcript_path")
    return transcript.read_transcript_lines(path) if isinstance(path, str) and path else None


def observe_repl_skill_start(payload: dict, session_id: str) -> None:
    tool_id, tool_input = payload.get("tool_use_id"), payload.get("tool_input")
    if (
        not isinstance(tool_id, str)
        or not tool_id.startswith("repl_")
        or not isinstance(tool_input, dict)
        or not isinstance(skill := tool_input.get("skill"), str)
        or skill not in _BUGFIX_SKILLS
    ):
        return
    if (lines := _read_human_lines(payload)) is None or (turn := latest_human_turn_context(lines)) is None:
        return
    _, _, human_id = turn
    if human_id is None:
        return

    def remember(state: dict) -> dict:
        state[_REPL_SKILL_CALL_KEY] = {"id": tool_id, "human": human_id}
        return state

    update_state(session_id, remember)


def _observe_repl_skill_success(payload: dict, session_id: str) -> None:
    tool_id, response = payload.get("tool_use_id"), payload.get("tool_response")
    if (
        payload.get("hook_event_name") != "PostToolUse"
        or not isinstance(tool_id, str)
        or not tool_id.startswith("repl_")
        or not isinstance(response, dict)
        or response.get("success") is not True
        or not isinstance(command := response.get("commandName"), str)
        or command not in _BUGFIX_SKILLS
    ):
        return

    def remember(state: dict) -> dict | None:
        call = state.get(_REPL_SKILL_CALL_KEY)
        if not isinstance(call, dict) or call.get("id") != tool_id:
            return None
        state.pop(_REPL_SKILL_CALL_KEY)
        state[_REPL_SKILL_TURN_KEY] = call["human"]
        return state

    update_state(session_id, remember)


def prevention_lines(text: str) -> list[tuple[str, str]]:
    unquoted = QUOTED_MATERIAL_RE.sub(
        lambda match: match[0] if match[0].startswith("`") and starts_with_kind(match[0]) else "", text
    )
    return [(match[0].strip(), match["text"].strip()) for match in _PREVENTION_LINE_RE.finditer(unquoted.replace("**", ""))]


def _has_typed_prevention(text: object) -> bool:
    if not isinstance(text, str):
        return False
    bodies = [body for _, body in prevention_lines(text) if body]
    return bool(bodies) and all(starts_with_kind(body) for body in bodies)


def _turn_block(
    payload: dict, turn: tuple[str, list[dict], str | None], message: object, delivery: str, *, can_classify: bool
) -> str | None:
    _, entries, human_id = turn
    kind = _turn_classification(entries)
    if kind is None:
        if can_classify and _understanding_kind(message):
            return None
        return _block_notice(
            f"最新の発話の理解に分類がないため、{delivery}を止めた。",
            fix="`send_to_user`で発話の理解を送り、見出しの直後に `分類: 是正` または `分類: 非是正` を書いてください。"
            "是正なら `agent-basics:bugfix` を起動してください。",
        )
    if kind != "是正":
        return None

    session_id = payload.get("session_id")
    repl_human_id = read_state(session_id).get(_REPL_SKILL_TURN_KEY) if isinstance(session_id, str) and session_id else None
    started, failed = _bugfix_calls(entries)
    if not started and not (human_id is not None and human_id == repl_human_id):
        if failed:
            return None
        return _block_notice(
            f"是正を受けた後に`agent-basics:bugfix`を起動していないため、{delivery}を止めた。",
            fix="`agent-basics:bugfix`を起動し、その手順で診断してから報告してください。",
        )
    if _has_typed_prevention(message):
        return None
    return _block_notice(
        f"種類付きの再発防止の行がないため、{delivery}を止めた。",
        fix="`agent-basics:bugfix`の記録に従い、種類付きの `再発防止:` の行を書いてください。",
    )


def send_block(payload: dict, message: object = None) -> str | None:
    lines = _read_human_lines(payload)
    if lines is None or (turn := latest_human_turn_context(lines)) is None:
        return None
    return _turn_block(payload, turn, message, "ユーザーへの送達", can_classify=True)


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    lines = _read_human_lines(payload)
    if lines is None or (turn := latest_human_turn_context(lines)) is None:
        return "approve", ""
    body = _turn_block(payload, turn, payload.get("last_assistant_message"), "ターンの終了", can_classify=False)
    if body is None:
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_bugfix_not_invoked", {})
    return "block", body


def main(payload_text: str) -> int:
    decision, body = evaluate(payload_text)
    print(json.dumps({"decision": decision, "reason": body} if decision == "block" else {}, ensure_ascii=False))
    return 0
