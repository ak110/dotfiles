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
import tempfile

import _atk_mq_add as _add
import _atk_mq_frontmatter as _frontmatter
import _atk_mq_remove_all as _remove_all
import _atk_mq_tbd as _tbd
from _atk_mq_common import (
    MQ_STATE_ADOPTED,
    MQ_STATE_INBOX,
    MQ_STATE_PROCESSING,
    MQ_STATE_REJECTED,
    MQ_STATES,
    MQ_TYPE_FEEDBACK,
    MQ_TYPE_TBD,
    WebInputError,
    _commit_and_push,
    _copy_to_tempfile,
    _dedup_positional_filenames,
    _pull,
    _push_pending_commits,
    _repo_lock,
    _require_type,
    _stamp_result,
    _subdir,
    _validate_filename,
    _validate_filenames_only,
)
from _atk_mq_list import _has_category
from _atk_mq_repo import _resolve_repo_id, _verify_frontmatter_target_repo
from _atk_mq_repo import edit_entry as _edit_entry

_CATEGORY_GATE_THRESHOLD = 3
_RESERVED_FRONTMATTER_KEYS_FOR_EDITING = (
    "target_commit",
    "depends_on",
    "repair_target",
    "repair_kind",
    "plan_file",
)


def _validate_no_reserved_frontmatter_modification(original: str, updated: str) -> None:
    """frontmatterの予約キーが不正に追加・変更・削除されていないかを検証する。

    利用者が$EDITORまたはWeb APIで内部管理用frontmatterを書き換えることを防ぐ。
    """
    original_parsed = _frontmatter.parse_frontmatter(original)
    updated_parsed = _frontmatter.parse_frontmatter(updated)

    if original_parsed is None and updated_parsed is None:
        return
    if original_parsed is None or updated_parsed is None:
        raise WebInputError("frontmatterの解析に失敗しました")

    original_data, _ = original_parsed
    updated_data, _ = updated_parsed
    target_repo_changed = original_data.get("target_repo") != updated_data.get("target_repo")

    for key in _RESERVED_FRONTMATTER_KEYS_FOR_EDITING:
        original_has_key = key in original_data
        updated_has_key = key in updated_data

        # 予約キーの追加を検出（キー不在 → キー有無を区別）
        if not original_has_key and updated_has_key:
            raise WebInputError(f"予約キー`{key}`の追加は許可されていません")

        # 対象リポジトリ変更時の分類失効はシステムが行う正当な削除である。
        if key == "target_commit" and original_has_key and not updated_has_key and target_repo_changed:
            continue

        # 予約キーの削除を検出
        if original_has_key and not updated_has_key:
            raise WebInputError(f"予約キー`{key}`の削除は許可されていません")

        # 予約キーの変更を検出
        if original_has_key and updated_has_key and original_data[key] != updated_data[key]:
            raise WebInputError(f"予約キー`{key}`の変更は許可されていません")


def _invalidate_repo_bound_metadata(original: str, updated: str) -> str:
    """target_repo変更時に旧リポジトリへ結び付くメタデータを削除する。"""
    original_parsed = _frontmatter.parse_frontmatter(original)
    updated_parsed = _frontmatter.parse_frontmatter(updated)
    if original_parsed is None or updated_parsed is None:
        return updated
    original_data, _ = original_parsed
    updated_data, updated_body = updated_parsed
    if original_data.get("target_repo") == updated_data.get("target_repo"):
        return updated
    updated_data.pop("target_commit", None)
    return _frontmatter.serialize_frontmatter(updated_data, updated_body)


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
    if action not in {"start-processing", "return-to-inbox", "adopt", "reject", "remove"}:
        raise WebInputError(f"未知のエントリ操作です: {action}")
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    _validate_filenames_only(filenames, inbox_dir)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        search_dirs = (
            [inbox_dir]
            if action == "start-processing"
            else [processing_dir]
            if action == "return-to-inbox"
            else [inbox_dir, processing_dir]
        )
        for filename in filenames:
            _verify_frontmatter_target_repo(filename, search_dirs, target_repo)
        paths = (
            _resolve_feedback_targets(filenames, inbox_dir)
            if action == "start-processing"
            else _resolve_feedback_targets(filenames, processing_dir)
            if action == "return-to-inbox"
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
            "return-to-inbox": MQ_STATE_INBOX,
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
            "return-to-inbox": f"chore: return {count} {item_word} to inbox",
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
    """平引数でfeedback本文を更新する。

    保存前に新旧frontmatterを比較し、内部管理用予約キーの追加・変更・削除を禁止する。
    """
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
        content_validator=_validate_no_reserved_frontmatter_modification,
        content_transformer=_invalidate_repo_bound_metadata,
    )


