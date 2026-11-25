"""誤りへの対処を「既存の規範で足りる」と述べて見送る報告を、ユーザーへ届く前に遮断する。  pyfltr: allow-comment

規範があるのに誤りが起きたことは、その規範だけでは防げないことを示す。
それでも「既存の基準で足りる」「規範は足さない」と述べて構造・検査・誘因の除去を見送る報告が繰り返され、
ユーザーが何度も同じ是正をした（2026年11月22日の対話）。
`agent-basics:bugfix`の`references/prevention.md`「再発防止を採用しない場合」も、既に規範があることを
採用しない理由から除く。条文の想起だけに頼る対策では再発が止まらなかったため、表現を機械的に検出して遮断する。

判定は語の一致だけで決まり、引用の素材（コードブロック、インラインコード、`>`で始まる引用行、かぎ括弧の中）と否定の形は除く。
言い回しの判定は言い換えで漏れるため、ユーザーへ届く報告では、見出しが`再発防止`で終わる行の本文が
対策の種類の語（`_common/prevention_kinds.py`）で始まることも求める。理由の言い回しによらず、
種類も区分名も示さずに再発防止を見送る報告を止める（2026年11月24日、「規則は既にあり守れなかっただけ」で見送った報告）。
この検査はターン途中の発話と思考には適用しない。記録の下書きを含み、ユーザーへ届いた報告ではないため。
誤検出でも作業を止めないよう、同じ主体が同じ発話の中で同じ本文を再発行した場合は通す。
自由文の照合は是正の有無を問わない。Stopのやり直しでも本文が変われば、その本文を検査する。

委譲先での実行可否: ユーザーへ届く本文（`send_to_user`、`AskUserQuestion`・`ExitPlanMode`、メインの最終応答）だけを対象とし、
委譲先の最終応答は委譲元が受領して判断するため対象外とする。
"""

import hashlib
import json
import re

from agent_toolkit._hooks import notice
lazy from agent_toolkit._common import send_misdetection
lazy from agent_toolkit._common import transcript as _transcript
lazy from agent_toolkit._common.prevention_kinds import KINDS_GUIDANCE, starts_with_kind
lazy from agent_toolkit._common.session_state import update_state
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context, resolve_hook_agent_id
lazy from agent_toolkit._hooks.bugfix_gate import QUOTED_MATERIAL_RE, prevention_lines
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.user_response_advisor import latest_human_turn, resend_scope

_SUBJECT = r"(?:既存|現行|既に?ある|今ある)の?(?:規範|基準|規定|条文|対策|手順|仕組み|ルール)"

_NOT_NEGATED = r"(?!(?:で(?:は)?|て(?:い)?)?な[いかく]|ず|ません|ていません)"

EXCUSE_RE = re.compile(
    _SUBJECT
    + r"(?:で|が|により|によって|だけで)?(?:十分|足り|足る|被覆|カバー|対応済み|防げ)"
    + _NOT_NEGATED
    + r"|規範(?:は|を|の追加は)(?:足さない|追加しない|不要)"
)

_RULE_EXISTS = (
    r"(?:(?:既存|現行)の)?(?:規則|規範|手順|ルール)(?:には|に|は|が)(?:既に|すでに|もう)?"
    r"(?:書かれている|ある|存在する|明記されている|定められている)"
)
_DISMISSAL = (
    r"(?:再発防止(?:策)?|対策|AWI)(?:は|を)?(?:見送る|採らない|しない|投入しない|追加しない)"
    r"(?!のではな|わけではな)"
)
_FREEFORM_EXCUSE_RE = re.compile(
    rf"(?:{_RULE_EXISTS}|(?:守れ|守られ|従え)ていなかっただけ)[^。！？!?\n]*?{_DISMISSAL}"
    rf"|{_DISMISSAL}。[ \t]*{_RULE_EXISTS}(?:ため|から)だ"
)

_KAGI_QUOTE_RE = re.compile(r"「[^「」\n]*」")

_FLAGGED_STATE_KEY = "norm_excuse_flagged_intermediate"
_RETRY_LIMIT = 16
_RESEND_KEY = "norm_excuse_resend"

_block_notice = notice.block_formatter("norm_excuse_gate")


def find_excuse(text: str) -> str | None:
    unquoted = _KAGI_QUOTE_RE.sub("", QUOTED_MATERIAL_RE.sub("", text))
    match = EXCUSE_RE.search(unquoted) or _FREEFORM_EXCUSE_RE.search(unquoted)
    return match.group(0) if match else None


def find_untyped_prevention(text: str) -> str | None:
    return next((line for line, body in prevention_lines(text) if body and not starts_with_kind(body)), None)


def _consume_retry(payload: dict, text: str) -> bool:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return False
    return notice.consume_resend(
        session_id,
        resolve_hook_agent_id(payload),
        _RESEND_KEY,
        text,
        label="既存の規範を理由に対処を見送る言い回しの本文を再発行した",
        scope=resend_scope(payload),
        limit=_RETRY_LIMIT,
    )


