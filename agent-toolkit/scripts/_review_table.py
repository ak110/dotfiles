"""レビュー指摘管理表の7列TSVを排他更新する補助CLI。"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

from _atomic_file import atomic_write
from _file_lock import acquire_lock, release_lock

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
TRACK_VALUES = ("plan-review", "plan-conformance", "independent")
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
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        acquire_lock(lock_file)
        try:
            rows = _read(path) if path.exists() else []
            _validate_rows(rows)
            updated = updater(rows)
            _validate_rows(updated)
            _write_atomic(path, updated)
            return updated
        finally:
            release_lock(lock_file)


def init(path: str | Path) -> int:
    """存在しない表を作成する。"""
    target = _path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        acquire_lock(lock_file)
        try:
            if target.exists():
                raise ValueError(f"レビュー表が既に存在する: {target}")
            _write_atomic(target, [])
        finally:
            release_lock(lock_file)
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


def _required_value(args: argparse.Namespace, option: str, positional: str) -> str:
    value = getattr(args, option) or getattr(args, positional)
    if not isinstance(value, str) or not value:
        raise ValueError(f"--{option}を指定する")
    return value


def build_parser(parent: argparse._SubParsersAction) -> None:
    """`review-table`配下のサブコマンドを登録する。"""
    review = parent.add_parser("review-table", help="レビュー指摘管理表（7列TSV）を操作する")
    sub = review.add_subparsers(dest="review_table_subcommand", required=True)
    init_parser = sub.add_parser("init", help="空のレビュー表を作成する")
    init_parser.add_argument("path")
    add_parser = sub.add_parser("add", help="レビュー担当の指摘を追加する")
    add_parser.add_argument("path")
    add_parser.add_argument("--round", required=True)
    add_parser.add_argument("--track", required=True, choices=TRACK_VALUES, help=_RECOVERY_GUIDANCE)
    for name, positional in (("location", "location_arg"), ("issue", "issue_arg")):
        add_parser.add_argument(positional, nargs="?")
        add_parser.add_argument(f"--{name}")
    respond_parser = sub.add_parser(
        "respond",
        help=(
            "round・track・location・issueのうち行を一意に特定できる列だけを指定してレビューイーの応答を更新する。"
            " 各セルはJSON文字列として保存されるため、--issueには復号後の本文を渡す。"
        ),
    )
    respond_parser.add_argument("path")
    respond_parser.add_argument("--round")
    respond_parser.add_argument("--track", choices=TRACK_VALUES)
    for name, positional in (("location", "location_arg"), ("issue", "issue_arg")):
        respond_parser.add_argument(positional, nargs="?")
        respond_parser.add_argument(f"--{name}")
    respond_parser.add_argument("--response-needed", required=True, choices=("yes", "no", "対応要", "対応不要"))
    respond_parser.add_argument("--response", default="")
    respond_parser.add_argument("--no-response-reason", default="")
    show_parser = sub.add_parser("show", help="レビュー表を表示する")
    show_parser.add_argument("path")
    show_parser.add_argument("--track", choices=TRACK_VALUES, help=_RECOVERY_GUIDANCE)
    validate_parser = sub.add_parser("validate", help="レビュー表を検証する")
    validate_parser.add_argument(
        "--allow-unanswered",
        action="store_true",
        help=f"未応答行を許容し、{_COLUMN_COUNT}列と複合キーなどの構造だけを検証する。{_RECOVERY_GUIDANCE}",
    )
    validate_parser.add_argument("path")


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
        location = args.location or args.location_arg or ""
        issue = args.issue or args.issue_arg or ""
        if not any((round_value, track, location, issue)):
            raise ValueError("round・track・location・issueのいずれかを指定する")
        return respond(
            args.path,
            round_value,
            track,
            location,
            issue,
            args.response_needed,
            args.response,
            args.no_response_reason,
        )
    raise ValueError(f"未知のreview-tableサブコマンド: {command}")
