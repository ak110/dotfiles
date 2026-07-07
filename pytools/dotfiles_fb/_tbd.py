"""tbd-add/tbd-list/tbd-answer/tbd-edit/tbd-adopt/tbd-rmサブコマンド実装。"""

import argparse
import datetime
import os
import pathlib
import subprocess
import sys

from pytools.dotfiles_fb._common import (
    _collect_message_via_editor,
    _commit_and_push,
    _is_tbd_answered,
    _iter_inbox_entries,
    _max_existing_seq,
    _pull,
    _stamp_result,
    _validate_filename,
    _validate_filenames_only,
)
from pytools.dotfiles_fb._formatters import _parse_target_repo, _shorten_home
from pytools.dotfiles_fb._list import _render_tbd_entries
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
    """tbd-listサブコマンド: TBD inboxを1件1行（filename・target_repo・本文冒頭要約）で出力する。

    出力形式は`list --type=tbd`と同一とし、target_repoグループ化・本文全文表示は行わない。
    本文全文表示が必要な場合は`show --all --type=tbd`または`show <filename>`を使う。
    """
    tbd_dir = private_notes / "tbd" / "inbox"
    if not args.skip_pull:
        _pull(private_notes)
    filter_repo: str | None = None
    if args.target_repo is not None:
        filter_repo = _resolve_repo_id(args.target_repo)
    entries: list[tuple[pathlib.Path, str, str]] = []
    for path, target_repo, text in _iter_inbox_entries(tbd_dir, filter_repo):
        answered = _is_tbd_answered(text)
        if args.status == "answered" and not answered:
            continue
        if args.status == "unanswered" and answered:
            continue
        entries.append((path, target_repo, text))
    _render_tbd_entries(entries)


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


def _resolve_tbd_targets(filenames: list[str], tbd_inbox: pathlib.Path) -> list[pathlib.Path]:
    """tbd/inbox配下のファイル名群を検証・解決し、未存在があればexit 2する。"""
    paths = [_validate_filename(f, tbd_inbox) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"tbd/inboxに存在しません: {p.name}", file=sys.stderr)
        sys.exit(2)
    return paths


def _cmd_tbd_adopt(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """tbd-adoptサブコマンド: 回答済みTBDをtbd/inboxからtbd/adopted/へ移動しcommit・push。

    全ファイルの存在を移動前に一括検証し、途中失敗による部分移動を防ぐ。
    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    """
    tbd_inbox = private_notes / "tbd" / "inbox"
    tbd_adopted = private_notes / "tbd" / "adopted"
    _validate_filenames_only(args.filenames, tbd_inbox)
    _pull(private_notes)
    paths = _resolve_tbd_targets(args.filenames, tbd_inbox)
    tbd_adopted.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    rel_paths: list[str] = []
    for src in paths:
        _stamp_result(src, outcome="tbd-adopted", now=now, commit=args.commit, note=args.note)
        dst = tbd_adopted / src.name
        src.rename(dst)
        moved.append(src.name)
        rel_paths.append(str(src.relative_to(private_notes)))
        rel_paths.append(str(dst.relative_to(private_notes)))
    count = len(moved)
    _commit_and_push(
        private_notes,
        f"chore: adopt {count} tbd {'item' if count == 1 else 'items'}",
        rel_paths,
    )
    print(f"{count}件採用: {', '.join(moved)}")


def _cmd_tbd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """tbd-rmサブコマンド: TBDをtbd/inboxから単純削除しcommit・push。

    全ファイルの存在を削除前に一括検証し、途中失敗による部分削除を防ぐ。
    `--note`が指定された場合はcommit messageへ「(理由: <note>)」形式で追記する。
    """
    tbd_inbox = private_notes / "tbd" / "inbox"
    _validate_filenames_only(args.filenames, tbd_inbox)
    _pull(private_notes)
    paths = _resolve_tbd_targets(args.filenames, tbd_inbox)
    for p in paths:
        p.unlink()
    count = len(paths)
    suffix = f" (理由: {args.note})" if args.note else ""
    _commit_and_push(
        private_notes,
        f"chore: remove {count} tbd {'item' if count == 1 else 'items'}{suffix}",
        ["tbd"],
    )
    print(f"{count}件削除: {', '.join(p.name for p in paths)}")
