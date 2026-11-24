"""Claude Code agent-toolkit: 文字列の記述言語を判定する。

文字列からコードブロック・インラインコード・URL・機械可読な返却行を除いた地の文を判定する。
判定条件は、日本語文字を含まない英語だけの地の文、地の文の先頭に置かれた英語の談話標識、
語数比が閾値未満であることと、説明中の他文字体系の語を対象とする。
語数比は日本語文字数を、日本語文字数と英単語数（連続英字列）の和で割った値とする。
いずれかの条件へ該当するとWARNと本文を返す。呼出側は同じ判定結果から、表示・後続操作・終了の前に日本語への書き直しを求める。
"""

import argparse
import enum
import json
import pathlib
import re
import sys
import unicodedata

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


_MIN_PLAIN_TEXT_LENGTH = 50


_MIN_JAPANESE_WORD_RATIO = 0.3

# フェンス付きコードブロック（言語指定の有無を問わない、複数行対応）。
_FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?```")


_EXPLANATION_FIELDS = frozenset({"group", "excess", "retain", "basis"})
_QUOTED_MATERIAL_PATTERN = re.compile(r"「[^」]*」|『[^』]*』|^[ \t]*>[^\n]*", re.MULTILINE)


_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")


_URL_PATTERN = re.compile(r"https?://\S+")


_MACHINE_IDENTIFIER_PATTERN = re.compile(
    r"(?:"
    r"(?<![\w.])(?:[A-Za-z]:[\\/]|\.{0,2}/|/)[^\s`、。]+"
    r"|\b[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\b"
    r"|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
    r"|\b(?=[0-9a-fA-F]{7,64}\b)(?=[0-9a-fA-F]*[0-9])[0-9a-fA-F]+\b"
    r"|(?<![A-Za-z0-9_])-{0,2}[A-Za-z0-9]+(?:[_.-][A-Za-z0-9]+)+(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])[A-Z0-9]+(?![A-Za-z0-9_])"
    r")"
)

# 機械可読な返却行。小文字のsnake_case識別子だけの行と、その識別子をキーとする`<キー>: <値>`行を対象とする。
# 委譲先の入口（`atk-mode`の`playbooks/subagent.md`「返却と終了」）は、委譲先がチェックポイントまたは
# 完了報告でターンを終える場合に指定形式の文面だけを出力し地の文を加えないことを求める。
# これらの形式のうち英字だけで構成される行（`status: checkpoint`などのcheckpointブロック、
# 英字の欄名で返す旧形式の返却、`condition_not_met`のような結果値の単独行）は、地の文へ残すと英語応答と判定され、
# 規定どおりの返却が遮断される。日本語の欄名（`統合後のHEAD:`など）の行は日本語の地の文として数える。
# キーを小文字のsnake_caseへ限定するのは、`Summary:`のように大文字で始まる英語の散文を
# 除外対象にせず、人間向け本文が英語で返ることの検出を維持するためである。
_MACHINE_READABLE_LINE_PATTERN = re.compile(r"^[ \t]*[a-z][a-z0-9_]*(?::[^\n]*)?[ \t]*$", re.MULTILINE)


_JAPANESE_CHAR_PATTERN = re.compile("[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\uff00-\uff60\uff61-\uff9f]")


_ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+")


_MIN_ENGLISH_WORD_COUNT = 2


_DISCOURSE_MARKERS = ("finally", "first", "let's", "okay", "next", "then", "also", "let", "now", "ok", "so")
_DISCOURSE_MARKER_PATTERN = re.compile(
    r"^(?:" + "|".join(_DISCOURSE_MARKERS) + r")(?![.．][A-Za-z])(?=[\s、，。．.:：])",
    re.IGNORECASE,
)


class CheckOutcome(enum.Enum):
    WARN = "warn"
    PASS = "pass"
    SKIP = "skip"


WARNING_BODY = (
    "直前のアシスタント応答の地の文が英語主体と判定された。"
    "地の文が日本語主体でも、冒頭が`Now`・`Next`・`Then`などの英語の語で始まる応答は同じ判定になる。"
    "ユーザーは英語の発話を読まない。"
)

BLOCK_BODY = "英語主体の本文を日本語で書き直すまで、呼び出しまたは終了を止めた。"

WARNING_FIX = (
    "直前の文または入力本文を、内容を保って日本語で書き直してください。"
    "識別子・コマンド・正式名・逐語引用は原表記を保ち、訂正後に同じ呼び出しを再発行してください。"
)
"""表示と後続の操作の取り違えを防ぐ、モデル自身による書き直し。"""


def explanation_is_japanese(text: str) -> bool:
    material = re.sub(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\x27[^\x27\n]*\x27(?!\w)', " ", text)
    return check_text(material)[0] is not CheckOutcome.WARN


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
    plain_text = _plain_text(text)
    if _has_foreign_word(plain_text):
        return (
            CheckOutcome.WARN,
            "直前のアシスタント応答の説明に日本語以外の文字列が混入している。引用・素材を保ち、説明を日本語で書け。",
        )
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


def _has_foreign_word(text: str) -> bool:
    run = 0
    for char in text:
        name = unicodedata.name(char, "")
        foreign = char.isalpha() and not (
            _JAPANESE_CHAR_PATTERN.fullmatch(char) or name.startswith(("LATIN", "CJK", "HIRAGANA", "KATAKANA"))
        )
        run = run + 1 if foreign else 0
        if run >= 2:
            return True
    return False


def _plain_text(text: str) -> str:

    def explanations(value: object) -> list[str]:
        if isinstance(value, list):
            return [part for item in value for part in explanations(item)]
        if isinstance(value, dict):
            return [item for key, item in value.items() if key in _EXPLANATION_FIELDS and isinstance(item, str)]
        return []

    def structured_prose(raw: str) -> str:
        try:
            return "\n".join(explanations(json.loads(raw)))
        except ValueError, TypeError:
            return " "

    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            plain_text = "\n".join(explanations(json.loads(stripped)))
        except ValueError:
            plain_text = text
    else:
        plain_text = text
    plain_text = _FENCED_CODE_PATTERN.sub(
        lambda match: (
            structured_prose(match.group().removeprefix("```json").removesuffix("```"))
            if match.group().startswith("```json\n")
            else " "
        ),
        plain_text,
    )
    plain_text = _QUOTED_MATERIAL_PATTERN.sub(" ", plain_text)
    plain_text = _INLINE_CODE_PATTERN.sub(" ", plain_text)
    plain_text = _URL_PATTERN.sub(" ", plain_text)
    plain_text = _MACHINE_READABLE_LINE_PATTERN.sub(" ", plain_text)
    plain_text = _MACHINE_IDENTIFIER_PATTERN.sub(" ", plain_text)
    return plain_text


def _is_english_only(japanese_count: int, english_word_count: int) -> bool:
    return japanese_count == 0 and english_word_count >= _MIN_ENGLISH_WORD_COUNT


def _starts_with_discourse_marker(plain_text: str) -> bool:
    return _DISCOURSE_MARKER_PATTERN.match(plain_text.lstrip()) is not None


def _is_below_word_ratio(japanese_count: int, english_word_count: int) -> bool:
    return japanese_count / (japanese_count + english_word_count) < _MIN_JAPANESE_WORD_RATIO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="記述言語を共通基準で判定する。")
    parser.add_argument("--text", required=True, help="判定する本文")
    args = parser.parse_args(argv)
    result, reason = check_text(args.text)
    print(json.dumps({"outcome": result.value, "body": reason, "fix": WARNING_FIX}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
