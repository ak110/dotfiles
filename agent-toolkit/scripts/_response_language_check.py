"""Claude Code agent-toolkit: メインエージェント応答の言語比率検査。

直前のアシスタントターン（非サブエージェント）のテキストブロックを集約し、
コードブロック・インラインコード・URLを除いた地の文の語数比を判定する。
語数比は日本語文字数を、日本語文字数と英単語数（連続英字列）の和で割った値とする。
比率が閾値未満なら警告メッセージを返し、PreToolUseのadditionalContext経由で
コーディングエージェントへ通知する。
"""

import enum
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
    "01-agent.md「言語表現」章に従い、進捗報告・判断・ステータス更新を"
    "ツール呼び出し前後の短文ステータスも含めて日本語で記述すること。"
)

BLOCK_BODY = "英語主体の応答が2ターン連続で検出された。ユーザーは英語の発話を読まないため、日本語での応答に切り替えること。"


def check(transcript_path: str) -> str | None:
    """直前のメインエージェント応答の語数比を判定する（後方互換ラッパー）。

    WARNのときのみ警告本文を返し、PASS・SKIPではNoneを返す。
    判定ロジックの詳細は`detailed_check()`を参照する。
    """
    outcome, body, _ = detailed_check(transcript_path)
    if outcome is CheckOutcome.WARN:
        return body
    return None


def detailed_check(transcript_path: str) -> tuple[CheckOutcome, str | None, str]:
    """直前のメインエージェント応答の語数比を判定し、3値で結果を返す。

    判定対象テキストはアシスタントターン内の`type == "text"`ブロックのみで、
    フェンス付きコードブロック・インラインコード・URLを除外する。
    語数比は日本語文字数 ÷（日本語文字数 ＋ 英単語数）で求める。
    閾値はモジュール定数を参照する。

    Returns:
        (判定結果, 警告本文またはNone, message ID)のタプル。
        SKIPまたはPASSの場合、警告本文はNoneを返す。
        message IDはtranscriptから取得できなかった場合は空文字列を返す。
    """
    if not transcript_path:
        return (CheckOutcome.SKIP, None, "")
    plain_text, msg_id = _collect_plain_text(transcript_path)
    if len(plain_text) < _MIN_PLAIN_TEXT_LENGTH:
        return (CheckOutcome.SKIP, None, msg_id)
    japanese_count = len(_JAPANESE_CHAR_PATTERN.findall(plain_text))
    english_word_count = len(_ENGLISH_WORD_PATTERN.findall(plain_text))
    denominator = japanese_count + english_word_count
    if denominator == 0:
        return (CheckOutcome.SKIP, None, msg_id)
    ratio = japanese_count / denominator
    if ratio >= _MIN_JAPANESE_WORD_RATIO:
        return (CheckOutcome.PASS, None, msg_id)
    return (CheckOutcome.WARN, WARNING_BODY, msg_id)


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
