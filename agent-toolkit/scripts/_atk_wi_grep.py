"""agent-toolkitプラグイン配下の`atk wi grep`コマンド用補助モジュール。

`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import pathlib
import re

from _atk_wi_common import _iter_entries, _pull_with_recent_reuse, _repo_lock
from _atk_wi_list import _answered_matches, _resolve_states
from _atk_wi_repo import _resolve_repo_id


def _cmd_grep(args: argparse.Namespace, private_notes: pathlib.Path) -> int:
    """grepサブコマンド: 本文全体（frontmatterを含む）を正規表現で検索し、該当行を列挙する。

    該当行は`<ファイル名>:<行番号>:<該当行>`形式（git grep準拠の出力形式）で列挙する。
    行番号はファイル先頭から1始まり。`--type`・`--status`・`--answered`・`--target-repo`は
    `list`サブコマンドと同一の選択肢・既定値を踏襲する。パターンはPythonの正規表現（`re`モジュール）
    として解釈し、`--ignore-case`指定時は大文字小文字を無視する。
    該当0件の場合は1、該当1件以上で0を返す。
    非エラーの真偽判定を終了コードで表現し、検索処理自体の失敗とは区別する。
    戻り値をそのまま`sys.exit`せず整数で返す設計は、`main`関数末尾の共通後処理
    （`_covers_unanswered_uwis`による通知抑止判定・`notify_unanswered_uwis_if_any`呼び出し）が
    `dispatch[sub]()`の直後で必ず実行される必要があるためである。ここで`sys.exit`すると
    `SystemExit`が`main`関数の呼び出し元まで伝播し、共通後処理が実行されずに終了してしまう。
    パターンが不正な正規表現の場合は`args.subparser.error()`でexit 2とする（既存の`edit`・
    `show`と同じ引数検証エラー時の扱いであり、こちらは意図的に共通後処理をスキップする）。
    """
    if not args.skip_pull:
        with _repo_lock(private_notes):
            _pull_with_recent_reuse(private_notes, force_pull=getattr(args, "pull", False))
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)
    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        compiled = re.compile(args.pattern, flags)
    except re.error as error:
        args.subparser.error(f"正規表現が不正です: {error}")
        raise AssertionError("unreachable") from error  # pragma: no cover - args.subparser.error()はSystemExitを送出する
    matched = False
    for path, _, text, _state, entry_type in _iter_entries(
        private_notes,
        _resolve_states(args.status),
        filter_repo,
        args.type,
    ):
        if not _answered_matches(entry_type, text, args.answered):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                matched = True
                print(f"{path.name}:{line_no}:{line}")
    return 0 if matched else 1
