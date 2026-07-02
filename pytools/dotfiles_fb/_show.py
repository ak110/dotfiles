"""showサブコマンド実装。"""

import argparse
import pathlib
import sys

from pytools.dotfiles_fb._common import _is_tbd_answered, _iter_inbox_entries, _pull, _validate_filename
from pytools.dotfiles_fb._formatters import _parse_target_repo
from pytools.dotfiles_fb._repo import _resolve_repo_id


def _cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """showサブコマンド: `FILENAME`指定時は当該1件、`--all`指定時は全件の本文を表示する。

    `FILENAME`・`--all`のいずれも未指定の場合はエラー終了する（exit 2）。
    `--type`指定時は出力対象種別（feedback・tbd・all）を絞り込む（既定: all）。
    `FILENAME`指定時は`--type`の値で探索対象inboxを限定する。
    `--type=all`（既定）はfeedback/inbox→tbd/inboxの順で探索する。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    `--status`指定時は、tbd側エントリのみ回答状況（answered・unanswered）で絞り込む
    （feedback側には作用しない）。
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
            search_kinds.append(("feedback", private_notes / "feedback" / "inbox"))
        if args.type in ("all", "tbd"):
            search_kinds.append(("tbd", private_notes / "tbd" / "inbox"))
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
        inbox_dir = private_notes / "feedback" / "inbox"
        entries: dict[str, list[tuple[str, str]]] = {}
        for path, target_repo, text in _iter_inbox_entries(inbox_dir, filter_repo):
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
        tbd_dir = private_notes / "tbd" / "inbox"
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
