"""通常本文と送信本文の問いかけでターンを終える応答を検出するStopフック。

直前のアシスタント応答の地の文へ、ユーザーへ判断を求める文が含まれ、同じ応答が
`AskUserQuestion`の呼び出しを持たない場合にターンの終了を遮断する。
判断を求める場面で`AskUserQuestion`を使う規定は規範が定めるが、自身の応答が
その場面に当てはまるかの分類は誤りやすいため、機械的な検出を置く。
判定は疑問符で終わる文と、判断を促す定型表現を含む文の2条件とする。
定型表現より前に条件節（`〜場合は`など）を持つ文は、条件が成立したときの手順の案内であり
判断を求めていないため、定型表現の条件から外す。
疑問符は直後に語が続かない場合だけ文末として扱うため、`〜ですか？と尋ねられた`のように
語句の内側にある疑問符は遮断の対象にならない。
コードブロック、インラインコード、URLおよび行頭が`>`の引用行は地の文から除くため、
記録の引用と実行例の中の疑問符は遮断の対象にならない。
判断を求めていない応答が遮断された場合は、その問いかけを本文から除いて応答を
書き直すことで通過する。

委譲先での実行可否: 委譲先は`AskUserQuestion`を実行できないため、hook入力と環境印で除外する。
"""

import re
from collections.abc import Iterator

from agent_toolkit._common import transcript as _transcript
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter
from agent_toolkit._hooks.stop_session import append_stop_log
from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session
from agent_toolkit._hooks.transcript_scan import read_transcript_entries_cached

_HOOK_ID = "pending_question_advisor"
_block_notice = _block_notice_formatter(_HOOK_ID)

# フェンス付きコードブロック・インラインコード・URL・行頭が`>`の引用行。
# 地の文の抽出範囲を`_response_language_check`とそろえ、引用行を追加で除く。
_FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")
_URL_PATTERN = re.compile(r"https?://\S+")
_QUOTE_LINE_PATTERN = re.compile(r"^[ \t]*>.*$", re.MULTILINE)

# 文の終端。句点・感嘆符・改行と、直後に語が続かない疑問符を終端とする。
# 「〜ですか？と尋ねられた」のように語句の内側にある疑問符は文末ではないため、終端に含めない。
_SENTENCE_END_PATTERN = re.compile(r"[。．！!\n]|[？?](?=\s|$)")

# ユーザーへ判断を促す定型表現。疑問符を伴わない依頼形の問いかけを検出する。
_REQUEST_EXPRESSIONS = (
    "お知らせください",
    "ご指示ください",
    "ご連絡ください",
    "ご判断ください",
    "教えてください",
    "選んでください",
)

# 定型表現より前にあれば、その文を条件付きの手順の案内とみなす条件節の標識。
# 「6.12で起動しなかった場合は、…を選んでください。」は判断要求ではない。
_CONDITION_MARKERS = ("場合は", "場合、", "ときは", "たら、")

_ASK_USER_QUESTION_TOOL = "AskUserQuestion"

BLOCK_BODY = (
    "地の文でユーザーへ判断を求めたままターンを終えようとしている。"
    "判断を求める場合はAskUserQuestionで確認し、"
    "確認が不要な場合はその問いかけを本文から除いて応答を書き直すこと。"
)

_BLOCK_FIX = "AskUserQuestionで確認するか、その問いかけを本文から除いて応答を書き直す。"


def _plain_text(text: str) -> str:
    """コードブロック・インラインコード・URL・引用行を除いた地の文を返す。"""
    plain = _FENCED_CODE_PATTERN.sub(" ", text)
    plain = _INLINE_CODE_PATTERN.sub(" ", plain)
    plain = _URL_PATTERN.sub(" ", plain)
    return _QUOTE_LINE_PATTERN.sub(" ", plain)


def _sentences(plain_text: str) -> Iterator[str]:
    """地の文を、終端記号を含んだ文へ区切って返す。"""
    start = 0
    for match in _SENTENCE_END_PATTERN.finditer(plain_text):
        yield plain_text[start : match.end()]
        start = match.end()
    yield plain_text[start:]


def _asks_user(plain_text: str) -> bool:
    """地の文がユーザーへ判断を求める文を含むかを返す。"""
    for raw in _sentences(plain_text):
        sentence = raw.strip()
        if not sentence:
            continue
        if sentence.endswith(("？", "?")):
            return True
        if any(_is_unconditional_request(sentence, expression) for expression in _REQUEST_EXPRESSIONS):
            return True
    return False


def _is_unconditional_request(sentence: str, expression: str) -> bool:
    """文が定型表現を含み、その表現より前に条件節の標識を持たないかを返す。"""
    position = sentence.find(expression)
    if position < 0:
        return False
    preceding = sentence[:position]
    return not any(marker in preceding for marker in _CONDITION_MARKERS)


def _latest_response(transcript_path: str) -> tuple[str, bool]:
    """最後の本文を返し、人間の応答・作業ツール・APIエラー以前の問いを除く。

    送信のtool resultを本文の境界から除くため、そのIDだけを透過させる。
    空の最終応答でも送信済み本文を保持し、本文を持つ別応答へ進めば判定対象を更新する。
    """
    texts: list[str] = []
    used_ask_user_question = False
    message_id = ""
    send_ids: set[str] = set()
    for entry in read_transcript_entries_cached(transcript_path):
        if entry.get("isSidechain"):
            continue
        message = entry.get("message", {})
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
        attachment = entry.get("attachment")
        queued_input = isinstance(attachment, dict) and attachment.get("type") == "queued_command"
        if entry.get("isApiErrorMessage") or entry.get("type") == "user" or queued_input:
            if (
                not entry.get("isApiErrorMessage")
                and not queued_input
                and blocks
                and all(
                    block.get("type") == "tool_result" and block.get("tool_use_id") in send_ids and not block.get("is_error")
                    for block in blocks
                )
            ):
                continue
            texts.clear()
            send_ids.clear()
            used_ask_user_question = False
            message_id = ""
            continue
        if entry.get("type") != "assistant" or not any(
            block.get("type") == "tool_use" or _transcript.visible_text_blocks([block]) for block in blocks
        ):
            continue
        current_id = message.get("id", "")
        if message_id and current_id and message_id != current_id:
            texts.clear()
            send_ids.clear()
            used_ask_user_question = False
        message_id = current_id
        for block in blocks:
            if block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                if name.endswith(_transcript.SEND_TO_USER_TOOL_SUFFIX):
                    if isinstance(block.get("id"), str):
                        send_ids.add(block["id"])
                else:
                    texts.clear()
                    used_ask_user_question = name == _ASK_USER_QUESTION_TOOL
            texts.extend(_transcript.visible_text_blocks([block]))
    return ("\n".join(texts), used_ask_user_question)


def evaluate(payload_text: str) -> tuple[str, str]:
    """問いかけの判定結果と、遮断する場合の理由を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        append_stop_log("", "approve_invalid_payload", {})
        return "approve", ""
    session_id, payload = resolved

    if not is_main_agent_context(payload):
        append_stop_log(session_id, "approve_delegated_session", {})
        return "approve", ""

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    text, used_ask_user_question = _latest_response(transcript_path)
    if used_ask_user_question:
        append_stop_log(session_id, "approve_ask_user_question_used", {})
        return "approve", ""
    if not _asks_user(_plain_text(text)):
        append_stop_log(session_id, "approve_no_pending_question", {})
        return "approve", ""

    reason = _block_notice(BLOCK_BODY, fix=_BLOCK_FIX)
    append_stop_log(session_id, "block_pending_question", {})
    return "block", reason
