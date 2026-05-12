"""Claude Code agent-toolkit: メインエージェント応答の日本語比率検査。

直前のアシスタントターン（非サブエージェント）のテキストブロックを集約し、
コードブロック・インラインコード・URLを除いた地の文の日本語文字比率を判定する。
比率が閾値未満なら警告メッセージを返し、PreToolUseのadditionalContext経由で
コーディングエージェントへ通知する。
"""

import re

from _transcript import iter_latest_assistant_messages

# プレーンテキストがこの文字数に満たない場合は検査をスキップする。
# 「OK」「了解」程度の短文応答で英語化検出を行わないようにするための下限。
_MIN_PLAIN_TEXT_LENGTH = 50

# 日本語文字比率の閾値。地の文の文字数のうち日本語文字（ひらがな・カタカナ・漢字・
# 半角カナ）がこの比率未満なら警告する。
_MIN_JAPANESE_RATIO = 0.3

# フェンス付きコードブロック（言語指定の有無を問わない、複数行対応）。
_FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?```")

# インラインコード（バッククォート間、改行を含まない）。
_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")

# HTTP/HTTPS URL。
_URL_PATTERN = re.compile(r"https?://\S+")

# 日本語文字（ひらがな U+3040-309F・カタカナ U+30A0-30FF・
# CJK統合漢字 U+4E00-9FFF・CJK拡張A U+3400-4DBF・半角カナ U+FF65-FF9F）。
_JAPANESE_CHAR_PATTERN = re.compile(r"[぀-ゟ゠-ヿ㐀-䶿一-鿿･-ﾟ]")

# 比率の分母に含めるscript系文字（日本語文字 + ASCIIラテン英字）。
# 数字・記号・空白は分母から除外することで、文体としての日本語／英語比率に近づける。
_SCRIPT_CHAR_PATTERN = re.compile(r"[A-Za-z぀-ゟ゠-ヿ㐀-䶿一-鿿･-ﾟ]")


def check(transcript_path: str) -> str | None:
    """直前のメインエージェント応答の日本語文字比率を判定する。

    判定対象テキストはアシスタントターン内の`type == "text"`ブロックのみで、
    フェンス付きコードブロック・インラインコード・URLを除外する。
    閾値以上または対象外条件（短文・script系文字皆無・transcript読み取り失敗）でNoneを返し、
    閾値未満で警告メッセージ本文（英語）を返す。閾値はモジュール定数を参照する。
    """
    if not transcript_path:
        return None
    plain_text = _collect_plain_text(transcript_path)
    if len(plain_text) < _MIN_PLAIN_TEXT_LENGTH:
        return None
    script_chars = _SCRIPT_CHAR_PATTERN.findall(plain_text)
    if not script_chars:
        return None
    japanese_chars = _JAPANESE_CHAR_PATTERN.findall(plain_text)
    ratio = len(japanese_chars) / len(script_chars)
    if ratio >= _MIN_JAPANESE_RATIO:
        return None
    return (
        "your previous assistant turn appears to be written largely in English."
        " Per styles.md, conduct progress updates / decisions / status reports in Japanese,"
        " including short status sentences around tool calls."
        " Do not switch to English just because the surrounding code identifiers or keywords are English."
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
