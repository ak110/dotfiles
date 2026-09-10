"""レビュー指摘管理表の8列TSVを排他更新する補助CLI。

排他ロックは表と同じディレクトリではなくホーム配下の専用ディレクトリへ置く。
表の配置先には計画作業rootが含まれる。兄弟のロックファイルを生成すると、計画バンドルの回収後も
ロックだけが作業rootへ残存し、計画の一覧と親ディレクトリの回収を妨げる。
表の本体ファイル自身へのロックには移行できない。更新は一時ファイルの原子的置換で行い、
置換のたびにinodeが変わるため、本体を開いて取得したロックは後続の更新と同じ実体を指さない。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path

from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import output_file as _output_file
from agent_toolkit._common import body_match as _body_match
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._common.atomic_file import atomic_write

# 配布物独立性のため、Web表示側`_atk_serve_plans.py`の`_REVIEW_TABLE_HEADERS`と同じ列順を二重に持つ。
# 列を増減する場合は双方を同期し、旧形式の読み取り互換も両側で更新する。
COLUMNS = (
    "round",
    "track",
    "location",
    "issue",
    "level",
    "response-needed",
    "response",
    "no-response-reason",
)
_COLUMN_COUNT = len(COLUMNS)
_KEY_COLUMN_COUNT = 4
TRACK_VALUES = ("plan-review", "exec-review", "plan-conformance", "independent")
_TRACK_ALIASES = {"implementation-review": "exec-review"}
_TRACK_INPUT_VALUES = (*TRACK_VALUES, *_TRACK_ALIASES)
LEVEL_VALUES = ("要件", "仕様", "詳細", "実装")
_RECOVERY_GUIDANCE = (
    f"期待列数は{_COLUMN_COUNT}、trackの位置はroundの直後、"
    f"levelの位置はissueの直後、levelの正規値集合は{', '.join(LEVEL_VALUES)}、"
    f"trackの正規値集合は{', '.join(TRACK_VALUES)}。"
    "implementation-reviewはexec-reviewとして読み取る。"
    "保存済み7列形式はlevelを空として読み込み、更新時に8列形式へ書き戻す"
)
_INPUT_GUIDANCE = (
    "計画ファイルと同じstemの`.plan-review.tsv`か`.exec-review.tsv`、または原因commit完全OID由来の"
    "`ci-<OID>.exec-review.tsv`、実装着手前の完全OID由来の`dlg-<OID>.exec-review.tsv`を"
    "通常ファイルの絶対パスで指定する。"
    "標準入力、パイプ及びプロセス置換は受理しない"
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
    """JSON文字列セルをデコードし、形式不正をエラーにする。"""
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"{line}行{column}列がJSON文字列ではない") from error
    if not isinstance(decoded, str):
        raise ValueError(f"{line}行{column}列が文字列ではない")
    return decoded


def _normalize_track(value: str) -> str:
    """レビューtrackの読み取り互換値を正規値へ変換する。"""
    return _TRACK_ALIASES.get(value, value)


def _parse_text(text: str) -> list[tuple[str, list[str]]]:
    """Raw TSVを検証し、元の行とtrack正規化済みのデコード済み行を対応づけて返す。"""
    rows: list[tuple[str, list[str]]] = []
    for line_number, raw_line in enumerate(text.splitlines(keepends=True), start=1):
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        cells = line.split("\t")
        if len(cells) not in (_COLUMN_COUNT - 1, _COLUMN_COUNT):
            raise ValueError(f"{line_number}行の列数が{_COLUMN_COUNT}ではない: {len(cells)}。{_RECOVERY_GUIDANCE}")
        row = [_decode_cell(cell, line=line_number, column=index) for index, cell in enumerate(cells, start=1)]
        if len(row) == _COLUMN_COUNT - 1:
            row.insert(4, "")
        row[1] = _normalize_track(row[1])
        rows.append((raw_line, row))
    return rows


def _read_table_text(path: Path) -> str:
    """レビュー表の本文をUTF-8で読む。

    通常ファイル以外は読み込みを試みずに拒否する。標準入力とプロセス置換は`/dev/fd`配下の
    パイプとして渡り、読み込むと書き込み側を待って停止するためである。
    存在しない場合と通常ファイルでない場合を別の文面にするのは、パイプを`/dev/stdin`として
    渡すと`_path`の解決が存在しないパスへ至り、同じ文面では原因を判別できないためである。
    """
    if not path.exists():
        raise ValueError(f"レビュー表を読み込めない: {path}: 存在しない。{_INPUT_GUIDANCE}")
    if not path.is_file():
        raise ValueError(f"レビュー表を読み込めない: {path}: 通常ファイルではない。{_INPUT_GUIDANCE}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"レビュー表を読み込めない: {path}: {error}。{_INPUT_GUIDANCE}") from error


def _read(path: Path) -> list[list[str]]:
    """TSVを読み、JSONデコード済みの行一覧を返す。"""
    return [row for _, row in _parse_text(_read_table_text(path))]


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
    """8列、先頭4列の複合キー一意性、指摘レベル及び応答分岐を検証する。"""
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
        if row[4] and row[4] not in LEVEL_VALUES:
            raise ValueError(f"{index}行のlevelが正規値ではない。{_RECOVERY_GUIDANCE}")
        key = _key(row)
        if key in keys:
            raise ValueError(f"{index}行の先頭4列が重複している")
        keys.add(key)
        response_needed = _normalized(row[5]).casefold()
        response = row[6].strip()
        reason = row[7].strip()
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


def lock_path(path: str | Path) -> Path:
    """レビュー表の排他に使うロックファイルのパスを返す。

    格納先を表と同じディレクトリから分離し、対象の絶対パスのダイジェストで名前を一意にする。
    分離の理由と本体ファイル自身をロックできない理由はモジュールのdocstringが述べる。
    ロックファイルは解放後も削除しない。削除してから再取得するまでの間に別の主体が同名のファイルを
    生成した場合、別々の実体へロックを取得した2主体が同時に表を更新する。
    """
    target = Path(path).expanduser().resolve(strict=False)
    digest = hashlib.sha256(target.as_posix().encode("utf-8")).hexdigest()[:32]
    return Path.home() / ".claude" / ".atk-locks" / "review-table" / f"{digest}.lock"


@contextlib.contextmanager
def _table_lock(path: Path) -> Iterator[None]:
    """レビュー表の排他ロックを取得し、離脱時に解放する。"""
    target_lock = lock_path(path)
    target_lock.parent.mkdir(parents=True, exist_ok=True)
    with target_lock.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        try:
            yield
        finally:
            _file_lock.release_lock(lock_file)


def _locked_update(path: Path, updater: Callable[[list[list[str]]], list[list[str]]]) -> list[list[str]]:
    """ロック内で再読込・検証・更新・原子的置換を実行する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _table_lock(path):
        rows = _read(path) if path.exists() else []
        _validate_rows(rows)
        updated = updater(rows)
        _validate_rows(updated)
        _write_atomic(path, updated)
        return updated


