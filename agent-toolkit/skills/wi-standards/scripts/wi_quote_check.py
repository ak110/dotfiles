"""AWI本文の逐語引用が出所の原文と一致するかを確かめ、要求単位ごとの引用位置を出力する。

`## ユーザー指摘の逐語引用`にある言語指定`text`のフェンスを出現順に`text[1]`、`text[2]`と番号付けし、
各フェンスの内容が出所のいずれかの原文の部分文字列であるかを判定する。
フェンスが確認回答の記録（行頭`回答: `か`自由記述: `の行を持つ書式）の場合は、ユーザーの値である回答・自由記述と
記録より前の地の文だけを原文と比べ、エージェントが書いた質問と選択肢は比べない。
全フェンスが一致した場合は、要求単位ごとの`逐語引用 text[N] 文字A-B`を出力する。
A-Bはフェンスの内容の先頭を1とし、改行も1文字として数えるUnicodeコードポイントの範囲で、単位の最初の文字から
最後の文字までを含む。要求単位への分け方は`exec-review-evidence-check`と共有する`requirement_units`に従う。

textフェンスの番号付け、文字範囲の数え方、確認回答の記録から回答と自由記述を取り出す規則は本スクリプトだけが持ち、
起草規範はこのスクリプトの実行を案内する。終了コードは一致が0、不一致が1、引数の誤りと読めない入力が2である。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import requirement_units

QUOTE_HEADING = "## ユーザー指摘の逐語引用"
ANSWER_LABELS = ("質問: ", "選択肢: ", "回答: ", "自由記述: ")
# 質問と選択肢はエージェントが書いた文であり、ユーザーの値は回答と自由記述だけである。
USER_ANSWER_LABELS = ("回答: ", "自由記述: ")


class InputError(Exception):
    """原文との比較を始められない入力の誤り。終了コード2で報告する。"""


@dataclasses.dataclass(frozen=True)
class Original:
    """フェンスと比べる原文と、その所在。`--user-events`の出力では`record`と`line`を持つ。"""

    text: str
    path: pathlib.Path
    record: str | None = None
    line: int | None = None

    def location(self) -> str:
        """出力へ書く所在を返す。"""
        if self.record is None:
            return f"出所={self.path}"
        return f"出所={self.path} record={self.record} line={self.line}"


@dataclasses.dataclass(frozen=True)
class Part:
    """フェンスのうち原文と比べる部分。`offset`はフェンスの内容の先頭からの0始まりの位置である。"""

    label: str
    text: str
    offset: int


def _quote_fences(body: str) -> list[str]:
    """逐語引用の節にある`text`フェンスの内容を出現順に返す。"""
    lines = body.splitlines()
    starts = [index for index, line in enumerate(lines) if line.rstrip() == QUOTE_HEADING]
    if len(starts) != 1:
        raise InputError(f"本文に『{QUOTE_HEADING.removeprefix('## ')}』の見出しが{len(starts)}件あります（1件が必要です）")
    start = starts[0] + 1
    end = next((index for index in range(start, len(lines)) if lines[index].startswith("## ")), len(lines))
    section = lines[start:end]
    fences = [
        "\n".join(section[first + 1 : last]) for first, last, info in requirement_units.fenced_blocks(section) if info == "text"
    ]
    if not fences:
        raise InputError("逐語引用の節に言語指定`text`のフェンスがありません")
    return fences


def _parts(content: str) -> list[Part]:
    """フェンスの内容から原文と比べる部分を返す。確認回答の記録では地の文と回答・自由記述の値だけを返す。"""
    lines = content.split("\n")
    if not any(line.startswith(USER_ANSWER_LABELS) for line in lines):
        return [Part("本文", content, 0)]
    parts: list[Part] = []
    label = "地の文"
    value: list[str] = []
    offset = position = 0
    for line in lines:
        current = next((candidate for candidate in ANSWER_LABELS if line.startswith(candidate)), None)
        if current is not None:
            parts.append(Part(label, "\n".join(value), offset))
            label, value, offset = current.removesuffix(": "), [line.removeprefix(current)], position + len(current)
        else:
            value.append(line)
        position += len(line) + 1
    parts.append(Part(label, "\n".join(value), offset))
    user_labels = {"地の文", *(candidate.removesuffix(": ") for candidate in USER_ANSWER_LABELS)}
    return [
        Part(part.label, part.text.rstrip("\n"), part.offset)
        for part in parts
        if part.label in user_labels and part.text.strip()
    ]


def _originals(path: pathlib.Path) -> list[Original]:
    """出所のファイルを原文へ分ける。`--user-events`の出力は`kind`が`user`の各行の`text`、それ以外はファイル全体とする。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InputError(f"出所を読めません: {path}: {error}") from error
    events: list[object] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            return [Original(text, path)]
    if not events or not all(isinstance(event, dict) for event in events):
        return [Original(text, path)]
    originals = [
        Original(event["text"], path, str(event.get("record")), event.get("line"))
        for event in events
        if isinstance(event, dict) and event.get("kind") == "user" and isinstance(event.get("text"), str)
    ]
    return originals or [Original(text, path)]


