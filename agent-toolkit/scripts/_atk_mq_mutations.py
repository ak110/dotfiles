"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

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

from _atk_mq_common import (
    MQ_STATE_ADOPTED,
    MQ_STATE_INBOX,
    MQ_STATE_PROCESSING,
    MQ_STATE_REJECTED,
    MQ_STATES,
    MQ_TYPE_TBD,
    WebInputError,
    _commit_and_push,
    _copy_to_tempfile,
    _dedup_positional_filenames,
    _pull,
    _repo_lock,
    _require_type,
    _stamp_result,
    _subdir,
    _validate_filename,
    _validate_filenames_only,
)
from _atk_mq_list import _has_category
from _atk_mq_repo import _verify_frontmatter_target_repo
from _atk_mq_repo import edit_entry as _edit_entry

_CATEGORY_GATE_THRESHOLD = 3


def transition_entries(
    private_notes: pathlib.Path,
    *,
    action: str,
    filenames: list[str],
    now: datetime.datetime,
    target_repo: str | None = None,
    note: str | None = None,
    commit: str | None = None,
    category: str | None = None,
    lock_timeout: float = -1,
    force: bool = False,
) -> list[str]:
    """平引数でエントリの一括状態遷移又は削除を実行する。

    `action="remove"`かつ`force=False`（既定）の場合、processing状態のファイルが
    対象に含まれるとexit 2で拒否する（`atk mq rm`の既定保護。処理中ファイルの
    意図しない削除を防ぐ。解除するには`force=True`を渡す）。
    """
    if action not in {"start-processing", "adopt", "reject", "remove"}:
        raise WebInputError(f"未知のエントリ操作です: {action}")
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    _validate_filenames_only(filenames, inbox_dir)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        search_dirs = [inbox_dir] if action == "start-processing" else [inbox_dir, processing_dir]
        for filename in filenames:
            _verify_frontmatter_target_repo(filename, search_dirs, target_repo)
        paths = (
            _resolve_feedback_targets(filenames, inbox_dir)
            if action == "start-processing"
            else _resolve_processable_targets(filenames, inbox_dir, processing_dir)
        )
        if category is not None:
            tbd_paths = [path.name for path in paths if _require_type(path, path.read_text(encoding="utf-8")) == MQ_TYPE_TBD]
            if tbd_paths:
                raise WebInputError(f"--categoryはfeedback専用です: {', '.join(tbd_paths)}")
        if action == "remove" and not force:
            protected = [path.name for path in paths if path.parent.name == MQ_STATE_PROCESSING]
            if protected:
                print(
                    "processing状態のファイルは既定で削除を保護します。"
                    f"削除するには--force（Web APIはforce指定）を指定してください: {', '.join(protected)}",
                    file=sys.stderr,
                )
                sys.exit(2)
        destination_name = {
            "start-processing": MQ_STATE_PROCESSING,
            "adopt": MQ_STATE_ADOPTED,
            "reject": MQ_STATE_REJECTED,
        }.get(action)
        if destination_name is None:
            for path in paths:
                path.unlink()
        else:
            destination = _subdir(private_notes, destination_name)
            conflicts = [path.name for path in paths if (destination / path.name).exists()]
            if conflicts:
                print(
                    f"移動先（{destination_name}）に同名エントリが既に存在します: {', '.join(conflicts)}",
                    file=sys.stderr,
                )
                sys.exit(2)
            for path in paths:
                if action in {"adopt", "reject"}:
                    _stamp_result(
                        path,
                        outcome=destination_name,
                        now=now,
                        commit=commit,
                        note=note,
                        category=category if action == "adopt" else None,
                    )
                shutil.move(path, destination / path.name)
        count = len(paths)
        item_word = "entry" if count == 1 else "entries"
        note_suffix = f" (理由: {note})" if action == "remove" and note else ""
        message = {
            "start-processing": f"chore: start processing {count} {item_word}",
            "adopt": f"chore: process {count} {item_word} (adopted)",
            "reject": f"chore: process {count} {item_word} (rejected)",
            "remove": f"chore: remove {count} {item_word}{note_suffix}",
        }[action]
        _commit_and_push(private_notes, message, list(MQ_STATES))
        if action == "adopt" and category is not None:
            adopted_dir = _subdir(private_notes, MQ_STATE_ADOPTED)
            adopted_count = sum(
                1
                for entry_path in adopted_dir.iterdir()
                if entry_path.is_file() and _has_category(entry_path.read_text(encoding="utf-8"), category)
            )
            if adopted_count >= _CATEGORY_GATE_THRESHOLD:
                print(
                    f"カテゴリ「{category}」の採用件数が{adopted_count}件に到達した。"
                    "上位カテゴリでの規範化・仕組み化の検討を必須とする"
                    "（agent-toolkit:agent-standards配下references/feedback-review-common.md"
                    "「同一カテゴリ累積時の規範化ゲート」参照）。",
                    file=sys.stderr,
                )
    return [path.name for path in paths]


