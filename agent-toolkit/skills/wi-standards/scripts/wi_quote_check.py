"""AWI本文の逐語引用が出所の原文と一致するかを確かめ、要求単位ごとの引用位置を出力する。

`## ユーザー指摘の逐語引用`にある言語指定`text`のフェンスを出現順に`text[1]`、`text[2]`と番号付けし、
各フェンスの内容が出所のいずれかの原文の部分文字列であるかを判定する。
フェンスが確認回答の記録（行頭`回答: `か`自由記述: `の行を持つ書式）の場合は、ユーザーの値である回答・自由記述と
記録より前の地の文だけを原文と比べ、エージェントが書いた質問と選択肢は比べない。
全フェンスが一致した場合は、要求単位ごとの`逐語引用 text[N] 文字A-B`を出力する。
引用節のtextフェンスの外にある本文の参照も、現在のフェンスの番号と要求単位の開始・終了位置に一致するか確かめる。
参照がずれている場合は位置の一覧を出力し、該当行と書き換え方法を標準エラーへ示す。
A-Bはフェンスの内容の先頭を1とし、改行も1文字として数えるUnicodeコードポイントの範囲で、単位の最初の文字から
最後の文字までを含む。要求単位への分け方は`exec-review-evidence-check`と共有する`requirement_units`に従う。

textフェンスの番号付け、文字範囲の数え方、確認回答の記録から回答と自由記述を取り出す規則は本スクリプトだけが持ち、
起草規範はこのスクリプトの実行を案内する。終了コードは一致が0、フェンスの不一致か本文の参照のずれが1、
引数の誤りと読めない入力が2である。

`--import-spec PATH`は保存済みuser-eventsから引用節へ取り込む。PATHは選択オブジェクトのJSON配列で、
各要素にsource（保存済みJSONLの絶対パス）、record、lineを指定する。同位置に複数イベントがある場合はsequenceも指定する。
modeはfull、range（1始まり・両端を含むstart/end）、command-args、answerから選び、省略時はfullを使う。
answerはassistant_contextの質問と全選択肢、user_responseの回答と自由記述を区別して生成する。
全選択を検証してから引用節を更新し、一致と引用位置を返す。入力不成立と不一致では原稿を変更しない。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys
from typing import Literal

import pydantic

from agent_toolkit._common import atomic_file, requirement_units
from agent_toolkit._common import next_action as _next_action

QUOTE_HEADING = "## ユーザー指摘の逐語引用"
ANSWER_LABELS = ("質問: ", "選択肢: ", "回答: ", "自由記述: ")
# 質問と選択肢はエージェントが書いた文であり、ユーザーの値は回答と自由記述だけである。
USER_ANSWER_LABELS = ("回答: ", "自由記述: ")
QUOTE_POSITION = re.compile(r"逐語引用\s*text\[(\d+)\]\s*文字(\d+)-(\d+)")


class InputError(Exception):
    """原文との比較を始められない入力の誤り。終了コード2で報告する。"""


@dataclasses.dataclass(frozen=True)
class Original:
    """フェンスと比べる原文と、その所在。`--user-events`の出力では`record`と`line`を持つ。"""

    text: str
    path: pathlib.Path
    record: str | None = None
    line: int | None = None
    sequence: int | None = None
    fence: int | None = None

    def location(self) -> str:
        """出力へ書く所在を返す。"""
        if self.record is None:
            return f"出所={self.path}"
        return f"出所={self.path} " + self.record_location()

    def record_location(self) -> str:
        """保存する引用へセッション記録上の位置を返す。"""
        return f"record={self.record} line={self.line}" + (f" sequence={self.sequence}" if self.sequence is not None else "")


@dataclasses.dataclass(frozen=True)
class Part:
    """フェンスのうち原文と比べる部分。`offset`はフェンスの内容の先頭からの0始まりの位置である。"""

    label: str
    text: str
    offset: int


class Selection(pydantic.BaseModel):
    """保存済みuserイベントから引用する対象と範囲。文字位置は1始まりの両端を含む。"""

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)

    source: str
    record: str
    line: pydantic.PositiveInt
    sequence: pydantic.PositiveInt | None = None
    mode: Literal["full", "range", "command-args", "answer"] = "full"
    start: pydantic.PositiveInt | None = None
    end: pydantic.PositiveInt | None = None


class _Option(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(strict=True)
    label: str
    description: str = ""


class _Question(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(strict=True)
    question: str
    options: list[_Option]


class _Response(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(strict=True)
    answers: list[str]
    notes: str = ""


class _UserEvent(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(strict=True)
    text: str
    record: str
    line: pydantic.PositiveInt
    sequence: pydantic.PositiveInt | None = None
    assistant_context: list[_Question] | None = None
    user_response: list[_Response] | None = None


def _answer_text(event: _UserEvent) -> str:
    questions, responses = event.assistant_context, event.user_response
    if not questions or not responses or len(questions) != len(responses):
        raise InputError("answerには対応するassistant_contextとuser_responseが必要です")
    values = [value for response in responses for value in [*response.answers, *([response.notes] if response.notes else [])]]
    if "\n".join(values) != event.text:
        raise InputError("user_responseの回答・自由記述がtextと一致しません。保存済みuser-eventsを取得し直してください")
    lines: list[str] = []
    for question, response in zip(questions, responses, strict=True):
        lines.append(f"質問: {question.question}")
        lines.extend(
            f"選択肢: {option.label}" + (f": {option.description}" if option.description else "") for option in question.options
        )
        if response.answers:
            lines.append("回答: " + "\n".join(response.answers))
        if response.notes:
            lines.append("自由記述: " + response.notes)
    return "\n".join(lines)


def _selected_text(selection: Selection, event: _UserEvent) -> str:
    if selection.mode == "range":
        if selection.start is None or selection.end is None or not 1 <= selection.start <= selection.end <= len(event.text):
            raise InputError("rangeにはtext内の1始まり・両端を含むstartとendを指定してください")
        content = event.text[selection.start - 1 : selection.end]
    else:
        if selection.start is not None or selection.end is not None:
            raise InputError("startとendはmode=rangeだけで指定できます")
        if selection.mode == "command-args":
            arguments = re.findall(r"<command-args>(.*?)</command-args>", event.text, flags=re.DOTALL)
            if len(arguments) != 1:
                raise InputError("command-argsにはcommand-args要素が1件あるイベントを指定してください")
            content = arguments[0]
        elif selection.mode == "answer":
            content = _answer_text(event)
        else:
            if event.user_response is not None or event.assistant_context is not None:
                raise InputError("確認回答の全体にはmode=answerを指定し、質問と全選択肢も取り込んでください")
            content = event.text
    if not content.strip():
        raise InputError("選択範囲が空です。ユーザーの値を含む範囲を指定してください")
    return content


def _import_quotes(body: str, spec_path: pathlib.Path) -> tuple[str, list[Original]]:
    """全選択を検証し、引用節だけを置換した原稿と比較用原文を返す。"""
    selections = pydantic.TypeAdapter(list[Selection]).validate_json(spec_path.read_bytes())
    if not selections:
        raise InputError("--import-specのJSON配列へ1件以上の選択を指定してください")
    sources: dict[pathlib.Path, list[_UserEvent]] = {}
    blocks: list[str] = []
    originals: list[Original] = []
    for selection in selections:
        path = pathlib.Path(selection.source)
        if not path.is_absolute():
            raise InputError(f"選択のsourceには絶対パスを指定してください: {path}")
        if path not in sources:
            events = [json.loads(raw) for raw in path.read_text(encoding="utf-8-sig").splitlines() if raw.strip()]
            if not all(isinstance(event, dict) for event in events):
                raise InputError(f"保存済みuser-eventsのJSONLオブジェクトを指定してください: {path}")
            sources[path] = [_UserEvent.model_validate(event) for event in events if event.get("kind") == "user"]
        matches = [
            event
            for event in sources[path]
            if event.record == selection.record
            and event.line == selection.line
            and (selection.sequence is None or event.sequence == selection.sequence)
        ]
        if len(matches) != 1:
            raise InputError(
                f"source={path} record={selection.record} line={selection.line}のイベントが{len(matches)}件です。"
                "保存済み出力のrecord・lineを確かめ、複数件ならsequenceも指定してください"
            )
        event = matches[0]
        content = _selected_text(selection, event)
        original = Original(event.text, path, event.record, event.line, event.sequence, len(originals) + 1)
        originals.append(original)
        # 原文に含まれるフェンスが引用を途中で閉じない長さにする。
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", content)), default=0))
        location = original.record_location()
        blocks.append(f"{location}\n\n{fence}text\n{content}\n{fence}")
    start, end = _quote_section(body)
    return body[:start] + "\n" + "\n\n".join(blocks) + "\n\n" + body[end:], originals


def _quote_section(body: str) -> tuple[int, int]:
    """引用節の見出し直後と次のH2の文字位置を返す。フェンス内の見出しは除く。"""
    lines = body.splitlines(keepends=True)
    inside = {index for first, last, _ in requirement_units.fenced_blocks(lines) for index in range(first, last + 1)}
    starts = [index for index, line in enumerate(lines) if index not in inside and line.rstrip() == QUOTE_HEADING]
    if len(starts) != 1:
        raise InputError(f"本文に『{QUOTE_HEADING.removeprefix('## ')}』の見出しが{len(starts)}件あります（1件が必要です）")
    start = starts[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if index not in inside and lines[index].startswith("## ")), len(lines)
    )
    return sum(map(len, lines[:start])), sum(map(len, lines[:end]))


def _quote_fences(body: str) -> list[str]:
    """逐語引用の節にある`text`フェンスの内容を出現順に返す。"""
    start, end = _quote_section(body)
    section = body[start:end].splitlines(keepends=True)
    fences = [
        "".join(section[first + 1 : last]).removesuffix("\r\n" if section[first].endswith("\r\n") else "\n")
        for first, last, info in requirement_units.fenced_blocks(section)
        if info == "text"
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
        Original(event["text"], path, str(event.get("record")), event.get("line"), event.get("sequence"))
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


def _reference_errors(body: str, spans: list[list[tuple[int, int, str]]]) -> list[str]:
    """引用節のtextフェンス外にある参照を、算出済みの要求単位の境界と比べる。"""
    start, end = _quote_section(body)
    first_line = len(body[:start].splitlines())
    section = body[start:end].splitlines(keepends=True)
    quoted = {
        first_line + index
        for first, last, info in requirement_units.fenced_blocks(section)
        if info == "text"
        for index in range(first + 1, last)
    }
    errors: list[str] = []
    for index, line in enumerate(body.splitlines()):
        if index in quoted:
            continue
        for match in QUOTE_POSITION.finditer(line):
            number, first, last = map(int, match.groups())
            units = spans[number - 1] if 1 <= number <= len(spans) else []
            if first <= last and any(first == unit[0] for unit in units) and any(last == unit[1] for unit in units):
                continue
            errors.append(f"本文の{index + 1}行目: {match.group()} が現在の引用節の要求単位の範囲と一致しません")
    return errors


def check(body: str, originals: list[Original]) -> tuple[list[str], list[str], str]:
    """一致と位置の出力行、不一致の診断、次の操作を返す。"""
    output: list[str] = []
    errors: list[str] = []
    spans: list[list[tuple[int, int, str]]] = []
    for number, content in enumerate(_quote_fences(body), start=1):
        parts = _parts(content)
        for part in parts:
            matched = [
                original
                for original in originals
                if original.fence in (None, number)
                and (part.text in original.text or part.text.replace("\r\n", "\n") in original.text)
            ]
            if not matched:
                errors.append(f"text[{number}]の{part.label}が出所のどの原文の部分文字列でもありません")
            else:
                output.extend(f"text[{number}] {part.label}: 一致 {original.location()}" for original in matched)
        if errors:
            continue
        units = [span for part in parts for span in _unit_spans(content, part)]
        spans.append(units)
        output.extend(f"逐語引用 text[{number}] 文字{start}-{end}: {unit}" for start, end, unit in units)
    if errors:
        return (
            [],
            errors,
            (
                "出所の原文（`--user-events`の出力では該当行の`text`）を読み直し、本文の該当する`text`フェンスを原文どおりへ"
                "直してから同じコマンドを再実行する。原文が渡した出所に無い場合は、原文を保持する記録を`--source`へ加える"
            ),
        )
    return (
        output,
        _reference_errors(body, spans),
        ("標準出力の位置の一覧から同じ要求単位の値へ本文の参照を書き換え、同じコマンドを再実行する"),
    )


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
        help="原文を保持するファイルの絶対パス。`atk run-script session-review-evidence -- ... --user-events`の出力か、"
        "原文そのものを持つファイル。複数の出所は反復して指定する",
    )
    parser.add_argument(
        "--import-spec",
        type=pathlib.Path,
        metavar="PATH",
        help="引用節を更新する選択JSON配列の絶対パス。各要素はsource（保存済みuser-eventsの絶対パス）、"
        "record、line、任意のsequence、mode（full/range/command-args/answer）を持ち、mode省略時はfullを使う。"
        "rangeは1始まり・両端を含むstart/endも指定する。配列順で取り込み、全件が原文と一致することを確かめて保存する",
    )
    args = parser.parse_args(argv)
    if not args.source and args.import_spec is None:
        parser.error("逐語引用と原文の比較には--source、取り込みには--import-specを指定する")
    if args.source and args.import_spec is not None:
        parser.error("--import-specは選択内のsourceを使うため、--sourceと同時に指定できません")
    paths = [args.body, *(args.source or []), *([args.import_spec] if args.import_spec is not None else [])]
    if any(not path.is_absolute() for path in paths):
        parser.error("WI本文、--source、--import-specには絶対パスを指定する")
    try:
        body = args.body.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        parser.error(f"WI本文を読めません: {args.body}: {error}")
    try:
        if args.import_spec is not None:
            body, originals = _import_quotes(body, args.import_spec)
        else:
            originals = [original for path in args.source for original in _originals(path)]
        output, errors, next_action = check(body, originals)
        if args.import_spec is not None and not errors:
            atomic_file.atomic_write(args.body, body)
    except (InputError, OSError, UnicodeError, json.JSONDecodeError, pydantic.ValidationError) as error:
        _next_action.report(
            f"失敗: {error}",
            next_action="WI本文と出所の絶対パス・内容を確かめ、取り込みでは--import-specのsource・record・line・"
            "sequence・mode・範囲を保存済みuser-eventsに合わせてから同じコマンドを再実行する",
        )
        return 2
    if output:
        print("\n".join(output))
    if errors:
        for error in errors:
            print(f"失敗: {error}", file=sys.stderr)
        print(
            _next_action.next_action_line(next_action),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
