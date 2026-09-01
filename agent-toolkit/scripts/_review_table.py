"""レビュー指摘管理表の7列TSVを排他更新する補助CLI。"""

from __future__ import annotations

import argparse
import json
import re
import typing
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path

import _atk_help
import _file_lock
from _atomic_file import atomic_write

COLUMNS = (
    "round",
    "track",
    "location",
    "issue",
    "response-needed",
    "response",
    "no-response-reason",
)
_COLUMN_COUNT = len(COLUMNS)
_KEY_COLUMN_COUNT = 4
TRACK_VALUES = ("plan-review", "implementation-review", "plan-conformance", "independent")
_RECOVERY_GUIDANCE = (
    f"期待列数は{_COLUMN_COUNT}、trackの位置はroundの直後、"
    f"trackの正規値集合は{', '.join(TRACK_VALUES)}。"
    "旧8列形式はseverity列を除いて7列形式で再作成する"
)
_YES_VALUES = frozenset({"yes", "true", "1", "required", "対応要"})
_NO_VALUES = frozenset({"no", "false", "0", "not-required", "対応不要"})
_WHITESPACE_RE = re.compile(r"\s+")
_ROUND_RE = re.compile(r"^[1-9][0-9]*$")


def _path(raw_path: str) -> Path:
    """表ファイルパスを絶対パスへ解決する。"""
    return Path(raw_path).expanduser().resolve()


def _cell(value: str) -> str:
    """セル文字列をJSON文字列へ符号化する。"""
    return json.dumps(value, ensure_ascii=False)


def _decode_cell(value: str, *, line: int, column: int) -> str:
    """JSON文字列セルを復号し、形式不正をエラーにする。"""
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"{line}行{column}列がJSON文字列ではない") from error
    if not isinstance(decoded, str):
        raise ValueError(f"{line}行{column}列が文字列ではない")
    return decoded


def _read(path: Path) -> list[list[str]]:
    """TSVを読み、JSON復号済みの行一覧を返す。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"レビュー表を解釈できない: {path}: {error}") from error
    rows: list[list[str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        cells = line.split("\t")
        if len(cells) != _COLUMN_COUNT:
            raise ValueError(f"{line_number}行の列数が{_COLUMN_COUNT}ではない: {len(cells)}。{_RECOVERY_GUIDANCE}")
        rows.append([_decode_cell(cell, line=line_number, column=index) for index, cell in enumerate(cells, start=1)])
    return rows


def _normalized(value: str) -> str:
    """複合キー用にUnicode、前後空白及び連続空白を正規化する。"""
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", value).strip())


def _key(row: list[str]) -> tuple[str, str, str, str]:
    return (
        _normalized(row[0]),
        _normalized(row[1]),
        _normalized(row[2]),
        _normalized(row[3]),
    )


def _validate_rows(rows: list[list[str]], *, require_responses: bool = False) -> None:
    """7列、先頭4列の複合キー一意性及び応答分岐を検証する。"""
    keys: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if len(row) != _COLUMN_COUNT:
            raise ValueError(f"{index}行の列数が{_COLUMN_COUNT}ではない")
        if any(not _normalized(value) for value in row[:_KEY_COLUMN_COUNT]):
            raise ValueError(f"{index}行の先頭4列は空にできない")
        if _ROUND_RE.match(_normalized(row[0])) is None:
            raise ValueError(f"{index}行のラウンドが1以上の整数ではない")
        if row[1] not in TRACK_VALUES:
            raise ValueError(f"{index}行のtrackが正規値ではない。{_RECOVERY_GUIDANCE}")
        key = _key(row)
        if key in keys:
            raise ValueError(f"{index}行の先頭4列が重複している")
        keys.add(key)
        response_needed = _normalized(row[4]).casefold()
        response = row[5].strip()
        reason = row[6].strip()
        if not response_needed:
            if require_responses:
                raise ValueError(f"{index}行の対応要否が未回答である")
            if response or reason:
                raise ValueError(f"{index}行は対応要否なしで応答欄を埋められない")
            continue
        if response_needed in _YES_VALUES:
            if not response or reason:
                raise ValueError(f"{index}行の対応要は対応内容だけを必要とする")
        elif response_needed in _NO_VALUES:
            if response or not reason:
                raise ValueError(f"{index}行の対応不要は対応不要理由だけを必要とする")
        else:
            raise ValueError(f"{index}行の対応要否がyes/noではない")


def validate(path: str | Path, *, require_responses: bool = True) -> int:
    """表全体を検証し、件数を標準出力へ表示する。"""
    target = _path(str(path))
    rows = _read(target)
    _validate_rows(rows, require_responses=require_responses)
    label = "検証成功" if require_responses else "構造検証成功"
    print(f"{label}: {target} ({len(rows)}件)")
    return 0


def _write_atomic(path: Path, rows: list[list[str]]) -> None:
    """行を一時ファイルへ書き、同一ディレクトリ内で原子的に置換する。"""
    content = "".join("\t".join(_cell(value) for value in row) + "\n" for row in rows)
    atomic_write(path, content, fsync=True)


def _locked_update(path: Path, updater: Callable[[list[list[str]]], list[list[str]]]) -> list[list[str]]:
    """ロック内で再読込・検証・更新・原子的置換を実行する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    _file_lock.ensure_plan_lock_ignored(lock_path)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        try:
            rows = _read(path) if path.exists() else []
            _validate_rows(rows)
            updated = updater(rows)
            _validate_rows(updated)
            _write_atomic(path, updated)
            return updated
        finally:
            _file_lock.release_lock(lock_file)


