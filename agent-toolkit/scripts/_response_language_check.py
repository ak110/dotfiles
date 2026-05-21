"""Claude Code agent-toolkit: メインエージェント応答の非ASCII文字比率検査。

直前のアシスタントターン（非サブエージェント）のテキストブロックを集約し、
コードブロック・インラインコード・URLを除いた地の文の非ASCII文字比率を判定する。
比率が閾値未満なら警告メッセージを返し、PreToolUseのadditionalContext経由で
コーディングエージェントへ通知する。
"""

import re

from _transcript import iter_latest_assistant_messages

# プレーンテキストがこの文字数に満たない場合は検査をスキップする。
# 「OK」「了解」程度の短文応答で英語化検出を行わないようにするための下限。
_MIN_PLAIN_TEXT_LENGTH = 50

# 非ASCII文字比率の閾値。地の文の文字数のうち非ASCII文字（日本語・全角記号など
# `\x00-\x7f`の範囲外の文字）がこの比率未満なら警告する。
_MIN_NON_ASCII_RATIO = 0.1

# フェンス付きコードブロック（言語指定の有無を問わない、複数行対応）。
_FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?```")

# インラインコード（バッククォート間、改行を含まない）。
_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")

# HTTP/HTTPS URL。
_URL_PATTERN = re.compile(r"https?://\S+")

# 非ASCII文字（`\x00-\x7f`の範囲外の文字すべて）。
_NON_ASCII_CHAR_PATTERN = re.compile(r"[^\x00-\x7f]")


def check(transcript_path: str) -> str | None:
    """直前のメインエージェント応答の非ASCII文字比率を判定する。

    判定対象テキストはアシスタントターン内の`type == "text"`ブロックのみで、
    フェンス付きコードブロック・インラインコード・URLを除外する。
    閾値以上または対象外条件（短文・transcript読み取り失敗）でNoneを返し、
    閾値未満で警告メッセージ本文（英語）を返す。閾値はモジュール定数を参照する。
    """
    if not transcript_path:
        return None
    plain_text = _collect_plain_text(transcript_path)
    if len(plain_text) < _MIN_PLAIN_TEXT_LENGTH:
        return None
    non_ascii_chars = _NON_ASCII_CHAR_PATTERN.findall(plain_text)
    ratio = len(non_ascii_chars) / len(plain_text)
    if ratio >= _MIN_NON_ASCII_RATIO:
        return None
    return (
        "your previous assistant turn appears to be written largely in English."
        " Per agent.md language-style chapter, conduct progress updates / decisions / status reports in Japanese,"
        " including short status sentences around tool calls."
    )


def _collect_plain_text(transcript_path: str) -> str:
    """直前アシスタントターンのテキストブロックを連結し、コード・URLをマスクした地の文を返す。"""
    texts: list[str] = []
    for message in iter_latest_assistant_messages(transcript_path):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            if isinstance(text, str) and text:
                texts.append(text)
    if not texts:
        return ""
    joined = "\n".join(texts)
    masked = _FENCED_CODE_PATTERN.sub(" ", joined)
    masked = _INLINE_CODE_PATTERN.sub(" ", masked)
    masked = _URL_PATTERN.sub(" ", masked)
    return masked
