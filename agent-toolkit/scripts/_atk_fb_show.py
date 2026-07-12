"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_show.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import pathlib
import sys

from _atk_fb_common import (
    FEEDBACK_STATE_ADOPTED,
    FEEDBACK_STATE_INBOX,
    FEEDBACK_STATE_PROCESSING,
    FEEDBACK_STATE_REJECTED,
    _is_tbd_answered,
    _iter_inbox_entries,
    _pull,
    _validate_filename,
)
from _atk_fb_formatters import _parse_target_repo
from _atk_fb_repo import _resolve_repo_id


def _cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """showサブコマンド: `FILENAME`指定時は当該1件、`--all`指定時は全件の本文を表示する。

    `FILENAME`・`--all`のいずれも未指定の場合はエラー終了する（exit 2）。
    `--type`指定時は出力対象種別（feedback・tbd・all）を限定する（既定: all）。
    `FILENAME`指定時は`--type`の値で探索対象を限定する。
    `--type=all`（既定）はfeedback/inbox→feedback/processing→tbd/inboxの順で探索する。
    `--all`指定時のfeedback走査もinbox・processing双方を対象に含める。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--status`指定時は、tbd側エントリのみ回答状況（answered・unanswered）で限定する
    （feedback側には作用しない）。
    `--include-processed`指定時は`FILENAME`指定分岐でfeedback側の探索対象へadopted・rejectedを追加する
    （`--all`には影響しない）。
    """
    if args.filename is None and not args.all:
        print("FILENAMEまたは--allのいずれかを指定してください。", file=sys.stderr)
        sys.exit(2)
    if not args.skip_pull:
        _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)

    if args.filename is not None:
        search_kinds: list[tuple[str, pathlib.Path]] = []
        if args.type in ("all", "feedback"):
            # feedback側はinbox・processing双方を検索対象に含める（`start-processing`後の
            # 途中状態も参照可能とするため）。
            search_kinds.append(("feedback", private_notes / "feedback" / FEEDBACK_STATE_INBOX))
            search_kinds.append(("feedback", private_notes / "feedback" / FEEDBACK_STATE_PROCESSING))
            if args.include_processed:
                search_kinds.append(("feedback", private_notes / "feedback" / FEEDBACK_STATE_ADOPTED))
                search_kinds.append(("feedback", private_notes / "feedback" / FEEDBACK_STATE_REJECTED))
        if args.type in ("all", "tbd"):
            search_kinds.append(("tbd", private_notes / "tbd" / FEEDBACK_STATE_INBOX))
        for kind, base_dir in search_kinds:
            path = _validate_filename(args.filename, base_dir)
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            target_repo = _parse_target_repo(text)
            if filter_repo is not None and target_repo != filter_repo:
                continue
            answered = _is_tbd_answered(text)
            if kind == "tbd" and args.status == "answered" and not answered:
                continue
            if kind == "tbd" and args.status == "unanswered" and answered:
                continue
            label = ""
            if kind == "tbd":
                label = " [answered]" if answered else " [unanswered]"
            print(f"## target_repo: {target_repo}")
            print(f"### {path.name}{label}")
            print(text)
            return
        print(f"inbox/tbdに存在しません: {args.filename}", file=sys.stderr)
        sys.exit(2)

    if args.type in ("all", "feedback"):
        # inbox・processing双方を走査対象にする（`start-processing`後の途中状態も
        # `--all`で確認できるようにするため）。
        entries: dict[str, list[tuple[str, str]]] = {}
        for state_name in (FEEDBACK_STATE_INBOX, FEEDBACK_STATE_PROCESSING):
            state_dir = private_notes / "feedback" / state_name
            for path, target_repo, text in _iter_inbox_entries(state_dir, filter_repo):
                entries.setdefault(target_repo, []).append((path.name, text))
        if entries:
            print("# feedback")
            for repo, items in entries.items():
                print(f"## target_repo: {repo}")
                for name, text in items:
                    print(f"### {name}")
                    print(text)
                    print()

    if args.type in ("all", "tbd"):
        tbd_dir = private_notes / "tbd" / FEEDBACK_STATE_INBOX
        tbd_entries: dict[str, list[tuple[str, str]]] = {}
        for path, target_repo, text in _iter_inbox_entries(tbd_dir, filter_repo):
            answered = _is_tbd_answered(text)
            if args.status == "answered" and not answered:
                continue
            if args.status == "unanswered" and answered:
                continue
            tbd_entries.setdefault(target_repo, []).append((path.name, text))
        if tbd_entries:
            print("# tbd")
            for repo, items in tbd_entries.items():
                print(f"## target_repo: {repo}")
                for name, text in items:
                    label = " [answered]" if _is_tbd_answered(text) else " [unanswered]"
                    print(f"### {name}{label}")
                    print(text)
                    print()
