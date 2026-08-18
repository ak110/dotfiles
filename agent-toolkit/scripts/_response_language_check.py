"""Claude Code agent-toolkit: メインエージェント応答の言語検査。

直前のアシスタントターン（非サブエージェント）のテキストブロックを集約し、
コードブロック・インラインコード・URLを除いた地の文を判定する。
判定条件は、日本語文字を含まない英語だけの地の文、地の文の先頭に置かれた英語の談話標識、
語数比が閾値未満であることの3種とする。
語数比は日本語文字数を、日本語文字数と英単語数（連続英字列）の和で割った値とする。
いずれかの条件へ該当すると警告メッセージを返し、PreToolUseのadditionalContext経由で
コーディングエージェントへ通知する。
"""

import enum
import re

from _transcript import iter_latest_assistant_messages

# プレーンテキストがこの文字数に満たない場合は語数比の判定をスキップする。
# 「OK」「了解」程度の短文応答で英語化検出を行わないようにするための下限。
# 日本語文字数0かつ英単語2語以上の地の文へは適用しない。当該入力は日本語が1文字も無く、
# 下限が防ごうとしている誤発火（日本語の短文が英語判定される事態）が原理的に生じないため。
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

# 日本語文字。CJK記号（U+3000-U+303F）・ひらがな（U+3040-U+309F）・カタカナ（U+30A0-U+30FF）・
# CJK統合漢字（U+4E00-U+9FFF）・全角英数記号（U+FF00-U+FF60）・半角カナ（U+FF61-U+FF9F）を対象とする。
# 非ASCII全域を対象にすると、ハングル・キリルのみで構成された応答も日本語と判定する。
# 半角ハングル（U+FFA0-U+FFDC）を含めないため、全角英数記号の上限をU+FF60、半角カナの上限をU+FF9Fとする。
_JAPANESE_CHAR_PATTERN = re.compile("[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\uff00-\uff60\uff61-\uff9f]")

# 英単語（連続するASCII英字列）。連続1列を1語として数える。
_ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+")

# 日本語文字を含まない地の文を英語応答と判定するための英単語数の下限。
# 1語だけの地の文は識別子・コマンド名の単独提示と区別できないため対象外とする。
_MIN_ENGLISH_WORD_COUNT = 2

# 地の文の先頭に置かれたら英語応答と判定する談話標識。
# 閉集合とする理由は、英単語一般を対象とする開いた集合にすると、
# 正当な日本語応答の冒頭へ置かれた製品名・識別子で誤発火するため。
# 直後が`.`と英字の並び（`Next.js`等の製品名形式）である場合と、
# 直後に語境界（空白・改行・読点・句点・コロン）が無い場合は標識と見なさない。
# インラインコードは地の文の収集時に空白へ置換済みのため、バッククォート内の語は先頭判定へ現れない。
_DISCOURSE_MARKERS = ("finally", "first", "let's", "okay", "next", "then", "also", "let", "now", "ok", "so")
_DISCOURSE_MARKER_PATTERN = re.compile(
    r"^(?:" + "|".join(_DISCOURSE_MARKERS) + r")(?![.．][A-Za-z])(?=[\s、，。．.:：])",
    re.IGNORECASE,
)


class CheckOutcome(enum.Enum):
    """言語検査の判定結果。"""

    WARN = "warn"
    PASS = "pass"
    SKIP = "skip"


# hookメッセージ英語規定（agent-toolkit/skills/agent-standards/references/claude-hooks.md）の例外。
# 英語化を矯正する指示を英語で伝えると英語傾向を助長するため日本語にする。
WARNING_BODY = (
    "直前のアシスタント応答が英語主体で記述されている。"
    "ユーザーは英語の発話を読まないため、日本語で言い直すこと。"
    "01-agent.md「日本語」節に従い、進捗報告・判断・ステータス更新を"
    "ツール呼び出し前後の短文ステータスも含めて日本語で記述すること。"
)

BLOCK_BODY = "英語主体の応答が2ターン連続で検出された。ユーザーは英語の発話を読まないため、日本語での応答に切り替えること。"


def detailed_check(transcript_path: str) -> tuple[CheckOutcome, str | None, str]:
    """直前のメインエージェント応答の記述言語を判定し、3値で結果を返す。

    判定対象テキストはアシスタントターン内の`type == "text"`ブロックのみで、
    フェンス付きコードブロック・インラインコード・URLを除外する。
    長さ下限を適用しない2条件（英語だけの地の文、先頭の談話標識）を先に判定し、
    どちらにも該当しない場合だけ長さ下限付きの語数比判定へ進む。

    Returns:
        (判定結果, 警告本文またはNone, message ID)のタプル。
        SKIPまたはPASSの場合、警告本文はNoneを返す。
        message IDはtranscriptから取得できなかった場合は空文字列を返す。
    """
    if not transcript_path:
        return (CheckOutcome.SKIP, None, "")
    plain_text, msg_id = _collect_plain_text(transcript_path)
    japanese_count = len(_JAPANESE_CHAR_PATTERN.findall(plain_text))
    english_word_count = len(_ENGLISH_WORD_PATTERN.findall(plain_text))
    if _is_english_only(japanese_count, english_word_count):
        return (CheckOutcome.WARN, WARNING_BODY, msg_id)
    if _starts_with_discourse_marker(plain_text):
        return (CheckOutcome.WARN, WARNING_BODY, msg_id)
    if len(plain_text) < _MIN_PLAIN_TEXT_LENGTH or japanese_count + english_word_count == 0:
        return (CheckOutcome.SKIP, None, msg_id)
    if _is_below_word_ratio(japanese_count, english_word_count):
        return (CheckOutcome.WARN, WARNING_BODY, msg_id)
    return (CheckOutcome.PASS, None, msg_id)


def _is_english_only(japanese_count: int, english_word_count: int) -> bool:
    """日本語文字を含まず英単語が下限以上の地の文かを返す。"""
    return japanese_count == 0 and english_word_count >= _MIN_ENGLISH_WORD_COUNT


def _starts_with_discourse_marker(plain_text: str) -> bool:
    """地の文の先頭が英語の談話標識かを返す。"""
    return _DISCOURSE_MARKER_PATTERN.match(plain_text.lstrip()) is not None


def _is_below_word_ratio(japanese_count: int, english_word_count: int) -> bool:
    """語数比が閾値未満かを返す。

    語数比は日本語文字数 ÷（日本語文字数 ＋ 英単語数）で求める。
    """
    return japanese_count / (japanese_count + english_word_count) < _MIN_JAPANESE_WORD_RATIO


def _collect_plain_text(transcript_path: str) -> tuple[str, str]:
    """直前アシスタントターンのテキストブロックを連結し、コード・URLをマスクした地の文とmessage IDを返す。

    テキストが空の場合は("", "")を返す。
    """
    texts: list[str] = []
    msg_id = ""
    for message in iter_latest_assistant_messages(transcript_path):
        raw_id = message.get("id", "")
        if not msg_id:
            msg_id = raw_id if isinstance(raw_id, str) else ""
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
        return ("", "")
    joined = "\n".join(texts)
    masked = _FENCED_CODE_PATTERN.sub(" ", joined)
    masked = _INLINE_CODE_PATTERN.sub(" ", masked)
    masked = _URL_PATTERN.sub(" ", masked)
    return (masked, msg_id)