def edit_entry_content(
    private_notes: pathlib.Path,
    *,
    state: str,
    filename: str,
    content: str,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    expected_content: str | None = None,
) -> bool:
    """平引数でfeedback本文を更新する。"""
    if state not in {MQ_STATE_INBOX, MQ_STATE_PROCESSING}:
        raise WebInputError("編集可能状態はinbox又はprocessingです")
    directory = private_notes / state
    return _edit_entry(
        private_notes,
        directory=directory,
        filename=filename,
        content=content,
        target_repo=target_repo,
        lock_timeout=lock_timeout,
        expected_content=expected_content,
        commit_message="chore: edit feedback item",
    )


def commit_entries(private_notes: pathlib.Path, *, lock_timeout: float = -1) -> bool:
    """平引数でinbox・processing配下の外部編集差分をcommit・pushする。"""
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        inbox_rel = MQ_STATE_INBOX
        processing_rel = MQ_STATE_PROCESSING
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", inbox_rel, processing_rel],
            cwd=private_notes,
            check=True,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            return False
        _commit_and_push(private_notes, "chore: edit queue items externally", [inbox_rel, processing_rel])
    return True


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
        # `_validate_filename`側で拡張子`.md`の省略を正規形へ補完する。
        inbox_path = _validate_filename(name, inbox_dir)
        processing_path = _validate_filename(inbox_path.name, processing_dir)
        if processing_path.exists():
            resolved.append(processing_path)
        elif inbox_path.exists():
            resolved.append(inbox_path)
        else:
            missing.append(inbox_path.name)
    if missing:
        for name in missing:
            print(f"inbox・processingのいずれにも存在しません: {name}", file=sys.stderr)
        sys.exit(2)
    return resolved


def _cmd_adopt(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """adoptサブコマンド: 採用としてinboxまたはprocessingからadopted/へ移動しcommit・push。

    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    inbox・processingいずれの起点も許容し、両方に同名ファイルがある場合はprocessingを優先する。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "adopt")
    filenames = transition_entries(
        private_notes,
        action="adopt",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
        category=args.category,
    )
    print(f"{len(filenames)}件採用処理: {', '.join(filenames)}")


def _cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """rejectサブコマンド: 不採用としてinboxまたはprocessingからrejected/へ移動しcommit・push。

    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    inbox・processingいずれの起点も許容し、両方に同名ファイルがある場合はprocessingを優先する。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "reject")
    filenames = transition_entries(
        private_notes,
        action="reject",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
    )
    print(f"{len(filenames)}件不採用処理: {', '.join(filenames)}")


def _cmd_start_processing(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """start-processingサブコマンド: inboxからprocessing/へ移動しcommit・push。

    後続の`adopt`・`reject`が処理を継続することを前提とし、`## 処理結果`節の追記はしない
    （最終処理結果の記録は`adopt`・`reject`側で行う）。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "start-processing")
    filenames = transition_entries(
        private_notes,
        action="start-processing",
        filenames=args.filenames,
        now=datetime.datetime.now(),
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件処理開始: {', '.join(filenames)}")


def _cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rmサブコマンド: inbox・processingいずれかから単純削除しcommit・push。

    processing優先で解決する（`_resolve_processable_targets`と同じ規約）。
    processing状態のファイルは既定で削除を保護し、`--force`指定時のみ削除を許可する。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "rm")
    filenames = transition_entries(
        private_notes,
        action="remove",
        filenames=args.filenames,
        now=datetime.datetime.now(),
        target_repo=args.target_repo,
        force=args.force,
        note=args.note,
    )
    print(f"{len(filenames)}件削除: {', '.join(filenames)}")


def _cmd_edit(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """editサブコマンド: $EDITORで対象ファイルを編集しcommit・push（差分なしなら無動作）。

    無引数時は_pull実行後にinbox配下でファイル名順の最大値（最終追加分）を選択する。
    """
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITORが未設定のため編集できません。", file=sys.stderr)
        sys.exit(1)
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
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
    edited = tmp_path.read_text(encoding="utf-8")
    original = snapshot.decode("utf-8")
    if edited == original:
        tmp_path.unlink(missing_ok=True)
        print("差分なし。")
        return
    try:
        edit_entry_content(
            private_notes,
            state=path.parent.name,
            filename=path.name,
            content=edited,
            target_repo=args.target_repo,
            expected_content=original,
        )
    except RuntimeError:
        print(
            f"編集中に他プロセスが対象を変更しました: {path.name}。"
            f"編集内容は{tmp_path}に残しています。再度atk mq editを実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    tmp_path.unlink(missing_ok=True)
    print(f"編集反映: {path.name}")


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のinbox・processing配下未コミット変更をコミット・push。

    inbox・processing配下に未コミット変更が無い場合は早期return。
    """
    if commit_entries(private_notes):
        print("外部編集分をコミット・pushしました。")
    else:
        print("差分なし。")
