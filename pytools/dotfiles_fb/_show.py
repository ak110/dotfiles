"""showサブコマンド実装。"""

import argparse
import pathlib
import sys

from pytools.dotfiles_fb._common import _iter_inbox_entries, _pull, _validate_filename
from pytools.dotfiles_fb._formatters import _parse_target_repo
from pytools.dotfiles_fb._repo import _resolve_repo_id


def _cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """showサブコマンド: `FILENAME`指定時は当該1件、`--all`指定時は全件の本文を表示する。

    `FILENAME`・`--all`のいずれも未指定の場合はエラー終了する（exit 2）。
    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    """
    inbox_dir = private_notes / "feedback" / "inbox"
    if args.filename is None and not args.all:
        print("FILENAMEまたは--allのいずれかを指定してください。", file=sys.stderr)
        sys.exit(2)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)

    if args.filename is not None:
        path = _validate_filename(args.filename, inbox_dir)
        _pull(private_notes)
        if not path.exists():
            print(f"inboxに存在しません: {path.name}", file=sys.stderr)
            sys.exit(2)
        text = path.read_text(encoding="utf-8")
        target_repo = _parse_target_repo(text)
        if filter_repo is not None and target_repo != filter_repo:
            return
        print(f"## target_repo: {target_repo}")
        print(f"### {path.name}")
        print(text)
        return

    _pull(private_notes)
    entries: dict[str, list[tuple[str, str]]] = {}
    for path, target_repo, text in _iter_inbox_entries(inbox_dir, filter_repo):
        entries.setdefault(target_repo, []).append((path.name, text))
    for repo, items in entries.items():
        print(f"## target_repo: {repo}")
        for name, text in items:
            print(f"### {name}")
            print(text)
            print()