def _build_noninteractive_edit_content(path: pathlib.Path, original: str, message: str) -> str:
    """MESSAGEを既存メタデータへ重ね、種別別の保存内容を返す。"""
    parsed = _frontmatter.parse_frontmatter(original)
    if parsed is None:
        raise WebInputError(f"frontmatterが破損しているため編集できません: {path.name}")
    stored_data, stored_body = parsed
    entry_type = _require_type(path, original)
    assert entry_type is not None
    message_frontmatter, message_body = _add.parse_entry_message(message, entry_type=entry_type)
    normalized_message_body = message_body.strip("\n")

    requested_type = message_frontmatter.get("type")
    if requested_type is not None and requested_type != entry_type:
        print(
            f"typeを変更することはできません（現在値: {entry_type}）: {path.name}",
            file=sys.stderr,
        )
        sys.exit(2)
    if entry_type != MQ_TYPE_TBD:
        tbd_only_keys = sorted({"scope", "question_type", "choices"} & message_frontmatter.keys())
        if tbd_only_keys:
            raise WebInputError(f"feedbackでは指定できないメタデータです: {', '.join(tbd_only_keys)}")

    updates = dict(message_frontmatter)
    if "target_repo" in updates:
        raw_target_repo = updates["target_repo"]
        if not isinstance(raw_target_repo, str):
            raise WebInputError("target_repoは文字列で指定してください")
        updates["target_repo"] = _resolve_repo_id(raw_target_repo)
    if "depends_on" in updates:
        raise WebInputError("depends_onは予約キーのため atk mq edit では指定できません")
    if "target_commit" in updates:
        raise WebInputError("target_commitは予約キーのため atk mq edit では指定できません")
    if "repair_target" in updates:
        raise WebInputError("repair_targetは予約キーのため atk mq edit では指定できません")
    if "repair_kind" in updates:
        raise WebInputError("repair_kindは予約キーのため atk mq edit では指定できません")
    if "plan_file" in updates:
        raise WebInputError("plan_fileは予約キーのため atk mq edit では指定できません")
    updated_data = {**stored_data, **updates}
    target_repo_changed = "target_repo" in updates and stored_data.get("target_repo") != updates["target_repo"]
    if target_repo_changed:
        updated_data.pop("target_commit", None)

    if entry_type != MQ_TYPE_TBD:
        return _frontmatter.serialize_frontmatter(updated_data, "\n" + normalized_message_body.rstrip() + "\n")

    if not normalized_message_body.strip():
        raise WebInputError("TBDの質問本文は空にできません")
    question_type = updated_data.get("question_type")
    if question_type not in {"choice", "yes-no", "free-form"}:
        raise WebInputError("question_typeが不正です")
    if question_type == "choice" and not updated_data.get("choices"):
        raise WebInputError("choice形式にはchoicesが必要です")

    marker_index = stored_body.rfind(_tbd.ANSWER_MARKER)
    if marker_index < 0:
        raise WebInputError("回答欄マーカーがありません")
    answer_heading_index = stored_body.rfind(_tbd.ANSWER_HEADING, 0, marker_index)
    question_heading_index = stored_body.rfind(_tbd.QUESTION_HEADING, 0, answer_heading_index)
    if answer_heading_index < 0 or question_heading_index < 0:
        raise WebInputError("TBDの質問見出しまたは回答見出しがありません")
    question_heading_end = question_heading_index + len(_tbd.QUESTION_HEADING)
    updated_body = (
        stored_body[:question_heading_end]
        + "\n\n"
        + normalized_message_body.rstrip()
        + "\n\n"
        + stored_body[answer_heading_index:]
    )
    return _frontmatter.serialize_frontmatter(updated_data, updated_body)


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
    """`feedback_dir`配下のファイル名群を検証・解決し、未存在があればexit 2する。

    `feedback_dir`には`start-processing`はinbox、`return-to-inbox`はprocessingが渡される。
    エラーメッセージは`feedback_dir.name`から動的に状態名を組み込み、呼び出し元の状態と一致させる。
    """
    paths = [_validate_filename(f, feedback_dir) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"{feedback_dir.name}に存在しません: {p.name}", file=sys.stderr)
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