def _block_body(phrase: str, delivery: str, hint: str = "") -> str:
    return _block_notice(
        f"本文が既存の規範を理由に対処を見送る言い回し「{phrase}」を含むため、{delivery}を止めた。"
        "規範があっても起きた誤りは、その規範だけでは再発を防げない。" + hint,
        fix=(
            "`agent-basics:bugfix`の`references/prevention.md`「案を比べる順」に従い、誘因の除去・構造・検査のどれかを選んで"
            "処置し、本文を書き直してください。案内を確認し、本文を維持する場合は同じ主体から同じ本文を再発行してよい。"
        ),
    )


def _untyped_prevention_body(line: str, delivery: str, hint: str = "") -> str:
    return _block_notice(
        f"本文の再発防止の行「{line}」が対策の種類の語で始まらないため、{delivery}を止めた。"
        "既存の規則があることや守れなかったことは、再発防止を見送る理由にならない。" + hint,
        fix=(
            f"`agent-basics:bugfix`の`references/prevention.md`に従って策を決め、行の先頭に種類の語を置いてください。{KINDS_GUIDANCE}。"
            "行が再発防止の記録でないなら、同じ主体から同じ本文を再発行してよい。"
        ),
    )


def text_block(payload: dict, text: object, delivery: str, *, retry: set[str] | None = None) -> str | None:
    if not isinstance(text, str):
        return None
    checks = retry or set()
    phrase = None if "norm-excuse" in checks else find_excuse(text)
    untyped = None if phrase is not None or "prevention-kind" in checks else find_untyped_prevention(text)
    if phrase is None and untyped is None:
        return None
    if not checks and _consume_retry(payload, text):
        return None
    if phrase is not None:
        return _block_body(phrase, delivery, send_misdetection.retry_hint("norm-excuse") if retry is not None else "")
    return _untyped_prevention_body(
        untyped or "",
        delivery,
        send_misdetection.retry_hint("prevention-kind") if retry is not None else "",
    )


def _codex_turn_texts(lines: list[str]) -> list[str]:
    """Codexの記録から、最後の`task_started`以後の発話本文と思考の要約を返す。"""
    texts: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = entry.get("payload") if isinstance(entry, dict) else None
        if not isinstance(item, dict):
            continue
        if entry.get("type") == "event_msg" and item.get("type") == "task_started":
            texts = []
        elif entry.get("type") == "response_item":
            if item.get("type") == "message" and item.get("role") == "assistant":
                blocks, kind = item.get("content"), "output_text"
            elif item.get("type") == "reasoning":
                blocks, kind = item.get("summary"), "summary_text"
            else:
                continue
            if isinstance(blocks, list):
                texts.extend(
                    text
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == kind and isinstance(text := block.get("text"), str)
                )
    return texts


def _intermediate_texts(payload: dict) -> list[str]:
    path = payload.get("transcript_path")
    lines = _transcript.read_transcript_lines(path) if isinstance(path, str) and path else None
    if not lines:
        return []
    last = payload.get("last_assistant_message")
    if is_codex_payload(payload):
        return [text for text in _codex_turn_texts(lines) if text != last]
    turn = latest_human_turn(lines)
    if turn is None:
        return []
    _prompt, entries = turn
    texts: list[str] = []
    for entry in entries:
        message = entry.get("message")
        content = message.get("content") if entry.get("type") == "assistant" and isinstance(message, dict) else None
        if isinstance(content, list):
            texts.extend(
                text
                for block in content
                if isinstance(block, dict)
                and isinstance(text := block.get("text" if block.get("type") == "text" else "thinking"), str)
                and block.get("type") in {"text", "thinking"}
            )
    return [text for text in texts if text != last]


def _first_flag(session_id: str, text: str) -> bool:
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    first = False

    def update(state: dict) -> dict:
        nonlocal first
        values = [value for value in state.get(_FLAGGED_STATE_KEY, []) if isinstance(value, str)]
        first = fingerprint not in values
        if first:
            values.append(fingerprint)
        state[_FLAGGED_STATE_KEY] = values[-64:]
        return state

    return first if update_state(session_id, update) else False


def intermediate_block(session_id: str, payload: dict) -> str | None:
    for text in _intermediate_texts(payload):
        if (phrase := find_excuse(text)) is not None and _first_flag(session_id, text):
            return _block_body(phrase, "ターンの終了（途中の発話）")
    return None


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    if not is_main_agent_context(payload):
        return "approve", ""
    body = text_block(payload, payload.get("last_assistant_message"), "ターンの終了") or intermediate_block(session_id, payload)
    if body is None:
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_norm_excuse", {})
    return "block", body


def main(payload_text: str) -> int:
    decision, body = evaluate(payload_text)
    print(json.dumps({"decision": decision, "reason": body} if decision == "block" else {}, ensure_ascii=False))
    return 0