def _unit_spans(content: str, part: Part) -> list[tuple[int, int, str]]:
    """部分の要求単位ごとに、フェンスの内容での1始まりの文字範囲と単位の本文を返す。

    要求単位は段落の行を空白で連結してから文へ分けるため、複数行にまたがる単位は内容の連続した部分文字列にならない。
    空白を除いた文字列で単位を前から順に探し、元の位置へ戻して最初と最後の文字の位置を求める。
    HTMLコメントは分割の前に除かれるため、同じ長さの空白へ置き換えてから探す。
    """
    masked = requirement_units.HTML_COMMENT.sub(lambda match: " " * len(match.group()), content)
    positions = [index for index, char in enumerate(masked) if not char.isspace()]
    compact = "".join(masked[index] for index in positions)
    cursor = next((number for number, index in enumerate(positions) if index >= part.offset), len(positions))
    spans: list[tuple[int, int, str]] = []
    for unit in requirement_units.requirement_units(part.text.split("\n")):
        compact_unit = "".join(unit.split())
        found = compact.find(compact_unit, cursor)
        if not compact_unit or found < 0:
            raise InputError(f"要求単位の位置を本文から特定できません: {unit}")
        end = found + len(compact_unit) - 1
        spans.append((positions[found] + 1, positions[end] + 1, unit))
        cursor = end + 1
    return spans


def check(body: str, originals: list[Original]) -> tuple[list[str], list[str]]:
    """一致した場合の出力行と、不一致の診断を返す。"""
    output: list[str] = []
    errors: list[str] = []
    for number, content in enumerate(_quote_fences(body), start=1):
        parts = _parts(content)
        for part in parts:
            matched = next((original for original in originals if part.text in original.text), None)
            if matched is None:
                errors.append(f"text[{number}]の{part.label}が出所のどの原文の部分文字列でもありません")
            else:
                output.append(f"text[{number}] {part.label}: 一致 {matched.location()}")
        if errors:
            continue
        for part in parts:
            output.extend(
                f"逐語引用 text[{number}] 文字{start}-{end}: {unit}" for start, end, unit in _unit_spans(content, part)
            )
    return output, errors


def main(argv: list[str] | None = None) -> int:
    """WI本文と出所を受け取り、逐語引用の一致と引用位置を出力する。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "body", type=pathlib.Path, metavar="PATH", help="`## ユーザー指摘の逐語引用`を持つWI本文ファイルの絶対パス"
    )
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        metavar="PATH",
        action="append",
        default=None,
        required=True,
        help="原文を保持するファイルの絶対パス。`atk run-script session-review-evidence -- ... --user-events`の出力か、"
        "原文そのものを持つファイル。複数の出所は反復して指定する",
    )
    args = parser.parse_args(argv)
    if any(not path.is_absolute() for path in [args.body, *args.source]):
        parser.error("WI本文と--sourceには絶対パスを指定する")
    try:
        body = args.body.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        parser.error(f"WI本文を読めません: {args.body}: {error}")
    try:
        originals = [original for path in args.source for original in _originals(path)]
        output, errors = check(body, originals)
    except InputError as error:
        _next_action.report(
            f"失敗: {error}",
            next_action="WI本文と--sourceの絶対パスと内容を確かめ、同じコマンドを再実行する",
        )
        return 2
    if errors:
        for error in errors:
            print(f"失敗: {error}", file=sys.stderr)
        print(
            _next_action.next_action_line(
                "出所の原文（`--user-events`の出力では該当行の`text`）を読み直し、本文の該当する`text`フェンスを原文どおりへ"
                "直してから同じコマンドを再実行する。原文が渡した出所に無い場合は、原文を保持する記録を`--source`へ加える"
            ),
            file=sys.stderr,
        )
        return 1
    print("\n".join(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
