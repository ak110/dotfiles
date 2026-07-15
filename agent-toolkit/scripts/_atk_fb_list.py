"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_list.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import pathlib

from _atk_fb_common import (
    FEEDBACK_STATE_INBOX,
    FEEDBACK_STATE_PROCESSING,
    _is_tbd_answered,
    _iter_inbox_entries,
    _pull,
)
from _atk_fb_formatters import _body_summary, _tbd_body_summary
from _atk_fb_repo import _resolve_repo_id


def _render_tbd_entries(entries: list[tuple[pathlib.Path, str, str]]) -> None:
    """TBDエントリ一覧を`# tbd`種別ヘッダ付きの1件1行形式で標準出力へ出力する。

    入力は`_iter_inbox_entries`の返り値と同形式の`(path, target_repo, text)`リストとする。
    `list`サブコマンドと`tbd-list`サブコマンドの双方から共通出力ヘルパーとして呼び出す。
    入力が空リストの場合は何も出力しない。
    """
    if not entries:
        return
    print("# tbd")
    for path, target_repo, text in entries:
        label = "answered" if _is_tbd_answered(text) else "unanswered"
        print(f"{path.name}\t{target_repo}\t[{label}] {_tbd_body_summary(text)}")


def _cmd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """listサブコマンド: feedback/tbd inbox・processing全件を1件1行（filename・target_repo・本文冒頭要約）で出力する。

    `--type`指定で出力対象種別（feedback・tbd・all）を限定する（既定: all）。
    `--status`指定で表示範囲を限定する（既定: all）。
    feedback側は`inbox`・`processing`・`all`を解釈する（`inbox`・`processing`両方表示が既定）。
    tbd側は`answered`・`unanswered`で回答状況を限定する（`inbox`・`processing`・`all`は
    tbd側に作用せず、tbd inboxの全件を返す）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--type=all`（既定）指定時、該当部エントリが1件以上ある場合のみ種別ヘッダを出力する。
    `--count`指定時は、フィルター適用後のfeedback件数とTBD件数の合計を整数のみで出力し、
    種別ヘッダ・エントリ行は出力しない。
    """
    if not args.skip_pull:
        _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)

    feedback_entries: list[tuple[pathlib.Path, str, str]] = []
    if args.type in ("all", "feedback"):
        # feedback側`--status`解釈: `inbox`=inbox配下のみ・`processing`=processing配下のみ・
        # `all`=両方連結。回答状況指定（answered/unanswered）はfeedback側では既定（all）扱い。
        feedback_status = args.status if args.status in (FEEDBACK_STATE_INBOX, FEEDBACK_STATE_PROCESSING, "all") else "all"
        if feedback_status in (FEEDBACK_STATE_INBOX, "all"):
            inbox_dir = private_notes / "feedback" / FEEDBACK_STATE_INBOX
            feedback_entries.extend(_iter_inbox_entries(inbox_dir, filter_repo))
        if feedback_status in (FEEDBACK_STATE_PROCESSING, "all"):
            processing_dir = private_notes / "feedback" / FEEDBACK_STATE_PROCESSING
            feedback_entries.extend(_iter_inbox_entries(processing_dir, filter_repo))

    tbd_entries: list[tuple[pathlib.Path, str, str]] = []
    if args.type in ("all", "tbd"):
        tbd_dir = private_notes / "tbd" / FEEDBACK_STATE_INBOX
        for path, target_repo, text in _iter_inbox_entries(tbd_dir, filter_repo):
            answered = _is_tbd_answered(text)
            if args.status == "answered" and not answered:
                continue
            if args.status == "unanswered" and answered:
                continue
            tbd_entries.append((path, target_repo, text))

    if args.count:
        print(len(feedback_entries) + len(tbd_entries))
        return

    if feedback_entries:
        print("# feedback")
        for path, target_repo, text in feedback_entries:
            print(f"{path.name}\t{target_repo}\t{_body_summary(text)}")

    _render_tbd_entries(tbd_entries)
