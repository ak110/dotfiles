"""adopt/reject/rm/edit/commitサブコマンド実装。"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

from pytools.dotfiles_fb._common import _commit_and_push, _pull, _subdir, _validate_filename


def _validate_filenames_only(filenames: list[str], base_dir: pathlib.Path) -> None:
    """ファイル名群の検証のみ行う（pull前の早期拒否用）。"""
    for f in filenames:
        _validate_filename(f, base_dir)


def _resolve_feedback_targets(filenames: list[str], feedback_dir: pathlib.Path) -> list[pathlib.Path]:
    """inbox配下のファイル名群を検証・解決し、未存在があればexit 2する。"""
    paths = [_validate_filename(f, feedback_dir) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"inboxに存在しません: {p.name}", file=sys.stderr)
        sys.exit(2)
    return paths


def _cmd_adopt(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """adoptサブコマンド: 採用としてinboxからadopted/へ移動しcommit・push。"""
    inbox_dir = private_notes / "feedback" / "inbox"
    _validate_filenames_only(args.filenames, inbox_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, inbox_dir)
    adopted_dir = _subdir(private_notes, "adopted")
    for p in paths:
        shutil.move(p, adopted_dir / p.name)
    count = len(paths)
    _commit_and_push(
        private_notes,
        f"chore: process {count} feedback {'item' if count == 1 else 'items'} (adopted)",
        ["feedback"],
    )
    print(f"{count}件採用処理: {', '.join(p.name for p in paths)}")


def _cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rejectサブコマンド: 不採用としてinboxからrejected/へ移動しcommit・push。"""
    inbox_dir = private_notes / "feedback" / "inbox"
    _validate_filenames_only(args.filenames, inbox_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, inbox_dir)
    rejected_dir = _subdir(private_notes, "rejected")
    for p in paths:
        shutil.move(p, rejected_dir / p.name)
    count = len(paths)
    _commit_and_push(
        private_notes,
        f"chore: process {count} feedback {'item' if count == 1 else 'items'} (rejected)",
        ["feedback"],
    )
    print(f"{count}件不採用処理: {', '.join(p.name for p in paths)}")


def _cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rmサブコマンド: inboxから単純削除しcommit・push。"""
    inbox_dir = private_notes / "feedback" / "inbox"
    _validate_filenames_only(args.filenames, inbox_dir)
    _pull(private_notes)
    paths = _resolve_feedback_targets(args.filenames, inbox_dir)
    for p in paths:
        p.unlink()
    count = len(paths)
    _commit_and_push(
        private_notes,
        f"chore: remove {count} feedback {'item' if count == 1 else 'items'}",
        ["feedback"],
    )
    print(f"{count}件削除: {', '.join(p.name for p in paths)}")


def _cmd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """editサブコマンド: $EDITORで対象ファイルを編集しcommit・push（差分なしなら無動作）。

    無引数時は_pull実行後にinbox配下でファイル名順の最大値（最終追加分）を選択する。
    """
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため編集できません。", file=sys.stderr)
        sys.exit(1)
    inbox_dir = private_notes / "feedback" / "inbox"
    if args.filename is None:
        _pull(private_notes)
        candidates = sorted(
            (p for p in inbox_dir.iterdir() if p.suffix == ".md" and p.is_file()),
            key=lambda p: p.name,
        )
        if not candidates:
            print("inboxが空のため編集対象がありません。", file=sys.stderr)
            sys.exit(2)
        path = candidates[-1]
    else:
        path = _validate_filename(args.filename, inbox_dir)
        _pull(private_notes)
    if not path.exists():
        print(f"inboxに存在しません: {path.name}", file=sys.stderr)
        sys.exit(2)
    before = path.read_bytes()
    subprocess.run([editor, str(path)], check=True)
    after = path.read_bytes()
    if before == after:
        print("差分なし。")
        return
    rel = str(path.relative_to(private_notes))
    _commit_and_push(private_notes, "chore: edit feedback item", [rel])
    print(f"編集反映: {path.name}")


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のinbox配下未コミット変更をコミット・push。

    inbox配下に未コミット変更が無い場合は早期return。
    """
    _pull(private_notes)
    inbox_rel = "feedback/inbox"
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", inbox_rel],
        cwd=private_notes,
        check=True,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        print("差分なし。")
        return
    _commit_and_push(private_notes, "chore: edit feedback items externally", [inbox_rel])
    print("外部編集分をコミット・pushしました。")
