"""`atk wi grep`による本文全体の正規表現・固定文字列検索。"""

import argparse
import json
import pathlib
import re
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._atk.wi.formatters import body_summary, uwi_body_summary
from agent_toolkit._atk.wi.repo import resolve_repo_id
from agent_toolkit._common import next_action as _next_action


def cmd_grep(args: argparse.Namespace, private_notes: pathlib.Path) -> int:
    """grepサブコマンド: 本文全体（frontmatterを含む）を検索し、該当行または一致した項目を列挙する。

    出力形式を指定しない場合は該当行の`<ファイル名>:<行番号>:<該当行>`形式（git grep準拠の出力形式）で、
    行番号はファイル先頭から1始まり。`--count`は一致した項目ごとに`<ファイル名>:<一致行数>`、
    `--files-with-matches`は一致した項目のファイル名、`--summary-only`は一致した項目ごとに
    `atk wi list --summary-only`と同じ`filename`・`summary`に一致行数`match_count`を加えた1件1行のJSON Linesを書く。
    3つの形式は行の列挙と同じ走査で項目を選び、項目の選択と行単位の一致判定を形式ごとに分けない。
    `--type`・`--state`・`--answered`・`--source`・`--target-repo`は
    `list`サブコマンドと同じ選択肢と、省略時に使う値を採用する。パターンはPythonの正規表現（`re`モジュール）
    として解釈し、`--fixed-strings`指定時は全文字を文字そのものとして扱う。`--ignore-case`指定時は大文字小文字を無視する。
    該当0件の場合は1、該当1件以上で0を返す。
    非エラーの真偽判定を終了コードで表現し、検索処理自体の失敗とは区別する。
    整数で返すことで、`main`が戻り値から終了コードを確定する。
    検索結果を返す途中では`SystemExit`を送出しない。
    パターンが不正な正規表現の場合は`args.subparser.error()`でexit 2とする（既存の`edit`・
    `show`と同じ引数検証エラー時の扱いであり、こちらは意図的に共通後処理をスキップする）。
    """
    if not args.skip_pull:
        with _wi_sync.repo_lock(private_notes):
            _wi_sync.pull_with_recent_reuse(private_notes, force_pull=getattr(args, "pull", False))
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = resolve_repo_id(args.target_repo)
    flags = re.IGNORECASE if args.ignore_case else 0
    # 固定文字列は正規表現へエスケープし、`--ignore-case`と行単位の一致判定を`-F`を指定しない場合と同じ処理で使う。
    pattern = re.escape(args.pattern) if args.fixed_strings else args.pattern
    try:
        compiled = re.compile(pattern, flags)
    except re.error as error:
        # argparseの使い方の表示と終了コード2は保ち、直し方を1行に続ける。
        args.subparser.error(
            f"正規表現が不正です: {error}\n"
            + _next_action.next_action_line(
                "Pythonの正規表現の構文を確かめて直すか、記号を文字として探す場合は`\\`でエスケープして再実行する"
            )
        )
        raise AssertionError("unreachable") from error  # pragma: no cover - args.subparser.error()はSystemExitを送出する
    lines_only = not (args.count or args.files_with_matches or args.summary_only)
    matched = False
    for path, _, text, _state, entry_type in _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=filter_repo,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
        important=getattr(args, "important", "all"),
    ):
        match_count = 0
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                match_count += 1
                if lines_only:
                    print(f"{path.name}:{line_no}:{line}")
        if match_count == 0:
            continue
        matched = True
        if args.count:
            print(f"{path.name}:{match_count}")
        elif args.files_with_matches:
            print(path.name)
        elif args.summary_only:
            # `atk wi list --summary-only`と同じ要約の生成元を使い、同じ項目で同じ値にする。
            summary = uwi_body_summary(text, sys.maxsize) if entry_type == WI_TYPE_UWI else body_summary(text, sys.maxsize)
            record = {"filename": path.name, "summary": summary, "match_count": match_count}
            print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    if not matched:
        _outcome.report_no_match("検索条件に一致する行は無い。検索は正常に完了した")
        return 1
    return 0
