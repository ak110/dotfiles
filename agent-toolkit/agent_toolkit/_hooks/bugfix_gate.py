"""ユーザーの欠陥指摘を受けたターンで、`agent-basics:bugfix`を起動せずに報告する操作を遮断する。

`send_to_user`の呼出し（PreToolUse）と、応答本文だけで終えるStopの両方が同じ判定（`bugfix_pending`）を使う。
この遮断の判定はtranscriptの観測だけで決まる。最後の人間の発話が欠陥指摘の語を含み、そのターンに`Skill`による
`agent-basics:bugfix`の呼出しが無い場合に成立する。発話の意味は判定しない。語の一致は誤検出があり得るが、
遮断された主体は`agent-basics:bugfix`を起動すれば同じ操作を通せるため、誤検出でも作業は止まらない。

通知を遮断にする根拠: 警告だけでは、欠陥指摘の後にbugfixを起動せず原因の推測をユーザーへ報告した事例があった
（2026年11月12日の統括セッション）。起動の有無は機械的に確定でき、起動で回復できるため、
`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`の遮断の条件に当たる。
Stopは`stop_hook_active`が真の呼出しで遮断せず、再帰を避ける。

委譲先での実行可否: ユーザー発話に対する遮断は、`send_to_user`を持つClaude Codeのメインだけを対象とする。
検証失敗後の起動案内は、失敗を観測したメインと委譲先へ、修正・commit・再検証の前に警告する。
こちらは構造化した失敗と同じ主体の起動成功を使い、自然文の中継だけから失敗を断定しない。
"""

import json
import re
lazy import os

from agent_toolkit._hooks import notice

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._common.json_lines import split_lines
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse import operation_skills
lazy from agent_toolkit._hooks.pretooluse.shell_checks import git_subcommand_tokens
lazy from agent_toolkit._hooks.user_response_advisor import latest_human_turn

# フェンス付きコードブロック、インラインコードおよび`>`で始まる引用行は、貼り付けた素材として扱う。
# 例示の語と調査を求める語、欠陥の指摘を表す語は、引用を除いたユーザー自身の依頼文から探す。
# posttooluseがbugfix_gateを取り込む向きに保つため、共有する定義はこちらへ置く。
QUOTED_MATERIAL_RE = re.compile(r"^[ \t]*(```|~~~)[\s\S]*?^[ \t]*\1[^\n]*$|`[^`\n]+`|^[ \t]*>[^\n]*$", re.MULTILINE)
_BUGFIX_SKILLS = frozenset({"agent-basics:bugfix", "bugfix"})
# 欠陥の指摘を表す語。貼り付けた素材（コードブロック、インラインコード、引用行）は除いた本文へ適用する。
_DEFECT_RE = re.compile(
    r"バグ|不具合|不良|欠陥|おかしい|おかしく|間違|誤り|誤っ|誤動作|壊れ|動かな|動いて(?:い)?ない|機能しな|"
    r"できて(?:い)?ない|なって(?:い)?ない|ミス|\b(?:bug|broken|regression)\b",
    re.IGNORECASE,
)

_block_notice = notice.block_formatter("bugfix_gate")
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
        return
    if _wait_ci_operation(tool_name, tool_input):
        # wait-ciの1はCI失敗、6は途中の新規失敗。待機timeoutやrun未登録は別の状態である。
        failed = _ci_exit_code(payload) in (1, 6)
    elif operation_skills.operation_matches("verification", tool_name, tool_input):
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
    """未起動の主体が修正や検証の再実行へ進む場合だけ、非遮断の起動案内を返す。"""
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
        or operation_skills.operation_matches("verification", tool_name, tool_input)
        or operation_skills.operation_matches("body-writing", tool_name, tool_input, str(payload.get("cwd", "")))
    ):
        return ""
    return _failure_notice(
        "検証・CIの失敗を観測した後、この主体のbugfix起動成功を確認できない。",
        tag="warn",
        removable_cause=True,
        subject=current["failure"],
        fix="`agent-basics:bugfix`を起動せよ。反復する失敗は同スキルの「書き出す場面」と`references/recurrence.md`に従え。",
    )


def is_defect_report(prompt: str) -> bool:
    """人間の発話が欠陥の指摘を含むかを、語の一致だけで返す。"""
    return _DEFECT_RE.search(QUOTED_MATERIAL_RE.sub("", prompt)) is not None


def _invokes_bugfix(entry: dict) -> bool:
    message = entry.get("message")
    content = message.get("content") if entry.get("type") == "assistant" and isinstance(message, dict) else None
    return isinstance(content, list) and any(
        isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == "Skill"
        and isinstance(block.get("input"), dict)
        and block["input"].get("skill") in _BUGFIX_SKILLS
        for block in content
    )


def bugfix_pending(lines: list[str]) -> bool:
    """最後の人間の発話が欠陥指摘で、そのターンに`agent-basics:bugfix`の起動が無ければ真を返す。"""
    turn = latest_human_turn(lines)
    if turn is None:
        return False
    prompt, entries = turn
    return is_defect_report(prompt) and not any(_invokes_bugfix(entry) for entry in entries)


def _block_body(delivery: str) -> str:
    return _block_notice(
        f"ユーザーの発話が欠陥の指摘を含み、このターンで`agent-basics:bugfix`を起動していない。{delivery}を止めた。",
        fix="`agent-basics:bugfix`を起動し、その手順で診断してから報告せよ。"
        "語の一致による検出のため、発話が欠陥の指摘に当たるかも同スキルの手順で確認せよ。",
    )


def _pending_for_main(payload: dict) -> bool:
    if is_codex_payload(payload) or not agent_id.is_main_agent_context(payload):
        return False
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return False
    lines = transcript.read_transcript_lines(path)
    return lines is not None and bugfix_pending(lines)


def send_block(payload: dict) -> str | None:
    """`send_to_user`の呼出し前の判定。遮断する場合は通知本文を返す。"""
    return _block_body("ユーザーへの送達") if _pending_for_main(payload) else None


def evaluate(payload_text: str) -> tuple[str, str]:
    """応答本文だけでターンを終える場合も、同じ判定で終了を遮断する。"""
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    if payload.get("stop_hook_active") is True or not _pending_for_main(payload):
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_bugfix_not_invoked", {})
    return "block", _block_body("ターンの終了")


def main(payload_text: str) -> int:
    """Stop判定の結果を標準出力へ返す。"""
    decision, body = evaluate(payload_text)
    print(json.dumps({"decision": decision, "reason": body} if decision == "block" else {}, ensure_ascii=False))
    return 0
