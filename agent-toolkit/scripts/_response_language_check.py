"""Claude Code agent-toolkit: 文字列の記述言語検査。

文字列からコードブロック・インラインコード・URL・機械可読な返却行を除いた地の文を判定する。
直前のメインエージェント応答はtranscriptから文字列を取得して同じ判定へ渡す。
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

# 機械可読な返却行。小文字のsnake_case識別子だけの行と、当該識別子をキーとする`<キー>: <値>`行を対象とする。
# `agent-toolkit/rules/02-agent-operations.md`「委譲時の厳守事項」は、委譲先がチェックポイント又は
# 完了報告でターンを終える場合に指定形式の文面だけを出力し地の文を加えないことを求める。
# 当該形式（`status: checkpoint`などのcheckpointブロック、`merged_head:`などの統合結果、
# `needs_escalation`の単独返却）は英字だけで構成されるため、地の文へ残すと英語応答と判定され、
# 規定どおりの返却が遮断される。
# キーを小文字のsnake_caseへ限定するのは、`Summary:`のように大文字で始まる英語の散文を
# 除外対象にせず、人間向け本文が英語で返ることの検出を維持するためである。
_MACHINE_READABLE_LINE_PATTERN = re.compile(r"^[ \t]*[a-z][a-z0-9_]*(?::[^\n]*)?[ \t]*$", re.MULTILINE)

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
    "直前のアシスタント応答の地の文が英語主体と判定された。"
    "地の文が日本語主体でも、冒頭が`Now`・`Next`・`Then`などの英語の語で始まる応答は同じ判定になる。"
    "ユーザーは英語の発話を読まないため、次の応答は冒頭の1文から日本語で書くこと。"
    "01-agent.mdに従い、進捗報告・判断・ステータス更新を"
    "ツール呼び出し前後の短文ステータスも含めて日本語で記述すること。"
    "日本語で応答し直せば、以後のターンはこの検査によって遮断されない。"
)

BLOCK_BODY = "英語主体の応答が2ターン連続で検出された。ユーザーは英語の発話を読まないため、日本語での応答に切り替えること。"


def check_text(text: str) -> tuple[CheckOutcome, str | None]:
    """文字列の記述言語を判定し、3値で結果を返す。

    判定対象テキストは、フェンス付きコードブロック・インラインコード・URL・機械可読な返却行を
    除外した地の文である。
    長さ下限を適用しない2条件（英語だけの地の文、先頭の談話標識）を先に判定し、
    どちらにも該当しない場合だけ長さ下限付きの語数比判定へ進む。

    Returns:
        (判定結果, 警告本文またはNone)のタプル。
        SKIPまたはPASSの場合、警告本文はNoneを返す。
    """
    plain_text = _FENCED_CODE_PATTERN.sub(" ", text)
    plain_text = _INLINE_CODE_PATTERN.sub(" ", plain_text)
    plain_text = _URL_PATTERN.sub(" ", plain_text)
    plain_text = _MACHINE_READABLE_LINE_PATTERN.sub(" ", plain_text)
    japanese_count = len(_JAPANESE_CHAR_PATTERN.findall(plain_text))
    english_word_count = len(_ENGLISH_WORD_PATTERN.findall(plain_text))
    if _is_english_only(japanese_count, english_word_count):
        return (CheckOutcome.WARN, WARNING_BODY)
    if _starts_with_discourse_marker(plain_text):
        return (CheckOutcome.WARN, WARNING_BODY)
    if len(plain_text) < _MIN_PLAIN_TEXT_LENGTH or japanese_count + english_word_count == 0:
        return (CheckOutcome.SKIP, None)
    if _is_below_word_ratio(japanese_count, english_word_count):
        return (CheckOutcome.WARN, WARNING_BODY)
    return (CheckOutcome.PASS, None)


def detailed_check(transcript_path: str) -> tuple[CheckOutcome, str | None, str]:
    """直前のメインエージェント応答の記述言語を判定し、3値で結果を返す。

    判定対象テキストはアシスタントターン内の`type == "text"`ブロックのみで、
    フェンス付きコードブロック・インラインコード・URL・機械可読な返却行を除外する。
    長さ下限を適用しない2条件（英語だけの地の文、先頭の談話標識）を先に判定し、
    どちらにも該当しない場合だけ長さ下限付きの語数比判定へ進む。

    Returns:
        (判定結果, 警告本文またはNone, message ID)のタプル。
        SKIPまたはPASSの場合、警告本文はNoneを返す。
        message IDはtranscriptから取得できなかった場合は空文字列を返す。
    """
    if not transcript_path:
        return (CheckOutcome.SKIP, None, "")
    raw_text, msg_id = _collect_raw_text(transcript_path)
    outcome, body = check_text(raw_text)
    return (outcome, body, msg_id)


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


def _collect_raw_text(transcript_path: str) -> tuple[str, str]:
    """直前アシスタントターンのテキストブロックを連結した文字列とmessage IDを返す。

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
    return ("\n".join(texts), msg_id)