def init(path: str | Path) -> int:
    """存在しない表を作成する。"""
    target = _path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    _file_lock.ensure_plan_lock_ignored(lock_path)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        try:
            if target.exists():
                raise ValueError(f"レビュー表が既に存在する: {target}")
            _write_atomic(target, [])
        finally:
            _file_lock.release_lock(lock_file)
    print(target)
    return 0


def add(path: str | Path, round_value: str, track: str, location: str, issue: str) -> int:
    """レビュー担当の指摘行を追加する。"""
    target = _path(str(path))
    row = [round_value, track, location, issue, "", "", ""]

    def updater(rows: list[list[str]]) -> list[list[str]]:
        if _key(row) in {_key(existing) for existing in rows}:
            raise ValueError("先頭4列の複合キーが重複している")
        return [*rows, row]

    rows = _locked_update(target, updater)
    print(f"追加成功: {target} ({len(rows)}件)")
    return 0


def _response_value(raw: str) -> str:
    """CLIの対応要否を保存用のyes/noへ正規化する。"""
    normalized = _normalized(raw).casefold()
    if normalized in _YES_VALUES:
        return "yes"
    if normalized in _NO_VALUES:
        return "no"
    raise ValueError("対応要否はyesまたはnoを指定する")


def _format_key_diagnostic(rows: list[list[str]], given: list[tuple[int, str]], matches: list[int]) -> str:
    """一意に解決できない部分キーと復号済み候補行を整形する。"""
    requested = ", ".join(f"{COLUMNS[index]}={value}" for index, value in given) or "なし"
    candidate_rows = [rows[index] for index in matches] if matches else rows
    candidate_lines = [
        "  - "
        + ", ".join(f"{column}={_normalized(value)}" for column, value in zip(COLUMNS[:_KEY_COLUMN_COUNT], row, strict=False))
        for row in candidate_rows
    ]
    candidates = "\n".join(candidate_lines) or "  - 候補行なし"
    return (
        f"指定された部分キー: {requested}\n"
        f"候補行（復号済み）:\n{candidates}\n"
        "レビュー表のセルはJSON文字列として保存されるため、キーには復号後の値を指定する。"
    )


def respond(
    path: str | Path,
    round_value: str,
    track: str,
    location: str,
    issue: str,
    response_needed: str,
    response: str,
    no_response_reason: str,
) -> int:
    """レビューイーの応答欄だけを部分キーで更新する。

    `round`・`track`・`location`・`issue`のうち非空で与えられた列だけを比較対象とし、
    該当行を特定する。該当行が1件でない場合は複合キー解決不能として拒否する。
    対応要否と矛盾する欄（`response-needed=yes`に対する`no-response-reason`、
    `response-needed=no`に対する`response`）の同時指定は`ValueError`で拒否する。
    """
    target = _path(str(path))
    needed = _response_value(response_needed)
    response = response.strip()
    reason = no_response_reason.strip()
    if needed == "yes" and reason:
        raise ValueError("対応要否がyesの場合はno-response-reasonを指定できない")
    if needed == "no" and response:
        raise ValueError("対応要否がnoの場合はresponseを指定できない")
    replacement = response if needed == "yes" else ""
    reason = reason if needed == "no" else ""
    given = [
        (index, _normalized(value)) for index, value in enumerate((round_value, track, location, issue)) if _normalized(value)
    ]

    def updater(rows: list[list[str]]) -> list[list[str]]:
        matches = [
            row_index
            for row_index, row in enumerate(rows)
            if all(_normalized(row[column_index]) == value for column_index, value in given)
        ]
        if len(matches) != 1:
            diagnostic = _format_key_diagnostic(rows, given, matches)
            raise ValueError(f"応答対象の複合キーが一意に解決できない: {len(matches)}件\n{diagnostic}")
        updated = [*rows]
        updated[matches[0]] = [*updated[matches[0]][:_KEY_COLUMN_COUNT], needed, replacement, reason]
        return updated

    _locked_update(target, updater)
    print(f"応答更新成功: {target}")
    return 0


