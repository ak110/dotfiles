"""listサブコマンド実装。"""

import argparse
import pathlib

from pytools.dotfiles_fb._common import _iter_inbox_entries, _pull
from pytools.dotfiles_fb._formatters import _body_summary
from pytools.dotfiles_fb._repo import _resolve_repo_id


def _cmd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """listサブコマンド: inbox全件を1件1行（filename・target_repo・本文冒頭要約）で出力する。

    `--target-repo`指定時は、正規化リモートURLへ変換した値とfrontmatterの`target_repo`が
    完全一致するエントリのみを出力する。
    """
    inbox_dir = private_notes / "feedback" / "inbox"
    _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)
    for path, target_repo, text in _iter_inbox_entries(inbox_dir, filter_repo):
        print(f"{path.name}\t{target_repo}\t{_body_summary(text)}")
