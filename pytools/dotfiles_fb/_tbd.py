"""tbd-add/tbd-list/tbd-answer/tbd-editサブコマンド実装。"""

import argparse
import datetime
import os
import pathlib
import subprocess
import sys

from pytools.dotfiles_fb._common import (
    _collect_message_via_editor,
    _commit_and_push,
    _iter_inbox_entries,
    _max_existing_seq,
    _pull,
    _validate_filename,
)
from pytools.dotfiles_fb._formatters import _parse_target_repo, _shorten_home
from pytools.dotfiles_fb._repo import _resolve_repo_id


def _tbd_subdir(private_notes: pathlib.Path) -> pathlib.Path:
    """tbd/inbox配下のディレクトリパスを返す。必要時に作成する。"""
    path = private_notes / "tbd" / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tbd_filename_completer(prefix: str, **_: object) -> list[str]:
    """argcomplete用のTBDファイル名補完候補生成。

    `~/private-notes/tbd/inbox/`配下の`*.md`ファイル名をprefix一致で返す。
    """
    tbd_dir = pathlib.Path.home() / "private-notes" / "tbd" / "inbox"
    if not tbd_dir.exists():
        return []
    return sorted(p.name for p in tbd_dir.iterdir() if p.suffix == ".md" and p.name.startswith(prefix))


def _is_tbd_answered(text: str) -> bool:
    """TBD本文の`## 回答`節にHTMLコメント以外の非空内容があれば真。"""
    marker = "\n## 回答\n"
    idx = text.find(marker)
    if idx < 0:
        return False
    body = text[idx + len(marker) :]
    next_h2 = body.find("\n## ")
    if next_h2 >= 0:
        body = body[:next_h2]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        return True
    return False


def _cmd_tbd_add(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """tbd-addサブコマンド: TBDをtbd/inboxへ投入してcommit・push。"""
    target_repo = _resolve_repo_id(args.repo_path)
    messages = list(args.messages)
    if not messages:
        message = _collect_message_via_editor()
        if message is None:
            sys.exit(1)
        messages = [message]
    if args.question_type == "choice" and not args.choices:
        print("--question-type=choice 時は --choices を指定してください。", file=sys.stderr)
        sys.exit(2)
    _pull(private_notes)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    tbd_dir = _tbd_subdir(private_notes)
    counter = _max_existing_seq(tbd_dir, timestamp) + 1
    fm_extra = ""
    if args.scope:
        fm_extra += f"scope: {args.scope}\n"
    fm_extra += f"question_type: {args.question_type}\n"
    if args.question_type == "choice":
        fm_extra += f"choices: {args.choices}\n"
    generated: list[str] = []
    for message in messages:
        filename = f"{timestamp}-{counter:03d}.md"
        content = (
            f"---\ntarget_repo: {target_repo}\n{fm_extra}---\n\n"
            f"## 質問\n\n{message}\n\n## 回答\n\n"
            "<!-- ユーザーはこの行以降に回答を追記する -->\n"
        )
        (tbd_dir / filename).write_text(content, encoding="utf-8")
        generated.append(filename)
        counter += 1
    count = len(generated)
    _commit_and_push(
        private_notes,
        f"chore: add {count} tbd {'item' if count == 1 else 'items'}",
        ["tbd"],
    )
    print(f"{count}件投入:")
    for filename in generated:
        print(f"  {_shorten_home(tbd_dir / filename, home)}")


def _cmd_tbd_list(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """tbd-listサブコマンド: TBDをtarget_repoごとに出力。"""
    tbd_dir = private_notes / "tbd" / "inbox"
    _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)
    entries: dict[str, list[tuple[str, str, bool]]] = {}
    for path, target_repo, text in _iter_inbox_entries(tbd_dir, filter_repo):
        answered = _is_tbd_answered(text)
        if args.status == "answered" and not answered:
            continue
        if args.status == "unanswered" and answered:
            continue
        entries.setdefault(target_repo, []).append((path.name, text, answered))
    for repo, items in entries.items():
        print(f"## target_repo: {repo}")
        for name, text, answered in items:
            label = "answered" if answered else "unanswered"
            print(f"### {name} [{label}]")
            print(text)
            print()


def _cmd_tbd_answer(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """tbd-answerサブコマンド: 未回答TBDを1件ずつ画面表示し$EDITORで回答する。"""
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため回答経路を利用できません。", file=sys.stderr)
        sys.exit(1)
    tbd_dir = private_notes / "tbd" / "inbox"
    _pull(private_notes)
    if not tbd_dir.exists():
        print("未回答のTBDはありません。")
        return
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)
    targets: list[pathlib.Path] = []
    for path in sorted(tbd_dir.iterdir()):
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        if filter_repo is not None and _parse_target_repo(text) != filter_repo:
            continue
        if _is_tbd_answered(text):
            continue
        targets.append(path)
    if not targets:
        print("未回答のTBDはありません。")
        return
    edited: list[str] = []
    for path in targets:
        print(f"--- {path.name} ---")
        print(path.read_text(encoding="utf-8"))
        before = path.read_bytes()
        result = subprocess.run([editor, str(path)], check=False)
        if result.returncode != 0:
            print(
                f"エディターが終了コード{result.returncode}で終了しました。中断します。",
                file=sys.stderr,
            )
            break
        after = path.read_bytes()
        if before != after:
            edited.append(path.name)
    if not edited:
        print("差分なし。")
        return
    count = len(edited)
    _commit_and_push(
        private_notes,
        f"chore: answer {count} tbd {'item' if count == 1 else 'items'}",
        ["tbd"],
    )
    print(f"{count}件回答反映: {', '.join(edited)}")


def _cmd_tbd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """tbd-editサブコマンド: $EDITORでTBDを編集してcommit・push。"""
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため編集できません。", file=sys.stderr)
        sys.exit(1)
    tbd_dir = private_notes / "tbd" / "inbox"
    path = _validate_filename(args.filename, tbd_dir)
    _pull(private_notes)
    if not path.exists():
        print(f"tbd/inboxに存在しません: {path.name}", file=sys.stderr)
        sys.exit(2)
    before = path.read_bytes()
    subprocess.run([editor, str(path)], check=True)
    after = path.read_bytes()
    if before == after:
        print("差分なし。")
        return
    rel = str(path.relative_to(private_notes))
    _commit_and_push(private_notes, "chore: edit tbd item", [rel])
    print(f"編集反映: {path.name}")
