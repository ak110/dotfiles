"""既存の規範を理由に再発防止を見送る句を、操作を止めずに警告する。  pyfltr: allow-comment

委譲先での実行可否: ユーザーが直接読む入力だけを判定し、委譲先の最終応答は対象外とする。
"""

import json
import re
from collections.abc import Iterable

from agent_toolkit._common.transcript import latest_assistant_text_messages, visible_assistant_text
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.bugfix_gate import QUOTED_MATERIAL_RE
from agent_toolkit._hooks.notice import resolve_notice, warning_formatter
from agent_toolkit._hooks.stop_session import parse_stop_session

_SOURCE = "norm_excuse_gate"
_CAUSE = "norm_excuse"
_warning = warning_formatter(_SOURCE)
_SUBJECT = r"(?:既存|現行|既に?ある|今ある)の?(?:規範|基準|規定|条文|対策|手順|仕組み|ルール)"
_NOT_NEGATED = r"(?!(?:で(?:は)?|て(?:い)?|して(?:い)?|でき)?な[いかく]|ず|ません|ていません|ではありません)"

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
    rf"|今後[ \t]*気を[ \t]*[付つ]けます(?![、, \t]*(?:とは|だけでは|では|という|と言))"
)
_KAGI_QUOTE_RE = re.compile(r"「[^「」\n]*」|『[^『』\n]*』")


def find_excuse(text: str) -> str | None:
    unquoted = _KAGI_QUOTE_RE.sub(" ", QUOTED_MATERIAL_RE.sub(" ", text))
    match = EXCUSE_RE.search(unquoted) or _FREEFORM_EXCUSE_RE.search(unquoted)
    return match.group(0) if match else None


def warning_for_texts(texts: Iterable[str], session_id: str = "", *, event_id: object = None) -> str:
    candidates = tuple(texts)
    phrase = next((found for text in candidates if (found := find_excuse(text)) is not None), None)
    if phrase is None:
        if session_id:
            resolve_notice(session_id, _SOURCE, _CAUSE)
        return ""
    return _warning(
        f"再発防止を見送る報告に当たりうる句「{phrase}」があります。"
        "既に規範があったことは、今回の条件を防げる根拠になりません。",
        fix=(
            "次の操作: `agent-basics:bugfix/references/prevention.md`「再発防止を採用しない場合」に従い、"
            "既存対策が効かなかった理由を区分し、文言・出し方の修正と仕組みの変更を比べてください。"
            "引用漏れなど実際の見送りでなければ、本文を変える必要はありません。"
        ),
        cause=_CAUSE,
        session_id=session_id,
        removable_cause=False,
        fingerprint_input=json.dumps((event_id if isinstance(event_id, str) else None, candidates), ensure_ascii=False),
    )


def _stop_event_id(payload: dict, text: str, entries: list[dict]) -> str | None:
    turn_id = payload.get("turn_id")
    if isinstance(turn_id, str) and turn_id:
        return turn_id
    for message in latest_assistant_text_messages(entries):
        identifier = message.get("id")
        if isinstance(identifier, str) and identifier and visible_assistant_text(message).strip() == text.strip():
            return identifier
    return None


def evaluate(payload_text: str, *, entries: list[dict] | None = None) -> tuple[str, str]:
    if (resolved := parse_stop_session(payload_text, lambda: None)) is not None:
        session_id, payload = resolved
        text = payload.get("last_assistant_message")
        if (
            is_main_agent_context(payload)
            and isinstance(text, str)
            and text.strip()
            and (warning := warning_for_texts((text,), session_id, event_id=_stop_event_id(payload, text, entries or [])))
        ):
            return "notify_user", warning
    return "approve", ""