def show(path: str | Path, track: str | None = None) -> int:
    """表のraw TSVを保存順のまま表示し、指定時はtrackで限定する。"""
    target = _path(str(path))
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"レビュー表を解釈できない: {target}: {error}") from error
    if track is None:
        print(text, end="")
        return 0
    if track not in TRACK_VALUES:
        raise ValueError(f"trackが正規値ではない。{_RECOVERY_GUIDANCE}")
    selected: list[str] = []
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        content = line.rstrip("\r\n")
        if not content:
            continue
        cells = content.split("\t")
        if len(cells) != _COLUMN_COUNT:
            raise ValueError(f"{line_number}行の列数が{_COLUMN_COUNT}ではない: {len(cells)}。{_RECOVERY_GUIDANCE}")
        if _decode_cell(cells[1], line=line_number, column=2) == track:
            selected.append(line)
    print("".join(selected), end="")
    return 0


class _GuidedSubcommandParser(argparse.ArgumentParser):
    """解釈できない引数を、当該サブコマンドの受理形式を示して拒否するサブパーサー。

    `argparse`の既定では、サブコマンドが解釈できない引数はトップレベルの
    `unrecognized arguments`として報告され、当該サブコマンドのusageも受理形式も表示されない。
    受理しない名前を個別に登録する方式では、未登録の名前をトップレベルのエラーとして報告するため、
    残余引数を一律に捕捉して受理形式を返す。
    """

    @typing.override
    def parse_known_args(  # type: ignore[override]  # pyright: ignore[reportIncompatibleMethodOverride]  # ty: ignore[invalid-method-override]
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> tuple[argparse.Namespace, list[str]]:
        parsed, remaining = super().parse_known_args(args, namespace)
        assert parsed is not None
        if remaining:
            self.error(f"解釈できない引数: {remaining[0]}。{self._accepted_form()}")
        return parsed, remaining

    def _accepted_form(self) -> str:
        """当該サブコマンドが受理するオプションと表のパスの指定方法を説明する。"""
        options = sorted(option for option in self._option_string_actions if option.startswith("--") and option != "--help")
        accepted = "・".join(options) if options else "なし"
        return f"{self.prog}が受理するオプションは{accepted}で、表のパスは位置引数で指定する"


def _add_cell_options(parser: argparse.ArgumentParser, option: str, description: str) -> None:
    """セル本文を受け取るオプションと、同じ本文をファイルから読むオプションを対で登録する。

    `atk mq add --body-file`と同じ利用形とし、引用符・改行・バッククォートを含む本文を
    シェルの引用規則を経由せずに渡せるようにする。
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f"--{option}", help=description)
    group.add_argument(
        f"--{option}-file",
        metavar="PATH",
        help=f"{description}を記載したファイルのパス。引用符・改行・バッククォートを含む本文をシェルのエスケープを介さず渡す場合に使う。",
    )


def _read_cell_file(option: str, raw_path: str) -> str:
    """セル本文を記載したファイルをUTF-8で読む。"""
    path = Path(raw_path).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"{option}の読み込みに失敗した: {raw_path}（{error}）") from error
    except UnicodeDecodeError as error:
        raise ValueError(f"{option}をUTF-8として解釈できない: {raw_path}") from error


def _cell_value(args: argparse.Namespace, dest: str) -> str:
    """`--<名前>`と`--<名前>-file`のうち指定された方からセル本文を返す。"""
    raw_path = getattr(args, f"{dest}_file", None)
    if raw_path is not None:
        return _read_cell_file(f"--{dest.replace('_', '-')}-file", raw_path)
    return getattr(args, dest, None) or ""


def _cell_value_with_positional(args: argparse.Namespace, dest: str, positional: str) -> str:
    """ファイル指定を優先し、未指定の場合だけ位置引数へフォールバックする。"""
    if getattr(args, f"{dest}_file", None) is not None:
        return _cell_value(args, dest)
    return _cell_value(args, dest) or getattr(args, positional) or ""


def _required_value(args: argparse.Namespace, option: str, positional: str) -> str:
    value = _cell_value_with_positional(args, option, positional)
    if not isinstance(value, str) or not value:
        raise ValueError(f"--{option}を指定する")
    return value


def build_parser(parent: argparse._SubParsersAction) -> None:
    """`review-table`配下のサブコマンドを登録する。"""
    review = _atk_help.add_command(parent, "review-table", **_atk_help.HELP["atk review-table"])
    sub = _atk_help.add_subcommands(
        review,
        dest="review_table_subcommand",
        required=False,
        show_help_when_missing=True,
        parser_class=_GuidedSubcommandParser,
    )
    init_parser = _atk_help.add_command(sub, "init", **_atk_help.HELP["atk review-table init"])
    path_help = "操作するレビュー指摘管理表のパス。計画ファイルと同じstemの`.plan-review.tsv`か`.exec-review.tsv`を指定する。"
    init_parser.add_argument("path", help=path_help)
    add_command_parser = _atk_help.add_command(sub, "add", **_atk_help.HELP["atk review-table add"])
    add_command_parser.add_argument("path", help=path_help)
    add_command_parser.add_argument(
        "--round",
        required=True,
        help="指摘を登録するレビューのラウンド番号。1以上の整数で指定する。",
    )
    add_command_parser.add_argument("--track", required=True, choices=TRACK_VALUES, help=_RECOVERY_GUIDANCE)
    for name, positional, description in (("location", "location_arg", "指摘箇所"), ("issue", "issue_arg", "指摘内容")):
        help_text = (
            "指摘箇所。`--location`か`--location-file`でも指定できる。"
            if name == "location"
            else "指摘内容。`--issue`か`--issue-file`でも指定できる。"
        )
        add_command_parser.add_argument(positional, nargs="?", help=help_text)
        _add_cell_options(add_command_parser, name, description)
    respond_parser = _atk_help.add_command(sub, "respond", **_atk_help.HELP["atk review-table respond"])
    respond_parser.add_argument("path", help=path_help)
    respond_parser.add_argument(
        "--round",
        help="更新する行を特定するラウンド番号。省略すると他の列だけで行を特定する。",
    )
    respond_parser.add_argument(
        "--track",
        choices=TRACK_VALUES,
        help="更新する行を特定するレビューの区分。省略すると他の列だけで行を特定する。",
    )
    for name, positional, description in (("location", "location_arg", "指摘箇所"), ("issue", "issue_arg", "指摘内容")):
        help_text = (
            "更新する行を特定する指摘箇所。`--location`か`--location-file`でも指定できる。"
            if name == "location"
            else "更新する行を特定する指摘内容。`--issue`か`--issue-file`でも指定できる。"
        )
        respond_parser.add_argument(positional, nargs="?", help=help_text)
        _add_cell_options(respond_parser, name, description)
    respond_parser.add_argument(
        "--response-needed",
        required=True,
        choices=("yes", "no", "対応要", "対応不要"),
        help="指摘への対応要否。yes又は対応要、no又は対応不要を指定する。",
    )
    _add_cell_options(respond_parser, "response", "対応内容")
    _add_cell_options(respond_parser, "no-response-reason", "対応不要理由")
    show_parser = _atk_help.add_command(sub, "show", **_atk_help.HELP["atk review-table show"])
    show_parser.add_argument("path", help=path_help)
    show_parser.add_argument("--track", choices=TRACK_VALUES, help=_RECOVERY_GUIDANCE)
    validate_parser = _atk_help.add_command(sub, "validate", **_atk_help.HELP["atk review-table validate"])
    validate_parser.add_argument(
        "--allow-unanswered",
        action="store_true",
        help=f"未応答行を許容し、{_COLUMN_COUNT}列と複合キーなどの構造だけを検証する。{_RECOVERY_GUIDANCE}",
    )
    validate_parser.add_argument("path", help=path_help)


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果をレビュー表操作へ振り分ける。"""
    command = args.review_table_subcommand
    if command == "init":
        return init(args.path)
    if command == "show":
        return show(args.path, args.track)
    if command == "validate":
        return validate(args.path, require_responses=not args.allow_unanswered)
    if command == "add":
        location = _required_value(args, "location", "location_arg")
        issue = _required_value(args, "issue", "issue_arg")
        return add(args.path, args.round, args.track, location, issue)
    if command == "respond":
        round_value = args.round or ""
        track = args.track or ""
        location = _cell_value_with_positional(args, "location", "location_arg")
        issue = _cell_value_with_positional(args, "issue", "issue_arg")
        if not any((round_value, track, location, issue)):
            raise ValueError("round・track・location・issueのいずれかを指定する")
        return respond(
            args.path,
            round_value,
            track,
            location,
            issue,
            args.response_needed,
            _cell_value(args, "response"),
            _cell_value(args, "no_response_reason"),
        )
    raise ValueError(f"未知のreview-tableサブコマンド: {command}")
