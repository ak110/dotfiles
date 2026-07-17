"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_add.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import pathlib
import subprocess
import sys

from _atk_fb_common import (
    _collect_message_via_editor,
    _commit_and_push,
    _count_feedback,
    _max_existing_seq,
    _pull,
    _reject_bare_repo_path_override,
    _resolve_repo_path_override,
    _subdir,
)
from _atk_fb_formatters import _shorten_home
from _atk_fb_repo import _resolve_repo_id


def _parse_leading_frontmatter(message: str) -> tuple[dict[str, str], str]:
    """メッセージ先頭のYAML frontmatterを解析してキー値と残り本文を返す。

    先頭が3ハイフンで始まらない場合は空dictと元メッセージを返す。
    frontmatter範囲は先頭区切りから次の区切りまで。
    範囲内に「キー: 値」形式でない行が含まれる場合は本文中の水平線との衝突と判定し、
    frontmatterと解釈せず空dictと元メッセージを返す（サイレントな本文欠落を予防する）。
    """
    lines = message.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, message
    body_start = None
    parsed: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        if ":" not in line:
            return {}, message
        key, _, value = line.partition(":")
        parsed[key.strip()] = value.strip()
    if body_start is None:
        return {}, message
    body = "\n".join(lines[body_start:]).lstrip("\n")
    return parsed, body


def _cmd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """addサブコマンド: メッセージをinboxへ投入してcommit・push。

    対象リポジトリは常にカレントディレクトリから解決する。ただし`fb add`直後のトークンが実在
    ディレクトリの場合は旧REPO_PATH位置引数形式の呼び出しとみなし、`atk.py`側の事前抽出で
    当該引数をREPO_PATHとして扱う（互換維持、抽出結果は`args.repo_path_override`で受け取る）。
    各メッセージ先頭がYAML frontmatter形式の場合は`target_repo`・`source`をCLIオプションより優先する。
    エディター経由の本文確定後に`_pull`を実行する順序とし、
    エディター起動前のブロッキング待ち（他端末の投入分を取り込むgit pull）を無くしてUXを改善する。
    `_pull`失敗時はエディターで確定済みの本文をstderrへ再表示してから終了し、入力内容の消失を防ぐ。
    """
    messages, repo_path_override = _resolve_repo_path_override(args.messages, args.repo_path_override)
    _reject_bare_repo_path_override(repo_path_override, messages, 'atk fb add "フィードバック本文"')
    target_repo = _resolve_repo_id(repo_path_override)
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    try:
        _pull(private_notes)
    except subprocess.CalledProcessError:
        print("git pullに失敗しました。確定済みの本文が消失しないよう以下に再表示します。", file=sys.stderr)
        for message in messages:
            print("---", file=sys.stderr)
            print(message, file=sys.stderr)
        sys.exit(1)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    inbox_dir = _subdir(private_notes, "inbox")
    counter = _max_existing_seq(inbox_dir, timestamp) + 1
    generated: list[str] = []
    for message in messages:
        fm, body = _parse_leading_frontmatter(message)
        item_target_repo = fm.get("target_repo", target_repo)
        item_source = fm.get("source", args.source)
        item_source_line = f"source: {item_source}\n" if item_source else ""
        filename = f"{timestamp}-{counter:03d}.md"
        content = f"---\ntarget_repo: {item_target_repo}\n{item_source_line}---\n\n{body}\n"
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
        print(f"  atk fb edit {filename}")
