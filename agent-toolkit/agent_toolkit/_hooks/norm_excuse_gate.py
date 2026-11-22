"""誤りへの対処を「既存の規範で足りる」と述べて見送る報告を、ユーザーへ届く前に遮断する。

規範があるのに誤りが起きたことは、その規範だけでは防げないことを示す。
それでも「既存の基準で足りる」「規範は足さない」と述べて構造・検査・誘因の除去を見送る報告が繰り返され、
ユーザーが何度も同じ是正をした（2026年11月22日の対話）。
`agent-basics:bugfix`の`references/prevention.md`「再発防止を採用しない場合」も、既に規範があることを
採用しない理由から除く。条文の想起だけに頼る対策は効かないため、表現を機械的に検出して遮断する。

判定は語の一致だけで決まり、引用の素材（コードブロック、インラインコード、`>`で始まる引用行）は除く。
誤検出でも作業を止めないよう、同じ主体が同じ本文を再発行した場合は一度だけ通す。
Stopは`stop_hook_active`が真の呼出しで遮断せず、再帰を避ける。

委譲先での実行可否: ユーザーへ届く本文（`send_to_user`、`AskUserQuestion`・`ExitPlanMode`、メインの最終応答）だけを対象とし、
委譲先の最終応答は委譲元が受領して判断するため対象外とする。
"""

import hashlib
import json
import re

from agent_toolkit._hooks import notice

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import transcript as _transcript
lazy from agent_toolkit._common.session_state import update_state
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context, resolve_hook_agent_id
lazy from agent_toolkit._hooks.bugfix_gate import QUOTED_MATERIAL_RE
lazy from agent_toolkit._hooks.user_response_advisor import latest_human_turn

_SUBJECT = r"(?:既存|現行|既に?ある|今ある)の?(?:規範|基準|規定|条文|対策|手順|仕組み|ルール)"
# 既存の規範・基準を理由に、追加の対処を見送る言い回し。
EXCUSE_RE = re.compile(
    _SUBJECT + r"(?:で|が|により|によって|だけで)?(?:十分|足り|足る|被覆|カバー|対応済み|防げ)(?!(?:で(?:は)?)?な[いかく])"
    r"|規範(?:は|を|の追加は)(?:足さない|追加しない|不要)"
)
_RETRY_STATE_KEY = "norm_excuse_retry_by_actor"
# 途中の発話は書き直せないため、検出を一度だけ知らせた本文の指紋を残し、以後のStopでは止めない。
_FLAGGED_STATE_KEY = "norm_excuse_flagged_intermediate"
_RETRY_LIMIT = 16

_block_notice = notice.block_formatter("norm_excuse_gate")


def find_excuse(text: str) -> str | None:
    """引用を除いた本文から、既存の規範を理由に対処を見送る言い回しを返す。"""
    match = EXCUSE_RE.search(QUOTED_MATERIAL_RE.sub("", text))
    return match.group(0) if match else None


def _consume_retry(payload: dict, text: str) -> bool:
    """同じ主体の同じ本文の2回目なら真を返して記録を消し、初回は記録して偽を返す。"""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return False
    actor = resolve_hook_agent_id(payload)
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    repeated = False

    def update(state: dict) -> dict:
        nonlocal repeated
        prior = state.get(_RETRY_STATE_KEY, {})
        prior = dict(prior) if isinstance(prior, dict) else {}
        values = [value for value in prior.get(actor, []) if isinstance(value, str)]
        repeated = fingerprint in values
        if repeated:
            values.remove(fingerprint)
        else:
            values.append(fingerprint)
        prior[actor] = values[-_RETRY_LIMIT:]
        state[_RETRY_STATE_KEY] = prior
        return state

    return repeated if update_state(session_id, update) else True


def _block_body(phrase: str, delivery: str) -> str:
    return _block_notice(
        f"本文が既存の規範を理由に対処を見送る言い回し「{phrase}」を含むため、{delivery}を止めた。"
        "規範があっても起きた誤りは、その規範だけでは再発を防げない。",
        fix=(
            "`agent-basics:bugfix`の`references/prevention.md`「案を比べる順」に従い、誘因の除去・構造・検査のどれかを選んで"
            "処置し、本文を書き直せ。言い回しが引用や見送り以外の意味なら、同じ主体から同じ本文を再発行してよい。"
        ),
    )


def text_block(payload: dict, text: object, delivery: str) -> str | None:
    """ユーザーへ届く本文の判定。遮断する場合は通知本文を返す。"""
    if not isinstance(text, str) or (phrase := find_excuse(text)) is None:
        return None
    if _consume_retry(payload, text):
        return None
    return _block_body(phrase, delivery)


def _intermediate_texts(payload: dict) -> list[str]:
    """現在のターンでツール呼出しの合間に出した発話の本文を返す。最後の応答は別に判定する。"""
    path = payload.get("transcript_path")
    lines = _transcript.read_transcript_lines(path) if isinstance(path, str) and path else None
    if not lines:
        return []
    turn = latest_human_turn(lines)
    if turn is None:
        return []
    _prompt, entries = turn
    texts: list[str] = []
    for entry in entries:
        message = entry.get("message")
        content = message.get("content") if entry.get("type") == "assistant" and isinstance(message, dict) else None
        if isinstance(content, list):
            # 画面に「summary」として出る要約済みの思考（`thinking`）も、ユーザーが読む本文として判定する。
            texts.extend(
                text
                for block in content
                if isinstance(block, dict)
                and isinstance(text := block.get("text" if block.get("type") == "text" else "thinking"), str)
                and block.get("type") in {"text", "thinking"}
            )
    last = payload.get("last_assistant_message")
    return [text for text in texts if text != last]


def _first_flag(session_id: str, text: str) -> bool:
    """途中の発話の検出を初めて知らせる場合だけ真を返し、指紋を記録する。"""
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
    """send_to_userの遮断を通常の発話で迂回した場合も、ターンの終了で一度だけ止める。"""
    for text in _intermediate_texts(payload):
        if (phrase := find_excuse(text)) is not None and _first_flag(session_id, text):
            return _block_body(phrase, "ターンの終了（途中の発話）")
    return None


def evaluate(payload_text: str) -> tuple[str, str]:
    """応答本文で終えるターンを同じ判定で止める。途中の発話も同じ言い回しを判定する。"""
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    if payload.get("stop_hook_active") is True or not is_main_agent_context(payload):
        return "approve", ""
    body = text_block(payload, payload.get("last_assistant_message"), "ターンの終了") or intermediate_block(session_id, payload)
    if body is None:
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_norm_excuse", {})
    return "block", body


def main(payload_text: str) -> int:
    """Stop判定の結果を標準出力へ返す。"""
    decision, body = evaluate(payload_text)
    print(json.dumps({"decision": decision, "reason": body} if decision == "block" else {}, ensure_ascii=False))
    return 0
