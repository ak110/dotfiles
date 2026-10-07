"""`atk wi grep`による本文全体の正規表現検索。"""

import argparse
import pathlib
import re

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.repo import resolve_repo_id
from agent_toolkit._common import next_action as _next_action


def cmd_grep(args: argparse.Namespace, private_notes: pathlib.Path) -> int:
    """grepサブコマンド: 本文全体（frontmatterを含む）を正規表現で検索し、該当行を列挙する。

    該当行は`<ファイル名>:<行番号>:<該当行>`形式（git grep準拠の出力形式）で列挙する。
    行番号はファイル先頭から1始まり。`--type`・`--state`・`--answered`・`--source`・`--target-repo`は
    `list`サブコマンドと同じ選択肢と、省略時に使う値を採用する。パターンはPythonの正規表現（`re`モジュール）
    として解釈し、`--ignore-case`指定時は大文字小文字を無視する。
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
    try:
        compiled = re.compile(args.pattern, flags)
    except re.error as error:
        # argparseの使い方の表示と終了コード2は保ち、直し方を1行に続ける。
        args.subparser.error(
            f"正規表現が不正です: {error}\n"
            + _next_action.next_action_line(
                "Pythonの正規表現の構文を確かめて直すか、記号を文字として探す場合は`\\`でエスケープして再実行する"
            )
        )
        raise AssertionError("unreachable") from error  # pragma: no cover - args.subparser.error()はSystemExitを送出する
    matched = False
    for path, _, text, _state, _entry_type in _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=filter_repo,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
    ):
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                matched = True
                print(f"{path.name}:{line_no}:{line}")
    if not matched:
        _outcome.report_no_match("検索条件に一致する行は無い。検索は正常に完了した")
        return 1
    return 0
