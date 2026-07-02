"""listサブコマンド実装。"""

import argparse
import pathlib

from pytools.dotfiles_fb._common import _is_tbd_answered, _iter_inbox_entries, _pull
from pytools.dotfiles_fb._formatters import _body_summary, _tbd_body_summary
from pytools.dotfiles_fb._repo import _resolve_repo_id


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
    """listサブコマンド: feedback/tbd inbox全件を1件1行（filename・target_repo・本文冒頭要約）で出力する。

    `--type`指定で出力対象種別（feedback・tbd・all）を絞り込む（既定: all）。
    `--status`指定でtbd側のみ回答状況を絞り込む（既定: all、feedback側には作用しない）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--type=all`（既定）指定時、該当部エントリが1件以上ある場合のみ種別ヘッダを出力する。
    """
    if not args.skip_pull:
        _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)

    feedback_entries: list[tuple[pathlib.Path, str, str]] = []
    if args.type in ("all", "feedback"):
        inbox_dir = private_notes / "feedback" / "inbox"
        feedback_entries = list(_iter_inbox_entries(inbox_dir, filter_repo))

    tbd_entries: list[tuple[pathlib.Path, str, str]] = []
    if args.type in ("all", "tbd"):
        tbd_dir = private_notes / "tbd" / "inbox"
        for path, target_repo, text in _iter_inbox_entries(tbd_dir, filter_repo):
            answered = _is_tbd_answered(text)
            if args.status == "answered" and not answered:
                continue
            if args.status == "unanswered" and answered:
                continue
            tbd_entries.append((path, target_repo, text))

    if feedback_entries:
        print("# feedback")
        for path, target_repo, text in feedback_entries:
            print(f"{path.name}\t{target_repo}\t{_body_summary(text)}")

    _render_tbd_entries(tbd_entries)