def _atomic_write_text(path: pathlib.Path, content: str) -> None:
    """同一ディレクトリの一時ファイルから置換してUTF-8本文を原子的に保存する。"""
    temporary_path: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            temporary_path = pathlib.Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def convert_entry_to_plan(
    private_notes: pathlib.Path,
    *,
    filename: str,
    plan_file: str,
    depends_on: tuple[str, ...] = (),
    target_repo: str | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None]:
    """既存feedbackを計画実装型へ変換し、保存済みメタデータを返す。"""
    plan_path = pathlib.Path(plan_file)
    if not plan_path.is_absolute():
        raise WebInputError("plan_fileは絶対パスで指定してください")
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    _validate_filenames_only([filename, *depends_on], inbox_dir)
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        try:
            if not plan_path.is_file():
                raise WebInputError(f"plan_fileが実在する通常ファイルではありません: {plan_file}")
        except OSError as error:
            raise WebInputError(f"plan_fileを検証できません: {plan_file}") from error

        path = _resolve_processable_targets([filename], inbox_dir, processing_dir)[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(f"frontmatterが破損しているため変換できません: {path.name}")
        data, body = parsed
        if _require_type(path, text) != MQ_TYPE_FEEDBACK:
            raise WebInputError(f"feedbackだけを計画実装型へ変換できます: {path.name}")
        raw_entry_repo = data.get("target_repo")
        if not isinstance(raw_entry_repo, str):
            raise WebInputError(f"target_repoが不正です: {path.name}")
        entry_repo = _resolve_repo_id(raw_entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")

        target_commit = data.get("target_commit")
        _add._verify_plan_base_commit(  # pylint: disable=protected-access
            plan_path,
            target_commit if isinstance(target_commit, str) else None,
        )
        canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in depends_on))
        if path.name in canonical_dependencies:
            raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
        data["plan_file"] = str(plan_path)
        data.pop("queue_schedule", None)
        if canonical_dependencies:
            data["depends_on"] = list(canonical_dependencies)
        else:
            data.pop("depends_on", None)
        updated_text = _frontmatter.serialize_frontmatter(data, body)
        if updated_text != text:
            _atomic_write_text(path, updated_text)
            relative_path = str(path.relative_to(private_notes))
            _commit_and_push(private_notes, "chore: convert feedback item to plan", [relative_path])
        return _add._read_saved_entry_details(path)  # pylint: disable=protected-access


