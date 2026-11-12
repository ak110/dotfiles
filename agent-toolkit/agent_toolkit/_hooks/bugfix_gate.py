"""ユーザーの欠陥指摘を受けたターンで、`agent-basics:bugfix`を起動せずに報告する操作を遮断する。

`send_to_user`の呼出し（PreToolUse）と、応答本文だけで終えるStopの両方が同じ判定（`bugfix_pending`）を使う。
判定はtranscriptの観測だけで決まる。最後の人間の発話が欠陥指摘の語を含み、そのターンに`Skill`による
`agent-basics:bugfix`の呼出しが無い場合に成立する。発話の意味は判定しない。語の一致は誤検出があり得るが、
遮断された主体は`agent-basics:bugfix`を起動すれば同じ操作を通せるため、誤検出でも作業は止まらない。

通知を遮断にする根拠: 警告だけでは、欠陥指摘の後にbugfixを起動せず原因の推測をユーザーへ報告した事例があった
（2026年11月12日の統括セッション）。起動の有無は機械的に確定でき、起動で回復できるため、
`agent-basics:writing-standards`の`references/claude-hooks-block-warn-eligibility.md`の遮断の条件に当たる。
Stopは`stop_hook_active`が真の呼出しで遮断せず、再帰を避ける。

委譲先での実行可否: ユーザーの発話を受けるメインだけを対象とし、サブエージェントと委譲先は判定しない。
`send_to_user`を持つClaude Codeだけで判定する。
"""

import json
import re

from agent_toolkit._hooks import notice

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.host import is_codex_payload
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
        fix="`Skill`で`agent-basics:bugfix`を起動し、その手順で診断してから報告せよ。"
        "欠陥の指摘ではない発話の場合も、起動すれば同じ操作を通せる。",
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
