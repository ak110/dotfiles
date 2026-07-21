"""agent-toolkitプラグイン配下の`atk fb`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_mutations.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import os
import pathlib
import shutil
import subprocess
import sys

from _atk_fb_common import (
    FEEDBACK_STATE_ADOPTED,
    FEEDBACK_STATE_INBOX,
    FEEDBACK_STATE_PROCESSING,
    FEEDBACK_STATE_REJECTED,
    _commit_and_push,
    _copy_to_tempfile,
    _pull,
    _repo_lock,
    _stamp_result,
    _subdir,
    _validate_filename,
    _validate_filenames_only,
)
from _atk_fb_list import _has_category
from _atk_fb_repo import _verify_frontmatter_target_repo

_CATEGORY_GATE_THRESHOLD = 3


def _resolve_feedback_targets(filenames: list[str], feedback_dir: pathlib.Path) -> list[pathlib.Path]:
    """inbox配下のファイル名群を検証・解決し、未存在があればexit 2する。"""
    paths = [_validate_filename(f, feedback_dir) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"inboxに存在しません: {p.name}", file=sys.stderr)
        sys.exit(2)
    return paths


def _resolve_processable_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
) -> list[pathlib.Path]:
    """inboxまたはprocessing配下のファイル名群を検証・解決し、未存在があればexit 2する。

    同一ファイルがinbox・processingの双方に存在する場合はprocessingを優先する
    （`start-processing`後の中断復帰時にprocessing側が最新状態のため）。
    """
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        # 検証はinbox基準ディレクトリで行うが、実体はいずれか片方の状態フォルダに存在する。
        inbox_path = _validate_filename(name, inbox_dir)
        processing_path = processing_dir / name
        if processing_path.exists():
            resolved.append(processing_path)
        elif inbox_path.exists():
            resolved.append(inbox_path)
        else:
            missing.append(name)
    if missing:
        for name in missing:
            print(f"inbox・processingのいずれにも存在しません: {name}", file=sys.stderr)
        sys.exit(2)
    return resolved


def _cmd_adopt(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """adoptサブコマンド: 採用としてinboxまたはprocessingからadopted/へ移動しcommit・push。

    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    inbox・processingいずれの起点も許容し、両方に同名ファイルがある場合はprocessingを優先する。
    """
    inbox_dir = private_notes / "feedback" / FEEDBACK_STATE_INBOX
    processing_dir = _subdir(private_notes, FEEDBACK_STATE_PROCESSING)
    _validate_filenames_only(args.filenames, inbox_dir)
    with _repo_lock(private_notes):
        _pull(private_notes)
        for filename in args.filenames:
            _verify_frontmatter_target_repo(filename, [inbox_dir, processing_dir], args.target_repo)
        paths = _resolve_processable_targets(args.filenames, inbox_dir, processing_dir)
        adopted_dir = _subdir(private_notes, FEEDBACK_STATE_ADOPTED)
        for p in paths:
            _stamp_result(
                p,
                outcome=FEEDBACK_STATE_ADOPTED,
                now=now,
                commit=args.commit,
                note=args.note,
                category=args.category,
            )
            shutil.move(p, adopted_dir / p.name)
        if args.category is not None:
            adopted_count = sum(
                1
                for entry_path in adopted_dir.iterdir()
                if entry_path.is_file() and _has_category(entry_path.read_text(encoding="utf-8"), args.category)
            )
            if adopted_count >= _CATEGORY_GATE_THRESHOLD:
                print(
                    f"カテゴリ「{args.category}」の採用件数が{adopted_count}件に到達した。"
                    "上位カテゴリでの規範化・仕組み化の検討を必須とする"
                    "（agent-toolkit:agent-standards配下references/feedback-review-common.md"
                    "「同一カテゴリ累積時の規範化ゲート」参照）。",
                    file=sys.stderr,
                )
        count = len(paths)
        _commit_and_push(
            private_notes,
            f"chore: process {count} feedback {'item' if count == 1 else 'items'} (adopted)",
            ["feedback"],
        )
    print(f"{count}件採用処理: {', '.join(p.name for p in paths)}")


def _cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """rejectサブコマンド: 不採用としてinboxまたはprocessingからrejected/へ移動しcommit・push。

    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    inbox・processingいずれの起点も許容し、両方に同名ファイルがある場合はprocessingを優先する。
    """
    inbox_dir = private_notes / "feedback" / FEEDBACK_STATE_INBOX
    processing_dir = _subdir(private_notes, FEEDBACK_STATE_PROCESSING)
    _validate_filenames_only(args.filenames, inbox_dir)
    with _repo_lock(private_notes):
        _pull(private_notes)
        for filename in args.filenames:
            _verify_frontmatter_target_repo(filename, [inbox_dir, processing_dir], args.target_repo)
        paths = _resolve_processable_targets(args.filenames, inbox_dir, processing_dir)
        rejected_dir = _subdir(private_notes, FEEDBACK_STATE_REJECTED)
        for p in paths:
            _stamp_result(p, outcome=FEEDBACK_STATE_REJECTED, now=now, commit=args.commit, note=args.note)
            shutil.move(p, rejected_dir / p.name)
        count = len(paths)
        _commit_and_push(
            private_notes,
            f"chore: process {count} feedback {'item' if count == 1 else 'items'} (rejected)",
            ["feedback"],
        )
    print(f"{count}件不採用処理: {', '.join(p.name for p in paths)}")


def _cmd_start_processing(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """start-processingサブコマンド: inboxからprocessing/へ移動しcommit・push。

    後続の`adopt`・`reject`が処理を継続することを前提とし、`## 処理結果`節の追記はしない
    （最終処理結果の記録は`adopt`・`reject`側で行う）。
    """
    inbox_dir = private_notes / "feedback" / FEEDBACK_STATE_INBOX
    _validate_filenames_only(args.filenames, inbox_dir)
    with _repo_lock(private_notes):
        _pull(private_notes)
        for filename in args.filenames:
            _verify_frontmatter_target_repo(filename, [inbox_dir], args.target_repo)
        paths = _resolve_feedback_targets(args.filenames, inbox_dir)
        processing_dir = _subdir(private_notes, FEEDBACK_STATE_PROCESSING)
        for p in paths:
            shutil.move(p, processing_dir / p.name)
        count = len(paths)
        _commit_and_push(
            private_notes,
            f"chore: start processing {count} feedback {'item' if count == 1 else 'items'}",
            ["feedback"],
        )
    print(f"{count}件処理開始: {', '.join(p.name for p in paths)}")


def _cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rmサブコマンド: inbox・processingいずれかから単純削除しcommit・push。

    processing優先で解決する（`_resolve_processable_targets`と同じ規約）。
    """
    inbox_dir = private_notes / "feedback" / FEEDBACK_STATE_INBOX
    processing_dir = _subdir(private_notes, FEEDBACK_STATE_PROCESSING)
    _validate_filenames_only(args.filenames, inbox_dir)
    with _repo_lock(private_notes):
        _pull(private_notes)
        for filename in args.filenames:
            _verify_frontmatter_target_repo(filename, [inbox_dir, processing_dir], args.target_repo)
        paths = _resolve_processable_targets(args.filenames, inbox_dir, processing_dir)
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
    inbox_dir = private_notes / "feedback" / FEEDBACK_STATE_INBOX
    processing_dir = _subdir(private_notes, FEEDBACK_STATE_PROCESSING)
    with _repo_lock(private_notes):
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
            _validate_filenames_only([args.filename], inbox_dir)
            _pull(private_notes)
            paths = _resolve_processable_targets([args.filename], inbox_dir, processing_dir)
            path = paths[0]
        _verify_frontmatter_target_repo(path.name, [inbox_dir, processing_dir], args.target_repo)
        snapshot = path.read_bytes()
    tmp_path = _copy_to_tempfile(snapshot)
    subprocess.run([editor, str(tmp_path)], check=True)
    edited = tmp_path.read_bytes()
    if edited == snapshot:
        tmp_path.unlink(missing_ok=True)
        print("差分なし。")
        return
    with _repo_lock(private_notes):
        _pull(private_notes)
        if not path.exists() or path.read_bytes() != snapshot:
            print(
                f"編集中に他プロセスが対象を変更しました: {path.name}。"
                f"編集内容は{tmp_path}に残しています。再度atk fb editを実行してください。",
                file=sys.stderr,
            )
            sys.exit(1)
        path.write_bytes(edited)
        rel = str(path.relative_to(private_notes))
        _commit_and_push(private_notes, "chore: edit feedback item", [rel])
    tmp_path.unlink(missing_ok=True)
    print(f"編集反映: {path.name}")


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のinbox配下未コミット変更をコミット・push。

    inbox配下に未コミット変更が無い場合は早期return。
    """
    with _repo_lock(private_notes):
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
