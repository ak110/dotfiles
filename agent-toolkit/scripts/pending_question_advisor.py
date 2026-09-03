"""地の文の問いかけでターンを終える応答を検出するStopフック。

直前のアシスタント応答の地の文へ、利用者へ判断を求める文が含まれ、同じ応答が
`AskUserQuestion`の呼び出しを持たない場合にターンの終了を遮断する。
判断を求める場面で`AskUserQuestion`を使う規定は規範が定めるが、自身の応答が
当該場面に当たるかの分類は誤りやすいため、機械的な検出を置く。
判定は疑問符で終わる文と、判断を促す定型表現を含む文の2条件とする。
疑問符は直後に語が続かない場合だけ文末として扱うため、`〜ですか？と尋ねられた`のように
語句の内側にある疑問符は遮断の対象にならない。
コードブロック、インラインコード、URL及び行頭が`>`の引用行は地の文から除くため、
記録の引用と実行例の中の疑問符は遮断の対象にならない。
判断を求めていない応答が遮断された場合は、当該問いかけを本文から除いて応答を
書き直すことで通過する。
"""

import json
import re
from collections.abc import Iterator

import _transcript
from _hook_notice import block_formatter as _block_notice_formatter
from _stop_gate import append_stop_log
from _stop_gate import parse_stop_session as _parse_stop_session

_HOOK_ID = "agent-toolkit/pending_question_advisor"

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

# 利用者へ判断を促す定型表現。疑問符を伴わない依頼形の問いかけを検出する。
_REQUEST_EXPRESSIONS = (
    "お知らせください",
    "ご指示ください",
    "ご連絡ください",
    "ご判断ください",
    "教えてください",
    "選んでください",
)

_ASK_USER_QUESTION_TOOL = "AskUserQuestion"

# hookメッセージ英語規定（agent-toolkit/skills/agent-standards/references/claude-hooks.md）の例外。
# 遮断の対象が日本語で書かれた地の文であり、対象と同じ言語で示す方が該当箇所を特定しやすい。
BLOCK_BODY = (
    "地の文で利用者へ判断を求めたままターンを終えようとしている。"
    "判断を求める場合はAskUserQuestionで確認し、"
    "確認が不要な場合は当該問いかけを本文から除いて応答を書き直すこと。"
)

_BLOCK_FIX = "AskUserQuestionで確認するか、当該問いかけを本文から除いて応答を書き直す。"


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


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
    """地の文が利用者へ判断を求める文を含むかを返す。"""
    for raw in _sentences(plain_text):
        sentence = raw.strip()
        if not sentence:
            continue
        if sentence.endswith(("？", "?")):
            return True
        if any(expression in sentence for expression in _REQUEST_EXPRESSIONS):
            return True
    return False


def _latest_response(transcript_path: str) -> tuple[str, bool]:
    """直前のアシスタント応答の本文と、`AskUserQuestion`の呼び出しの有無を返す。"""
    texts: list[str] = []
    used_ask_user_question = False
    for message in _transcript.iter_latest_assistant_messages(transcript_path):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    texts.append(text)
            elif block_type == "tool_use" and block.get("name") == _ASK_USER_QUESTION_TOOL:
                used_ask_user_question = True
    return ("\n".join(texts), used_ask_user_question)


def main(payload_text: str) -> int:
    """地の文の問いかけでターンを終えようとした応答を遮断する。"""
    resolved = _parse_stop_session(payload_text, _approve)
    if resolved is None:
        append_stop_log("", "approve_invalid_payload", {})
        return 0
    session_id, payload = resolved

    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "approve_stop_hook_active", {"stop_hook_active": True})
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    text, used_ask_user_question = _latest_response(transcript_path)
    if used_ask_user_question:
        append_stop_log(session_id, "approve_ask_user_question_used", {})
        _approve()
        return 0
    if not _asks_user(_plain_text(text)):
        append_stop_log(session_id, "approve_no_pending_question", {})
        _approve()
        return 0

    reason = _block_notice(BLOCK_BODY, fix=_BLOCK_FIX)
    append_stop_log(session_id, "block_pending_question", {})
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0