def init(path: str | Path) -> int:
    """存在しない表を作成する。"""
    target = _path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    with _table_lock(target):
        if target.exists():
            raise ValueError(f"レビュー表が既に存在する: {target}")
        _write_atomic(target, [])
    print(target)
    return 0


def add(path: str | Path, round_value: str, track: str, location: str, issue: str, level: str = "詳細") -> int:
    """レビュー担当の指摘行を追加する。"""
    target = _path(str(path))
    row = [round_value, _normalize_track(track), location, issue, level, "", "", ""]

    def updater(rows: list[list[str]]) -> list[list[str]]:
        if _key(row) in {_key(existing) for existing in rows}:
            raise ValueError("先頭4列の複合キーが重複している")
        return [*rows, row]

    rows = _locked_update(target, updater)
    saved_rows = [saved_row for saved_row in _read(target) if _key(saved_row) == _key(row)]
    if len(saved_rows) != 1:
        raise ValueError(f"追加した行を保存済みの表から一意に解決できない: {len(saved_rows)}件")
    saved_row = saved_rows[0]
    for column, expected, saved in (("location", location, saved_row[2]), ("issue", issue, saved_row[3])):
        if _body_match.verdict(expected, saved) != "一致":
            position = _body_match.first_difference(expected, saved)
            raise ValueError(
                f"保存本文が送信元本文と一致しない: {target}\n"
                f"不一致の列: {column}\n"
                f"最初の差異: {position}文字目\n"
                f"送信元本文:\n{expected}\n"
                f"保存本文:\n{saved}"
            )
    print(f"追加成功: {target} ({len(rows)}件)")
    print("location_body_match: 一致")
    print("issue_body_match: 一致")
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
    """一意に解決できない部分キーとデコード済み候補行を整形する。"""
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
        f"候補行（デコード済み）:\n{candidates}\n"
        "レビュー表のセルはJSON文字列として保存されるため、キーにはデコード後の値を指定する。"
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
    track = _normalize_track(track)
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
        updated[matches[0]] = [*updated[matches[0]][:_KEY_COLUMN_COUNT], updated[matches[0]][4], needed, replacement, reason]
        return updated

    _locked_update(target, updater)
    saved_rows = [row for row in _read(target) if all(_normalized(row[column_index]) == value for column_index, value in given)]
    if len(saved_rows) != 1:
        raise ValueError(f"更新した行を保存済みの表から一意に解決できない: {len(saved_rows)}件")
    saved_row = saved_rows[0]
    saved_body = saved_row[6] if needed == "yes" else saved_row[7]
    expected_body = replacement if needed == "yes" else reason
    if _body_match.verdict(expected_body, saved_body) != "一致":
        position = _body_match.first_difference(expected_body, saved_body)
        column = "response" if needed == "yes" else "no_response_reason"
        raise ValueError(
            f"保存本文が送信元本文と一致しない: {target}\n"
            f"不一致の列: {column}\n"
            f"最初の差異: {position}文字目\n"
            f"送信元本文:\n{expected_body}\n"
            f"保存本文:\n{saved_body}"
        )
    print(f"応答更新成功: {target}")
    print("body_match: 一致")
    return 0


