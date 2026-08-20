"""レビュー指摘管理表の6列TSVを排他更新する補助CLI。"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Callable
from pathlib import Path

from _file_lock import acquire_lock, release_lock

COLUMNS = (
    "severity",
    "location",
    "issue",
    "response-needed",
    "response",
    "no-response-reason",
)
_COLUMN_COUNT = len(COLUMNS)
_YES_VALUES = frozenset({"yes", "true", "1", "required", "対応要"})
_NO_VALUES = frozenset({"no", "false", "0", "not-required", "対応不要"})
_WHITESPACE_RE = re.compile(r"\s+")


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
            raise ValueError(f"{line_number}行の列数が{_COLUMN_COUNT}ではない: {len(cells)}")
        rows.append([_decode_cell(cell, line=line_number, column=index) for index, cell in enumerate(cells, start=1)])
    return rows


def _normalized(value: str) -> str:
    """複合キー用にUnicode、前後空白及び連続空白を正規化する。"""
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", value).strip())


def _key(row: list[str]) -> tuple[str, str, str]:
    return (_normalized(row[0]), _normalized(row[1]), _normalized(row[2]))


def _validate_rows(rows: list[list[str]], *, require_responses: bool = False) -> None:
    """6列、複合キー一意性及び応答分岐を検証する。"""
    keys: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if len(row) != _COLUMN_COUNT:
            raise ValueError(f"{index}行の列数が{_COLUMN_COUNT}ではない")
        if any(not _normalized(value) for value in row[:3]):
            raise ValueError(f"{index}行の先頭3列は空にできない")
        key = _key(row)
        if key in keys:
            raise ValueError(f"{index}行の先頭3列が重複している")
        keys.add(key)
        response_needed = _normalized(row[3]).casefold()
        response = row[4].strip()
        reason = row[5].strip()
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


def validate(path: str | Path) -> int:
    """表全体を検証し、件数を標準出力へ表示する。"""
    target = _path(str(path))
    rows = _read(target)
    _validate_rows(rows, require_responses=True)
    print(f"検証成功: {target} ({len(rows)}件)")
    return 0


def _write_atomic(path: Path, rows: list[list[str]]) -> None:
    """行を一時ファイルへ書き、同一ディレクトリ内で原子的に置換する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            for row in rows:
                handle.write("\t".join(_cell(value) for value in row))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


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
    if target.exists():
        raise ValueError(f"レビュー表が既に存在する: {target}")
    _write_atomic(target, [])
    print(target)
    return 0


def add(path: str | Path, severity: str, location: str, issue: str) -> int:
    """レビュー担当の指摘行を追加する。"""
    target = _path(str(path))
    row = [severity, location, issue, "", "", ""]

    def updater(rows: list[list[str]]) -> list[list[str]]:
        if _key(row) in {_key(existing) for existing in rows}:
            raise ValueError("先頭3列の複合キーが重複している")
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


def respond(
    path: str | Path,
    severity: str,
    location: str,
    issue: str,
    response_needed: str,
    response: str,
    no_response_reason: str,
) -> int:
    """レビューイーの応答欄だけを複合キーで更新する。"""
    target = _path(str(path))
    needed = _response_value(response_needed)
    replacement = response.strip() if needed == "yes" else ""
    reason = no_response_reason.strip() if needed == "no" else ""
    if needed == "no" and not reason:
        reason = response.strip()
    key = tuple(_normalized(value) for value in (severity, location, issue))

    def updater(rows: list[list[str]]) -> list[list[str]]:
        matches = [index for index, row in enumerate(rows) if _key(row) == key]
        if len(matches) != 1:
            raise ValueError(f"応答対象の複合キーが一意に解決できない: {len(matches)}件")
        updated = [*rows]
        updated[matches[0]] = [*updated[matches[0]][:3], needed, replacement, reason]
        return updated

    _locked_update(target, updater)
    print(f"応答更新成功: {target}")
    return 0


def show(path: str | Path) -> int:
    """表のraw TSVを保存順のまま表示する。"""
    target = _path(str(path))
    try:
        print(target.read_text(encoding="utf-8"), end="")
    except OSError as error:
        raise ValueError(f"レビュー表を解釈できない: {target}: {error}") from error
    return 0


def _required_value(args: argparse.Namespace, option: str, positional: str) -> str:
    value = getattr(args, option) or getattr(args, positional)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{option}を指定する")
    return value


def build_parser(parent: argparse._SubParsersAction) -> None:
    """`review-table`配下のサブコマンドを登録する。"""
    review = parent.add_parser("review-table", help="レビュー指摘管理表（6列TSV）を操作する")
    sub = review.add_subparsers(dest="review_table_subcommand", required=True)
    init_parser = sub.add_parser("init", help="空のレビュー表を作成する")
    init_parser.add_argument("path")
    add_parser = sub.add_parser("add", help="レビュー担当の指摘を追加する")
    add_parser.add_argument("path")
    for name, positional in (("severity", "severity_arg"), ("location", "location_arg"), ("issue", "issue_arg")):
        add_parser.add_argument(positional, nargs="?")
        add_parser.add_argument(f"--{name}")
    respond_parser = sub.add_parser("respond", help="複合キーでレビューイーの応答を更新する")
    respond_parser.add_argument("path")
    for name, positional in (("severity", "severity_arg"), ("location", "location_arg"), ("issue", "issue_arg")):
        respond_parser.add_argument(positional, nargs="?")
        respond_parser.add_argument(f"--{name}")
    respond_parser.add_argument("--response-needed", required=True, choices=("yes", "no", "対応要", "対応不要"))
    respond_parser.add_argument("--response", default="")
    respond_parser.add_argument("--no-response-reason", default="")
    for name in ("show", "validate"):
        parser = sub.add_parser(name, help=f"レビュー表を{name}する")
        parser.add_argument("path")


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果をレビュー表操作へ振り分ける。"""
    command = args.review_table_subcommand
    if command == "init":
        return init(args.path)
    if command == "show":
        return show(args.path)
    if command == "validate":
        return validate(args.path)
    severity = _required_value(args, "severity", "severity_arg")
    location = _required_value(args, "location", "location_arg")
    issue = _required_value(args, "issue", "issue_arg")
    if command == "add":
        return add(args.path, severity, location, issue)
    if command == "respond":
        return respond(
            args.path,
            severity,
            location,
            issue,
            args.response_needed,
            args.response,
            args.no_response_reason,
        )
    raise ValueError(f"未知のreview-tableサブコマンド: {command}")
