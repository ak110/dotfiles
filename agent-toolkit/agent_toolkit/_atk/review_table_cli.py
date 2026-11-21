"""`atk review-table`の引数を解析し、レビュー指摘管理表の操作へ振り分ける。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk.review_table import (
    _add_rows,
    _path,
    _read_table_text,
    add,
    init,
    recheck,
    respond,
    show,
    validate,
)
from agent_toolkit._atk.review_table_format import (
    _COLUMN_COUNT,
    _TRACK_INPUT_VALUES,
    _TRACK_READ_VALUES,
    LEVEL_VALUES,
    RECHECK_VERDICTS,
    _decode_cell,
    _normalize_track,
    _parse_text,
    _require_issue_labels,
    _row_position,
)
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._plan import bundle_kinds as _bundle_kinds
from agent_toolkit._plan import locations as _plan_locations

_ActionableError = _next_action.ActionableError


def _row_id(raw: str) -> int:
    """1始まりの行識別子を検証する。"""
    try:
        value = int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            _next_action.with_next_action(f"row-idが整数ではない: {raw}", "row-idは1以上の整数で指定する")
        ) from error
    if value < 1:
        raise argparse.ArgumentTypeError(
            _next_action.with_next_action(f"row-idが1未満である: {raw}", "row-idは1以上の整数で指定する")
        )
    return value


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
        raise _ActionableError(
            f"{option}の読み込みに失敗した: {raw_path}（{error}）",
            next_action=f"{option}へ読み取れるファイルのパスを指定して再実行する",
        ) from error
    except UnicodeDecodeError as error:
        raise _ActionableError(
            f"{option}をUTF-8として解釈できない: {raw_path}",
            next_action=f"{option}へUTF-8で保存したファイルを指定して再実行する",
        ) from error


def _cell_value(args: argparse.Namespace, dest: str) -> str:
    """`--<名前>-file`からセル本文を返す。"""
    raw_path = getattr(args, f"{dest}_file", None)
    if raw_path is not None:
        return _read_cell_file(f"--{dest.replace('_', '-')}-file", raw_path)
    return ""


def _required_value(args: argparse.Namespace, option: str) -> str:
    value = _cell_value(args, option)
    if not isinstance(value, str) or not value:
        raise _ActionableError(
            f"--{option}-fileが指定されていない", next_action=f"--{option}-fileへ本文を記載したファイルのパスを指定する"
        )
    return value


def _read_add_items(raw_path: str, round_value: str, track: str) -> list[list[str]]:
    """UTF-8 JSON配列の指摘本文とレベルを、共通round・trackの行へ変換する。"""
    guidance = "--items-fileへlocation・issue・levelの文字列3キーを持つ非空JSON配列をUTF-8で保存して指定する"
    try:
        items = json.loads(_read_cell_file("--items-file", raw_path))
    except json.JSONDecodeError as error:
        raise _ActionableError(f"--items-fileのJSONが不正である: {error}", next_action=guidance) from error
    if not isinstance(items, list) or not items:
        raise _ActionableError("--items-fileは非空のJSON配列が必要である", next_action=guidance)
    rows = []
    for index, item in enumerate(items, start=1):
        if (
            not isinstance(item, dict)
            or set(item) != {"location", "issue", "level"}
            or any(not isinstance(value, str) for value in item.values())
        ):
            raise _ActionableError(f"--items-fileの項目{index}のキーか値の型が不正である", next_action=guidance)
        if item["level"] not in LEVEL_VALUES:
            raise _ActionableError(
                f"--items-fileの項目{index}のlevelが正規値ではない",
                next_action=f"levelを{', '.join(LEVEL_VALUES)}のいずれかにして再実行する",
            )
        _require_issue_labels(item["location"], item["issue"], round_value, f"--items-fileの項目{index}のissue")
        rows.append([round_value, _normalize_track(track), item["location"], item["issue"], item["level"], "", ""])
    return rows


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
    init_parser.add_argument("path", help="前提に示した書式で、新しく作るレビュー指摘管理表の絶対パスを指定する。")
    path_help = "記録済みのレビュー指摘管理表の絶対パス。旧名の表を使う場合も、そのパスを指定する。"
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
        help="指摘を登録するレビューのラウンド番号。1以上で、同じtrackの指摘の行と有効な判定の最大のround以上の整数を指定する。",
    )
    add_command_parser.add_argument(
        "--track",
        required=True,
        choices=_TRACK_INPUT_VALUES,
        help="指摘を登録するレビューの区分。レビュー工程に対応する正規値から指定する。",
    )
    add_input = add_command_parser.add_mutually_exclusive_group(required=True)
    add_input.add_argument(
        "--level",
        choices=LEVEL_VALUES,
        help="指摘レベル。要件、仕様、詳細または実装を指定する。",
    )
    add_input.add_argument(
        "--items-file",
        metavar="PATH",
        help="複数指摘のUTF-8 JSON配列。各要素は文字列location・issue・levelの3必須キー。"
        "round・trackは全件共通。単数形の--level・--location-file・--issue-fileとは併用できない。",
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
        "--row-id",
        type=_row_id,
        help="showが出力した1始まりの行識別子。round・track・location・issueとは併用できない。",
    )
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
    _add_cell_options(respond_parser, "response", "対応要とした指摘へ記録する対応内容")
    _add_cell_options(respond_parser, "no-response-reason", "対応不要とした指摘へ記録する理由")
    recheck_parser = _atk_help.add_command(
        sub,
        "recheck",
        allow_abbrev=False,
        **_atk_help.HELP["atk review-table recheck"],
    )
    recheck_parser.add_argument("path", help=path_help)
    recheck_parser.add_argument(
        "--row-id",
        type=_row_id,
        required=True,
        help="showが出力した1始まりの行識別子。現在のラウンドより前の、応答済みのレビュー担当の指摘行を指定する。",
    )
    recheck_parser.add_argument(
        "--round",
        required=True,
        help="現在の再レビューのラウンド番号。2以上で、同じtrackの指摘の行と有効な判定の最大のround以上の整数を指定する。",
    )
    recheck_parser.add_argument(
        "--verdict",
        required=True,
        choices=RECHECK_VERDICTS,
        help="レビューイーの応答を確かめた判定。完了、対応不十分または未対応を指定する。",
    )
    _add_cell_options(recheck_parser, "issue", "対応不十分・未対応で必須の、今回のroundへ登録する残る問題の指摘内容")
    show_parser = _atk_help.add_command(sub, "show", **_atk_help.HELP["atk review-table show"])
    show_parser.add_argument("path", help=path_help)
    show_parser.add_argument(
        "--row-id", type=_row_id, help="保存順の1始まりの行識別子で表示行を選ぶ。respondと同じ識別子を使う。"
    )
    show_parser.add_argument(
        "--field",
        choices=("response", "no-response-reason"),
        help="--row-idの行の指定列をデコード済み全文で表示する。tsv出力形式で使う。",
    )
    show_parser.add_argument(
        "--track",
        choices=_TRACK_READ_VALUES,
        help="表示対象を指定したレビュー区分の行だけに限定する。省略すると全行を表示する。",
    )
    show_parser.add_argument(
        "--round",
        type=int,
        help="表示対象を指定したラウンドの行だけに限定する。省略すると全ラウンドを表示する。",
    )
    show_parser.add_argument(
        "--format",
        choices=("tsv", "jsonl", "summary"),
        default="tsv",
        help="出力形式。tsvは先頭にrow-idを付けたTSV、jsonlはrow-idとデコード済み各列のJSON Lines。"
        "summaryは--roundで指定したラウンドの登録数・未応答数・応答済み数を1件のJSONで返す。"
        "summaryのtrack省略時はexec-review。現在と過去の同じtrackに指摘がある場合は、"
        "previous_findings（過去roundとrow_ids）、history_command（全roundを読む操作）、"
        "repetition_guidance（反復原因の判定先）も返す。"
        "同じtrackの過去roundに指摘がある場合は、直前の指摘roundのrecheck_round、再確認の無い行のrecheck_pending_row_ids・"
        "recheck_pending_count、判定別のrecheck_countsも返す。",
    )
    validate_parser = _atk_help.add_command(sub, "validate", **_atk_help.HELP["atk review-table validate"])
    validate_parser.add_argument(
        "--allow-unanswered",
        action="store_true",
        help=f"未応答行を許容し、{_COLUMN_COUNT}列と複合キーなどの構造だけを検証する。",
    )
    validate_parser.add_argument("path", help=path_help)


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果をレビュー指摘管理表の操作へ振り分ける。"""
    command = args.review_table_subcommand
    if command == "init":
        _require_writable_exec_review(args.path)
        return init(args.path)
    if command == "show":
        return show(args.path, args.track, args.format, args.round, row_id=args.row_id, field=args.field)
    if command == "validate":
        return validate(args.path, require_responses=not args.allow_unanswered)
    if command == "add":
        _require_writable_exec_review(args.path)
        items_file = getattr(args, "items_file", None)
        if items_file is not None:
            if any((args.level is not None, args.location_file is not None, args.issue_file is not None)):
                raise _ActionableError(
                    "--items-fileは単数形のlevel・location・issue指定と同時に使えない",
                    next_action="--items-fileか、--level・--location-file・--issue-fileのどちらか一方を指定する",
                )
            return _add_rows(_path(args.path), _read_add_items(items_file, args.round, args.track))
        location = _required_value(args, "location")
        issue = _required_value(args, "issue")
        _require_issue_labels(location, issue, args.round, "--issue-file")
        return add(args.path, args.round, args.track, location, issue, args.level)
    if command == "recheck":
        _require_writable_exec_review(args.path)
        return recheck(args.path, args.row_id, args.round, args.verdict, _cell_value(args, "issue"))
    if command == "respond":
        _require_writable_exec_review(args.path)
        round_value = args.round or ""
        track = args.track or ""
        location = _cell_value(args, "location")
        issue = _cell_value(args, "issue")
        if args.row_id is not None and any((round_value, track, location, issue)):
            raise _ActionableError(
                "row-idはround・track・location・issueと同時に指定できない",
                next_action="--row-idか、--round・--track・--location-file・--issue-fileのどちらか一方で行を指定する",
            )
        if args.row_id is None and not any((round_value, track, location, issue)):
            raise _ActionableError(
                "応答する行が指定されていない",
                next_action="--row-id・--round・--track・--location-file・--issue-fileのいずれかで行を指定する",
            )
        return respond(
            args.path,
            round_value,
            track,
            location,
            issue,
            _cell_value(args, "response"),
            _cell_value(args, "no_response_reason"),
            args.row_id,
        )
    raise ValueError(f"未知のreview-tableサブコマンド: {command}")


def _require_writable_exec_review(raw_path: str) -> None:
    """保存済み計画の領域の表と、保存済みの旧計画レビュー表を更新対象から除外する。"""
    target = _path(raw_path)
    _plan_locations.reject_saved_plans_root_write(target)
    name = target.name
    if _bundle_kinds.PLAN_REVIEW.matches(name) or (name.startswith("dlg-") and _bundle_kinds.EXEC_REVIEW.matches(name)):
        raise _ActionableError(
            "保存済みの旧形式のレビュー指摘管理表は読み取り専用です", next_action="更新には.exec-review.tsvを指定する"
        )
    if not target.exists():
        return
    for line_number, (raw_line, _row) in enumerate(_parse_text(_read_table_text(target)), start=1):
        raw_track = _decode_cell(raw_line.rstrip("\r\n").split("\t")[1], line=line_number, column=2)
        if raw_track != "exec-review":
            raise _ActionableError(
                f"{_row_position(line_number)}の旧review typeは読み取り専用です: {raw_track}",
                next_action="更新にはexec-reviewの行だけを持つ.exec-review.tsvを指定する",
            )