def show(
    path: str | Path,
    track: str | None = None,
    output_format: str = "tsv",
    round_value: int | None = None,
) -> int:
    """表を保存順で表示し、指定時はtrackとラウンドで限定する。"""
    target = _path(str(path))
    text = _read_table_text(target)
    rows = _parse_text(text)
    if track is not None and track not in _TRACK_INPUT_VALUES:
        raise ValueError(f"trackが正規値ではない。{_RECOVERY_GUIDANCE}")
    track = _normalize_track(track) if track is not None else None
    selected = [
        (raw_line, row)
        for raw_line, row in rows
        if (track is None or row[1] == track) and (round_value is None or row[0] == str(round_value))
    ]
    if output_format == "tsv":
        print("".join(raw_line for raw_line, _ in selected), end="")
        return 0
    for _, row in selected:
        print(json.dumps(dict(zip(COLUMNS, row, strict=True)), ensure_ascii=False))
    return 0


def _add_cell_options(parser: argparse.ArgumentParser, option: str, description: str) -> None:
    """セル本文をファイルから読むオプションを登録する。

    `atk wi add --body-file`と同じ利用形とし、引用符・改行・バッククォートを含む本文を
    シェルの引用規則を経由せずに渡せるようにする。
    """
    parser.add_argument(
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
    """`--<名前>-file`からセル本文を返す。"""
    raw_path = getattr(args, f"{dest}_file", None)
    if raw_path is not None:
        return _read_cell_file(f"--{dest.replace('_', '-')}-file", raw_path)
    return ""


def _required_value(args: argparse.Namespace, option: str) -> str:
    value = _cell_value(args, option)
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
    )
    init_parser = _atk_help.add_command(sub, "init", **_atk_help.HELP["atk review-table init"])
    path_help = (
        "操作するレビュー指摘管理表のパス。計画ファイルと同じstemの`.plan-review.tsv`か"
        "`.exec-review.tsv`、または原因commit完全OID由来の`ci-<OID>.exec-review.tsv`、"
        "実装着手前の完全OID由来の`dlg-<OID>.exec-review.tsv`を指定する。"
    )
    init_parser.add_argument("path", help=path_help)
    add_command_parser = _atk_help.add_command(
        sub,
        "add",
        allow_abbrev=False,
        **_atk_help.HELP["atk review-table add"],
    )
    add_command_parser.add_argument("path", help=path_help)
    add_command_parser.add_argument(
        "--round",
        required=True,
        help="指摘を登録するレビューのラウンド番号。1以上の整数で指定する。",
    )
    add_command_parser.add_argument(
        "--track",
        required=True,
        choices=_TRACK_INPUT_VALUES,
        help="指摘を登録するレビューの区分。レビュー工程に対応する正規値から指定する。",
    )
    add_command_parser.add_argument(
        "--level",
        required=True,
        choices=LEVEL_VALUES,
        help="指摘レベル。要件、仕様、詳細又は実装を指定する。",
    )
    for name, description in (
        ("location", "追加する指摘箇所"),
        ("issue", "追加する指摘内容"),
    ):
        _add_cell_options(add_command_parser, name, description)
    respond_parser = _atk_help.add_command(
        sub,
        "respond",
        allow_abbrev=False,
        **_atk_help.HELP["atk review-table respond"],
    )
    respond_parser.add_argument("path", help=path_help)
    respond_parser.add_argument(
        "--round",
        help="更新する行を特定するラウンド番号。省略すると他の列だけで行を特定する。",
    )
    respond_parser.add_argument(
        "--track",
        choices=_TRACK_INPUT_VALUES,
        help="更新する行を特定するレビューの区分。省略すると他の列だけで行を特定する。",
    )
    for name, description in (
        ("location", "更新する行を特定する指摘箇所"),
        ("issue", "更新する行を特定する指摘内容"),
    ):
        _add_cell_options(respond_parser, name, description)
    respond_parser.add_argument(
        "--response-needed",
        required=True,
        choices=("yes", "no", "対応要", "対応不要"),
        help="指摘への対応要否。yes又は対応要、no又は対応不要を指定する。",
    )
    _add_cell_options(respond_parser, "response", "対応要とした指摘へ記録する対応内容")
    _add_cell_options(respond_parser, "no-response-reason", "対応不要とした指摘へ記録する理由")
    show_parser = _atk_help.add_command(sub, "show", **_atk_help.HELP["atk review-table show"])
    show_parser.add_argument("path", help=path_help)
    show_parser.add_argument(
        "--track",
        choices=_TRACK_INPUT_VALUES,
        help="表示対象を指定したレビュー区分の行だけに限定する。省略すると全行を表示する。",
    )
    show_parser.add_argument(
        "--round",
        type=int,
        help="表示対象を指定したラウンドの行だけに限定する。省略すると全ラウンドを表示する。",
    )
    show_parser.add_argument(
        "--format",
        choices=("tsv", "jsonl"),
        default="tsv",
        help="出力形式。tsvは保存済みのraw TSV、jsonlはデコード済みのJSON Linesを表示する。",
    )
    _output_file.add_output_file_arg(show_parser)
    validate_parser = _atk_help.add_command(sub, "validate", **_atk_help.HELP["atk review-table validate"])
    validate_parser.add_argument(
        "--allow-unanswered",
        action="store_true",
        help=f"未応答行を許容し、{_COLUMN_COUNT}列と複合キーなどの構造だけを検証する。",
    )
    validate_parser.add_argument("path", help=path_help)


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果をレビュー表操作へ振り分ける。"""
    command = args.review_table_subcommand
    if command == "init":
        return init(args.path)
    if command == "show":
        return show(args.path, args.track, args.format, args.round)
    if command == "validate":
        return validate(args.path, require_responses=not args.allow_unanswered)
    if command == "add":
        location = _required_value(args, "location")
        issue = _required_value(args, "issue")
        return add(args.path, args.round, args.track, location, issue, args.level)
    if command == "respond":
        round_value = args.round or ""
        track = args.track or ""
        location = _cell_value(args, "location")
        issue = _cell_value(args, "issue")
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
