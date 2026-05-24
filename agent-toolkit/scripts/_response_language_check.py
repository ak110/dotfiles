"""Claude Code agent-toolkit: メインエージェント応答の言語比率検査。

直前のアシスタントターン（非サブエージェント）のテキストブロックを集約し、
コードブロック・インラインコード・URLを除いた地の文の語数比を判定する。
語数比は日本語文字数を、日本語文字数と英単語数（連続英字列）の和で割った値とする。
比率が閾値未満なら警告メッセージを返し、PreToolUseのadditionalContext経由で
コーディングエージェントへ通知する。
"""

import re

from _transcript import iter_latest_assistant_messages

# プレーンテキストがこの文字数に満たない場合は検査をスキップする。
# 「OK」「了解」程度の短文応答で英語化検出を行わないようにするための下限。
_MIN_PLAIN_TEXT_LENGTH = 50

# 語数比の閾値。地の文の日本語文字数を、日本語文字数と英単語数の和で割った値が
# この比率未満なら警告する。連続する英字列を1英単語として数えることで、
# 裸の英識別子・コマンド名・ファイルパスが分母を文字数分押し上げる水増しを抑え、
# 日本語主体の応答に英語専門用語が多数混在する場合の誤発火を防ぐ。
_MIN_JAPANESE_WORD_RATIO = 0.3

# フェンス付きコードブロック（言語指定の有無を問わない、複数行対応）。
_FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?```")

# インラインコード（バッククォート間、改行を含まない）。
_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")

# HTTP/HTTPS URL。
_URL_PATTERN = re.compile(r"https?://\S+")

# 日本語文字（`\x00-\x7f`の範囲外の文字すべて。全角記号などを含む）。
_JAPANESE_CHAR_PATTERN = re.compile(r"[^\x00-\x7f]")

# 英単語（連続するASCII英字列）。連続1列を1語として数える。
_ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+")


def check(transcript_path: str) -> str | None:
    """直前のメインエージェント応答の語数比を判定する。

    判定対象テキストはアシスタントターン内の`type == "text"`ブロックのみで、
    フェンス付きコードブロック・インラインコード・URLを除外する。
    語数比は日本語文字数 ÷（日本語文字数 ＋ 英単語数）で求める。
    閾値以上、または対象外条件（短文・日本語と英単語がともにゼロ・transcript読み取り失敗）でNoneを返し、
    閾値未満で警告メッセージ本文（英語）を返す。閾値はモジュール定数を参照する。
    """
    if not transcript_path:
        return None
    plain_text = _collect_plain_text(transcript_path)
    if len(plain_text) < _MIN_PLAIN_TEXT_LENGTH:
        return None
    japanese_count = len(_JAPANESE_CHAR_PATTERN.findall(plain_text))
    english_word_count = len(_ENGLISH_WORD_PATTERN.findall(plain_text))
    denominator = japanese_count + english_word_count
    if denominator == 0:
        return None
    ratio = japanese_count / denominator
    if ratio >= _MIN_JAPANESE_WORD_RATIO:
        return None
    return (
        "your previous assistant turn appears to be written largely in English."
        " Per agent.md 「言語表現」 chapter, conduct progress updates / decisions / status reports in Japanese,"
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
