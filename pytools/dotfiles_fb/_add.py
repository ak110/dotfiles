"""addサブコマンド実装。"""

import argparse
import datetime
import pathlib
import sys

from pytools.dotfiles_fb._common import (
    _collect_message_via_editor,
    _commit_and_push,
    _count_feedback,
    _max_existing_seq,
    _pull,
    _subdir,
)
from pytools.dotfiles_fb._formatters import _shorten_home
from pytools.dotfiles_fb._repo import _resolve_repo_id


def _cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """addサブコマンド: メッセージをinboxへ投入してcommit・push。"""
    target_repo = _resolve_repo_id(args.repo_path)
    messages = list(args.messages)
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    _pull(private_notes)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    inbox_dir = _subdir(private_notes, "inbox")
    counter = _max_existing_seq(inbox_dir, timestamp) + 1
    source_line = f"source: {args.source}\n" if args.source else ""
    generated: list[str] = []
    for message in messages:
        filename = f"{timestamp}-{counter:03d}.md"
        content = f"---\ntarget_repo: {target_repo}\n{source_line}---\n\n{message}\n"
        (inbox_dir / filename).write_text(content, encoding="utf-8")
        generated.append(filename)
        counter += 1
    count = len(generated)
    _commit_and_push(
        private_notes,
        f"chore: add {count} feedback {'item' if count == 1 else 'items'}",
        ["feedback"],
    )
    print(f"{count}件投入:")
    for filename in generated:
        print(f"  {_shorten_home(inbox_dir / filename, home)}")
    print(f"inbox: 計{_count_feedback(inbox_dir)}件")
    print("編集する場合:")
    for filename in generated:
        print(f"  dotfiles-fb edit {filename}")