def _cmd_convert_to_plan(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """convert-to-planサブコマンドを実行する。"""
    target_repo = args.target_repo
    if target_repo is None:
        target_repo, _local_worktree = _add.resolve_add_target(None)
    try:
        details = convert_entry_to_plan(
            private_notes,
            filename=args.filename,
            plan_file=args.plan_file,
            depends_on=tuple(args.depends_on or ()),
            target_repo=target_repo,
        )
    except WebInputError as error:
        print(f"変換を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"計画実装型へ変換: {args.filename}")
    _add._print_entry_details(details)  # pylint: disable=protected-access


def set_entry_dependencies(
    private_notes: pathlib.Path,
    *,
    filename: str,
    depends_on: tuple[str, ...],
    target_repo: str | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None]:
    """既存feedbackの明示依存だけを更新し、保存済みメタデータを返す。"""
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    _validate_filenames_only([filename, *depends_on], inbox_dir)
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        path = _resolve_processable_targets([filename], inbox_dir, processing_dir)[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(f"frontmatterが破損しているため依存を更新できません: {path.name}")
        data, body = parsed
        if _require_type(path, text) != MQ_TYPE_FEEDBACK:
            raise WebInputError(f"feedbackだけ依存を更新できます: {path.name}")
        raw_entry_repo = data.get("target_repo")
        if not isinstance(raw_entry_repo, str):
            raise WebInputError(f"target_repoが不正です: {path.name}")
        entry_repo = _resolve_repo_id(raw_entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")

        canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in depends_on))
        if path.name in canonical_dependencies:
            raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
        data.pop("queue_schedule", None)
        if canonical_dependencies:
            data["depends_on"] = list(canonical_dependencies)
        else:
            data.pop("depends_on", None)
        updated_text = _frontmatter.serialize_frontmatter(data, body)
        if updated_text != text:
            _atomic_write_text(path, updated_text)
            relative_path = str(path.relative_to(private_notes))
            _commit_and_push(private_notes, "chore: update feedback dependencies", [relative_path])
        return _add._read_saved_entry_details(path)  # pylint: disable=protected-access


def _cmd_set_dependencies(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """set-dependenciesサブコマンドを実行する。"""
    target_repo = args.target_repo
    if target_repo is None:
        target_repo, _local_worktree = _add.resolve_add_target(None)
    try:
        details = set_entry_dependencies(
            private_notes,
            filename=args.filename,
            depends_on=tuple(args.depends_on or ()),
            target_repo=target_repo,
        )
    except WebInputError as error:
        print(f"依存更新を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"依存を更新: {args.filename}")
    _add._print_entry_details(details)  # pylint: disable=protected-access


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


def _cmd_return_to_inbox(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """return-to-inboxサブコマンド: processingからinbox/へ戻しcommit・push。

    保留判定でprocessing化済みの対象を未処理状態へ戻す用途で使う
    （`agent-toolkit:process-feedbacks`スキルのステップ2.5・ステップ7参照）。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "return-to-inbox")
    filenames = transition_entries(
        private_notes,
        action="return-to-inbox",
        filenames=args.filenames,
        now=datetime.datetime.now(),
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件inboxへ差し戻し: {', '.join(filenames)}")


def _cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """rmサブコマンド: 個別指定または対象リポジトリ単位でactive項目を削除する。"""
    if args.all:
        filenames = _remove_all.remove_all_entries(
            private_notes,
            target_repo=args.target_repo,
            assume_yes=args.yes,
            force=args.force,
            note=args.note,
        )
        if filenames:
            print(f"{len(filenames)}件削除: {', '.join(filenames)}")
        return

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
    """editサブコマンド: MESSAGE又は$EDITORで対象を編集しcommit・pushする。

    無引数時は_pull実行後にinbox配下でファイル名順の最大値（最終追加分）を選択する。
    """
    message = args.message
    if message is not None and args.filename is None:
        args.subparser.error("MESSAGEを指定する場合はFILENAMEも指定してください。")
    if message is not None:
        try:
            _add.reject_message_file_path(message)
        except WebInputError as error:
            print(f"編集を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
    editor = None
    if message is None:
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
    original = snapshot.decode("utf-8")
    tmp_path: pathlib.Path | None = None
    if message is None:
        assert editor is not None
        tmp_path = _copy_to_tempfile(snapshot)
        subprocess.run([editor, str(tmp_path)], check=True)
        edited = tmp_path.read_text(encoding="utf-8")
    else:
        try:
            edited = _build_noninteractive_edit_content(path, original, message)
        except WebInputError as error:
            print(f"編集を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
    if edited == original:
        if tmp_path is not None:
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
        if tmp_path is None:
            print(
                f"編集中に他プロセスが対象を変更しました: {path.name}。"
                "指定したMESSAGEは反映されていません。同じFILENAMEとMESSAGEで再実行してください。",
                file=sys.stderr,
            )
        else:
            print(
                f"編集中に他プロセスが対象を変更しました: {path.name}。"
                f"編集内容は{tmp_path}に残しています。再度atk mq editを実行してください。",
                file=sys.stderr,
            )
        sys.exit(1)
    if tmp_path is not None:
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
