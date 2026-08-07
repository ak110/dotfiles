"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_mutations.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import hashlib
import os
import pathlib
import secrets
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
    RESERVATION_INTERNAL_REPO,
    Reservation,
    ReservationConflict,
    WebInputError,
    _commit_and_push,
    _copy_to_tempfile,
    _dedup_positional_filenames,
    _is_tbd_answered,
    _pull,
    _push_pending_commits,
    _repo_lock,
    _require_type,
    _stamp_result,
    _subdir,
    _validate_filename,
    _validate_filenames_only,
    ensure_reservation_mutation_allowed,
    parse_reservation,
    parse_reservation_companion,
    reservation_matches_companion,
    reservation_metadata_present,
)
from _atk_mq_list import _has_category
from _atk_mq_repo import _resolve_repo_id, _verify_frontmatter_target_repo, _verify_target_repo_content
from _atk_mq_repo import edit_entry as _edit_entry

_CATEGORY_GATE_THRESHOLD = 3
_RESERVED_FRONTMATTER_KEYS_FOR_EDITING = (
    "target_commit",
    "depends_on",
    "repair_target",
    "repair_kind",
    "plan_file",
    "reservation",
    "reservation_companion",
    "target_commit_history",
)

_DEFAULT_RESERVATION_LEASE_MINUTES = 30


def _entry_target_repo(path: pathlib.Path, text: str) -> str:
    """エントリの`target_repo`を検証し、正規化した識別子を返す。"""
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        print(f"frontmatterを解析できないため採否処理を停止しました: {path}", file=sys.stderr)
        sys.exit(2)
    raw_target_repo = parsed[0].get("target_repo")
    if not isinstance(raw_target_repo, str) or not raw_target_repo:
        print(f"frontmatterにtarget_repoがないため採否処理を停止しました: {path}", file=sys.stderr)
        sys.exit(2)
    return _resolve_repo_id(raw_target_repo)


def _answered_tbd_blockers(private_notes: pathlib.Path, mutation_paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """同じ対象repoにある、今回の操作対象外の回答済みactive TBDを返す。"""
    excluded = set(mutation_paths)
    target_repos = {_entry_target_repo(path, path.read_text(encoding="utf-8")) for path in mutation_paths}
    blockers: list[pathlib.Path] = []
    for state in (MQ_STATE_INBOX, MQ_STATE_PROCESSING):
        state_dir = private_notes / state
        if not state_dir.is_dir():
            continue
        for path in sorted(state_dir.iterdir()):
            if path in excluded or path.suffix != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            entry_repo = _entry_target_repo(path, text)
            if entry_repo not in target_repos:
                continue
            if _require_type(path, text) == MQ_TYPE_TBD and _is_tbd_answered(text):
                blockers.append(path)
    return blockers


def _validate_no_reserved_frontmatter_modification(original: str, updated: str) -> None:
    """frontmatterの予約キーが不正に追加・変更・削除されていないかを検証する。

    利用者が$EDITORまたはWeb APIで内部管理用frontmatterを書き換えることを防ぐ。
    """
    if reservation_metadata_present(original):
        raise ReservationConflict("予約中の項目は所有者用操作だけが変更できます")
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
    state: str | None = None,
    expected_content: str | None = None,
) -> list[str]:
    """平引数でエントリの一括状態遷移又は削除を実行する。

    `action="remove"`かつ`force=False`（既定）の場合、processing状態のファイルが
    対象に含まれるとexit 2で拒否する（`atk mq rm`の既定保護。処理中ファイルの
    意図しない削除を防ぐ。解除するには`force=True`を渡す）。
    """
    if action not in {"start-processing", "return-to-inbox", "adopt", "reject", "remove"}:
        raise WebInputError(f"未知のエントリ操作です: {action}")
    if state is not None and (action != "remove" or state not in {MQ_STATE_INBOX, MQ_STATE_PROCESSING}):
        raise WebInputError("stateはremoveでinbox又はprocessingを指定する場合に限り使用できます")
    if expected_content is not None and (action != "remove" or len(filenames) != 1):
        raise WebInputError("expected_contentはremoveで1件を指定する場合に限り使用できます")
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    _validate_filenames_only(filenames, inbox_dir)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        search_dirs = (
            [private_notes / state]
            if state is not None
            else [inbox_dir]
            if action == "start-processing"
            else [processing_dir]
            if action == "return-to-inbox"
            else [inbox_dir, processing_dir]
        )
        missing_is_conflict = action == "remove" and expected_content is not None
        paths = (
            _resolve_feedback_targets(filenames, private_notes / state, missing_is_conflict=missing_is_conflict)
            if state is not None
            else _resolve_feedback_targets(filenames, inbox_dir, missing_is_conflict=missing_is_conflict)
            if action == "start-processing"
            else _resolve_feedback_targets(filenames, processing_dir, missing_is_conflict=missing_is_conflict)
            if action == "return-to-inbox"
            else _resolve_processable_targets(
                filenames,
                inbox_dir,
                processing_dir,
                missing_is_conflict=missing_is_conflict,
            )
        )
        current_content: str | None = None
        if action == "remove" and expected_content is not None:
            try:
                current_content = paths[0].read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
            if current_content != expected_content:
                raise RuntimeError("編集中に他プロセスが対象を変更しました")
        if current_content is None:
            for filename in filenames:
                _verify_frontmatter_target_repo(filename, search_dirs, target_repo)
        else:
            normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None
            _verify_target_repo_content(paths[0], current_content, normalized_target_repo)
        for path in paths:
            ensure_reservation_mutation_allowed(path, path.read_text(encoding="utf-8"))
        if action in {"adopt", "reject"}:
            answered_tbds = _answered_tbd_blockers(private_notes, paths)
            if answered_tbds:
                print(
                    "対象リポジトリに今回の操作対象外の回答済みTBDがあるため、採否処理を停止しました: "
                    + ", ".join(path.name for path in answered_tbds),
                    file=sys.stderr,
                )
                sys.exit(2)
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
                    "（agent-toolkit:process-feedbacks配下references/decision-format.md"
                    "「上位カテゴリの評価」参照）。",
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
        changed = subprocess.run(
            ["git", "status", "--porcelain", "--", inbox_rel, processing_rel],
            cwd=private_notes,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for line in changed:
            relative = line[3:].split(" -> ")[-1]
            current_path = private_notes / relative
            current_text = current_path.read_text(encoding="utf-8") if current_path.is_file() else ""
            original = subprocess.run(
                ["git", "show", f"HEAD:{relative}"],
                cwd=private_notes,
                check=False,
                capture_output=True,
                text=True,
            )
            if reservation_metadata_present(current_text) or (
                original.returncode == 0 and reservation_metadata_present(original.stdout)
            ):
                raise ReservationConflict(f"予約中の項目は外部編集commitで変更できません: {relative}")
        _commit_and_push(private_notes, "chore: edit queue items externally", [inbox_rel, processing_rel])
    return True


def _resolve_feedback_targets(
    filenames: list[str],
    feedback_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    """`feedback_dir`配下のファイル名群を検証・解決し、未存在があればexit 2する。

    `feedback_dir`には`start-processing`はinbox、`return-to-inbox`はprocessingが渡される。
    エラーメッセージは`feedback_dir.name`から動的に状態名を組み込み、呼び出し元の状態と一致させる。
    """
    paths = [_validate_filename(f, feedback_dir) for f in filenames]
    missing = [p for p in paths if not p.exists()]
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        for p in missing:
            print(f"{feedback_dir.name}に存在しません: {p.name}", file=sys.stderr)
        sys.exit(2)
    return paths


def _resolve_processable_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
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
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
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
        ensure_reservation_mutation_allowed(path, text)
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
        ensure_reservation_mutation_allowed(path, text)
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


def _utc_text(value: datetime.datetime) -> str:
    """UTC日時を秒精度のISO 8601文字列へ変換する。"""
    return value.astimezone(datetime.UTC).replace(microsecond=0).isoformat()


def _token_hash(token: str) -> str:
    """予約tokenを保存用SHA-256へ変換する。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _canonical_dependencies(values: tuple[str, ...], inbox_dir: pathlib.Path) -> tuple[str, ...]:
    """依存filenameを検証し、初出順へ正規化する。"""
    return tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in values))


def _reservation_for_path(path: pathlib.Path, text: str) -> tuple[dict[str, object], str, Reservation]:
    """予約付きprocessing feedbackを検証して返す。"""
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        raise WebInputError(f"frontmatterが破損しています: {path.name}")
    data, body = parsed
    reservation, invalid = parse_reservation(data, state=path.parent.name, entry_type=_require_type(path, text))
    if invalid or reservation is None:
        raise ReservationConflict(f"予約metadataが不正です: {path.name}")
    return data, body, reservation


def _verify_token(path: pathlib.Path, reservation: Reservation, token: str) -> None:
    """生tokenが保存hashと一致することを検証する。"""
    if not secrets.compare_digest(reservation.token_hash, _token_hash(token)):
        raise ReservationConflict(f"予約tokenが一致しません: {path.name}")


def _find_active_path(private_notes: pathlib.Path, filename: str) -> pathlib.Path | None:
    """active状態からfilenameを解決する。"""
    for state in (MQ_STATE_PROCESSING, MQ_STATE_INBOX):
        candidate = private_notes / state / filename
        if candidate.is_file():
            return candidate
    return None


def _resolve_companion_locked(
    private_notes: pathlib.Path,
    *,
    reservation: Reservation,
    target_repo: str,
    target_filename: str,
) -> pathlib.Path:
    """対応する内部companionを検証して返す。"""
    path = _find_active_path(private_notes, reservation.companion)
    if path is None:
        raise ReservationConflict(f"予約companionが見つかりません: {reservation.companion}")
    text = path.read_text(encoding="utf-8")
    parsed = _frontmatter.parse_frontmatter(text)
    entry_type = parsed[0].get("type") if parsed is not None else None
    metadata = (
        parse_reservation_companion(parsed[0], entry_type=entry_type if isinstance(entry_type, str) else None)
        if parsed is not None
        else None
    )
    if not reservation_matches_companion(
        reservation,
        metadata,
        target_repo=target_repo,
        target_filename=target_filename,
    ):
        raise ReservationConflict(f"予約companionが一致しません: {reservation.companion}")
    return path


def _matching_companion_path(
    private_notes: pathlib.Path,
    *,
    reservation: Reservation,
    target_repo: str,
    target_filename: str,
) -> pathlib.Path | None:
    """相互対応するcompanionだけを削除対象として返す。"""
    path = _find_active_path(private_notes, reservation.companion)
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    parsed = _frontmatter.parse_frontmatter(text)
    entry_type = parsed[0].get("type") if parsed is not None else None
    metadata = (
        parse_reservation_companion(parsed[0], entry_type=entry_type if isinstance(entry_type, str) else None)
        if parsed is not None
        else None
    )
    if not reservation_matches_companion(
        reservation,
        metadata,
        target_repo=target_repo,
        target_filename=target_filename,
    ):
        return None
    return path


def _clear_reservation(
    data: dict[str, object],
    reservation: Reservation,
    *,
    add_depends_on: tuple[str, ...] = (),
    remove_companion_dependency: bool = True,
) -> None:
    """予約内部metadataと、検証済みならcompanion依存を除去する。"""
    data.pop("reservation", None)
    raw_dependencies = data.get("depends_on")
    dependencies = (
        [
            value
            for value in raw_dependencies
            if isinstance(value, str)
            and (
                not remove_companion_dependency
                or not reservation.companion_dependency_added
                or value != reservation.companion_dependency_filename
            )
        ]
        if isinstance(raw_dependencies, list)
        else []
    )
    dependencies.extend(value for value in add_depends_on if value not in dependencies)
    if dependencies:
        data["depends_on"] = dependencies
    else:
        data.pop("depends_on", None)


def reserve_inbox_entries(
    private_notes: pathlib.Path,
    *,
    repo_path: str,
    filenames: list[str],
    reason: str,
    plan_file: str | None = None,
    lease_minutes: int = _DEFAULT_RESERVATION_LEASE_MINUTES,
    now: datetime.datetime | None = None,
    lock_timeout: float = -1,
) -> str:
    """Inbox feedback群を同一tokenで期限付き予約する。"""
    if lease_minutes <= 0:
        raise WebInputError("lease_minutesは1以上で指定してください")
    if not reason.strip():
        raise WebInputError("reasonは空でない文字列で指定してください")
    filenames = list(dict.fromkeys(filenames))
    target_repo, worktree = _add.resolve_add_target(repo_path)
    if worktree is None:
        raise WebInputError("reserve-inboxにはローカルworktreeを指定してください")
    plan_path = pathlib.Path(plan_file) if plan_file is not None else None
    if plan_path is not None and (not plan_path.is_absolute() or not plan_path.is_file()):
        raise WebInputError("plan_fileは実在する絶対パスで指定してください")
    if plan_path is not None:
        _add._verify_plan_base_commit(  # pylint: disable=protected-access
            plan_path,
            _add.resolve_head_commit(worktree),
        )
    effective_now = (now or datetime.datetime.now(datetime.UTC)).astimezone(datetime.UTC)
    expires_at = effective_now + datetime.timedelta(minutes=lease_minutes)
    token = secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    _validate_filenames_only(filenames, inbox_dir)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        try:
            paths = _resolve_feedback_targets(filenames, inbox_dir, missing_is_conflict=True)
        except RuntimeError as error:
            raise ReservationConflict(str(error)) from error
        loaded: list[tuple[pathlib.Path, dict[str, object], str]] = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            parsed = _frontmatter.parse_frontmatter(text)
            if parsed is None or _require_type(path, text) != MQ_TYPE_FEEDBACK:
                raise WebInputError(f"予約できるのはfrontmatterが正常なfeedbackだけです: {path.name}")
            data, body = parsed
            raw_repo = data.get("target_repo")
            if not isinstance(raw_repo, str) or _resolve_repo_id(raw_repo) != target_repo:
                raise WebInputError(f"target_repoが一致しません: {path.name}")
            if reservation_metadata_present(text):
                raise ReservationConflict(f"既に予約されています: {path.name}")
            loaded.append((path, data, body))
        for path, data, body in loaded:
            companion_message = f"予約中のキュー項目を旧process-loopから保護する内部項目: {path.name}"
            companion = _add._add_entries_locked(  # pylint: disable=protected-access
                private_notes,
                parsed_messages=[({}, companion_message)],
                target_repo=RESERVATION_INTERNAL_REPO,
                source="reservation",
                now=effective_now,
                entry_type=MQ_TYPE_FEEDBACK,
                scope=None,
                question_type=None,
                choices=None,
            )[0]
            companion_path = inbox_dir / companion
            companion_parsed = _frontmatter.parse_frontmatter(companion_path.read_text(encoding="utf-8"))
            assert companion_parsed is not None
            companion_data, companion_body = companion_parsed
            companion_data["reservation_companion"] = {
                "target_repo": target_repo,
                "target_filename": path.name,
                "token_hash": token_hash,
            }
            companion_data["queue_schedule"] = {
                "dependency": {
                    "kind": "external-upstream",
                    "recheck_after": "9999-12-31T23:59:59+00:00",
                    "condition": "対応する予約項目が解除されること",
                    "hold_reason": "予約互換companion",
                }
            }
            _atomic_write_text(companion_path, _frontmatter.serialize_frontmatter(companion_data, companion_body))
            raw_dependencies = data.get("depends_on")
            dependencies = list(raw_dependencies) if isinstance(raw_dependencies, list) else []
            if companion not in dependencies:
                dependencies.append(companion)
            data["depends_on"] = dependencies
            reservation_data: dict[str, object] = {
                "token_hash": token_hash,
                "owner": str(worktree),
                "generation": "1",
                "reason": reason.strip(),
                "reserved_at": _utc_text(effective_now),
                "updated_at": _utc_text(effective_now),
                "expires_at": _utc_text(expires_at),
                "companion": companion,
                "companion_dependency_added": "true",
                "companion_dependency_filename": companion,
            }
            if plan_path is not None:
                reservation_data["plan_file"] = str(plan_path)
            data["reservation"] = reservation_data
            _atomic_write_text(path, _frontmatter.serialize_frontmatter(data, body))
            shutil.move(path, processing_dir / path.name)
        _commit_and_push(private_notes, f"chore: reserve {len(paths)} queue entries", list(MQ_STATES))
    return token


def renew_reservations(
    private_notes: pathlib.Path,
    *,
    filenames: list[str],
    reservation_token: str,
    target_repo: str | None = None,
    lease_minutes: int = _DEFAULT_RESERVATION_LEASE_MINUTES,
    now: datetime.datetime | None = None,
    lock_timeout: float = -1,
) -> list[dict[str, object | None]]:
    """所有者tokenが一致する予約群の期限と世代を更新する。"""
    if lease_minutes <= 0:
        raise WebInputError("lease_minutesは1以上で指定してください")
    filenames = list(dict.fromkeys(filenames))
    effective_now = (now or datetime.datetime.now(datetime.UTC)).astimezone(datetime.UTC)
    processing_dir = private_notes / MQ_STATE_PROCESSING
    _validate_filenames_only(filenames, private_notes / MQ_STATE_INBOX)
    expected_repo = _resolve_repo_id(target_repo) if target_repo is not None else None
    details: list[dict[str, object | None]] = []
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        try:
            paths = _resolve_feedback_targets(filenames, processing_dir, missing_is_conflict=True)
        except RuntimeError as error:
            raise ReservationConflict(str(error)) from error
        loaded: list[tuple[pathlib.Path, dict[str, object], str, Reservation]] = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            data, body, reservation = _reservation_for_path(path, text)
            _verify_token(path, reservation, reservation_token)
            raw_repo = data.get("target_repo")
            if not isinstance(raw_repo, str):
                raise WebInputError(f"target_repoが不正です: {path.name}")
            item_repo = _resolve_repo_id(raw_repo)
            if expected_repo is not None and item_repo != expected_repo:
                raise WebInputError(f"target_repoが一致しません: {path.name}")
            _resolve_companion_locked(
                private_notes,
                reservation=reservation,
                target_repo=item_repo,
                target_filename=path.name,
            )
            loaded.append((path, data, body, reservation))
        for path, data, body, reservation in loaded:
            raw = dict(data["reservation"]) if isinstance(data["reservation"], dict) else {}
            raw["generation"] = str(reservation.generation + 1)
            raw["updated_at"] = _utc_text(effective_now)
            raw["expires_at"] = _utc_text(effective_now + datetime.timedelta(minutes=lease_minutes))
            data["reservation"] = raw
            _atomic_write_text(path, _frontmatter.serialize_frontmatter(data, body))
            details.append(_add._read_saved_entry_details(path))  # pylint: disable=protected-access
        _commit_and_push(private_notes, f"chore: renew {len(paths)} queue reservations", [MQ_STATE_PROCESSING])
    return details


def merge_inbox_entry(
    private_notes: pathlib.Path,
    *,
    repo_path: str,
    filename: str,
    message: str,
    reservation_token: str | None = None,
    plan_file: str | None = None,
    depends_on: tuple[str, ...] | None = None,
    supersede: tuple[str, ...] = (),
    now: datetime.datetime | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None]:
    """inbox又は所有中予約へ本文と計画metadataを原子的に統合する。"""
    target_repo, worktree = _add.resolve_add_target(repo_path)
    if worktree is None:
        raise WebInputError("merge-inboxにはローカルworktreeを指定してください")
    target_commit = _add.resolve_head_commit(worktree)
    parsed_message = _add.parse_entry_message(message, entry_type=MQ_TYPE_FEEDBACK)
    message_data, message_body = parsed_message
    plan_path = pathlib.Path(plan_file) if plan_file is not None else None
    if plan_path is not None:
        if not plan_path.is_absolute() or not plan_path.is_file():
            raise WebInputError("plan_fileは実在する絶対パスで指定してください")
        _add._verify_plan_base_commit(plan_path, target_commit)  # pylint: disable=protected-access
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    canonical_name = _validate_filename(filename, inbox_dir).name
    supersede_names = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in supersede))
    if canonical_name in supersede_names:
        raise WebInputError("canonical項目をsupersedeへ指定できません")
    canonical_dependencies = _canonical_dependencies(depends_on, inbox_dir) if depends_on is not None else None
    if canonical_dependencies is not None and canonical_name in canonical_dependencies:
        raise WebInputError(f"自分自身を依存先へ指定できません: {canonical_name}")
    effective_now = now or datetime.datetime.now(datetime.UTC)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        try:
            path = _resolve_processable_targets(
                [canonical_name],
                inbox_dir,
                processing_dir,
                missing_is_conflict=True,
            )[0]
        except RuntimeError as error:
            raise ReservationConflict(str(error)) from error
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None or _require_type(path, text) != MQ_TYPE_FEEDBACK:
            raise WebInputError(f"統合できるのはfrontmatterが正常なfeedbackだけです: {path.name}")
        data, _stored_body = parsed
        raw_repo = data.get("target_repo")
        if not isinstance(raw_repo, str) or _resolve_repo_id(raw_repo) != target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}")
        reservation: Reservation | None = None
        companion_path: pathlib.Path | None = None
        if path.parent.name == MQ_STATE_PROCESSING:
            if reservation_token is None:
                raise ReservationConflict(f"processing項目の統合には予約tokenが必要です: {path.name}")
            data, _stored_body, reservation = _reservation_for_path(path, text)
            _verify_token(path, reservation, reservation_token)
            companion_path = _resolve_companion_locked(
                private_notes,
                reservation=reservation,
                target_repo=target_repo,
                target_filename=path.name,
            )
        elif reservation_token is not None:
            raise ReservationConflict(f"指定した予約は既に失効又は解除されています: {path.name}")
        else:
            ensure_reservation_mutation_allowed(path, text)
        try:
            supersede_paths = _resolve_feedback_targets(list(supersede_names), inbox_dir, missing_is_conflict=True)
        except RuntimeError as error:
            raise ReservationConflict(str(error)) from error
        for candidate in supersede_paths:
            candidate_text = candidate.read_text(encoding="utf-8")
            if _require_type(candidate, candidate_text) != MQ_TYPE_FEEDBACK:
                raise WebInputError(f"supersede対象はfeedbackに限ります: {candidate.name}")
            ensure_reservation_mutation_allowed(candidate, candidate_text)
            candidate_data = _frontmatter.parse_frontmatter(candidate_text)
            assert candidate_data is not None
            raw_candidate_repo = candidate_data[0].get("target_repo")
            if not isinstance(raw_candidate_repo, str) or _resolve_repo_id(raw_candidate_repo) != target_repo:
                raise WebInputError(f"supersede対象のtarget_repoが一致しません: {candidate.name}")
        previous_target = data.get("target_commit")
        if isinstance(previous_target, str) and previous_target != target_commit:
            raw_history = data.get("target_commit_history")
            history = [value for value in raw_history if isinstance(value, str)] if isinstance(raw_history, list) else []
            if previous_target not in history:
                history.append(previous_target)
            data["target_commit_history"] = history
        data["target_commit"] = target_commit
        for key, value in message_data.items():
            if key not in _add._RESERVED_FRONTMATTER_KEYS:  # pylint: disable=protected-access
                data[key] = value
        if plan_path is not None:
            data["plan_file"] = str(plan_path)
        if canonical_dependencies is not None:
            if canonical_dependencies:
                data["depends_on"] = list(canonical_dependencies)
            else:
                data.pop("depends_on", None)
        if reservation is not None:
            assert companion_path is not None
            companion_path.unlink()
            _clear_reservation(data, reservation)
        updated = _frontmatter.serialize_frontmatter(
            data, message_body if message_body.startswith("\n") else f"\n{message_body.rstrip()}\n"
        )
        destination = inbox_dir / path.name
        _atomic_write_text(path, updated)
        if path.parent.name == MQ_STATE_PROCESSING:
            shutil.move(path, destination)
        rejected_dir = _subdir(private_notes, MQ_STATE_REJECTED)
        for candidate in supersede_paths:
            _stamp_result(
                candidate,
                outcome=MQ_STATE_REJECTED,
                now=effective_now,
                commit=None,
                note=f"{canonical_name}へ統合",
            )
            shutil.move(candidate, rejected_dir / candidate.name)
        _commit_and_push(private_notes, "chore: merge feedback into inbox", list(MQ_STATES))
        return _add._read_saved_entry_details(destination)  # pylint: disable=protected-access


def release_reservations(
    private_notes: pathlib.Path,
    *,
    filenames: list[str],
    reservation_token: str,
    target_repo: str | None = None,
    add_depends_on: tuple[str, ...] = (),
    lock_timeout: float = -1,
) -> list[dict[str, object | None]]:
    """所有者tokenが一致する予約を解除してinboxへ戻す。"""
    filenames = list(dict.fromkeys(filenames))
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = private_notes / MQ_STATE_PROCESSING
    dependencies = _canonical_dependencies(add_depends_on, inbox_dir)
    expected_repo = _resolve_repo_id(target_repo) if target_repo is not None else None
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        try:
            paths = _resolve_feedback_targets(filenames, processing_dir, missing_is_conflict=True)
        except RuntimeError as error:
            raise ReservationConflict(str(error)) from error
        loaded: list[tuple[pathlib.Path, dict[str, object], str, Reservation, pathlib.Path]] = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            data, body, reservation = _reservation_for_path(path, text)
            _verify_token(path, reservation, reservation_token)
            raw_repo = data.get("target_repo")
            if expected_repo is not None and (not isinstance(raw_repo, str) or _resolve_repo_id(raw_repo) != expected_repo):
                raise WebInputError(f"target_repoが一致しません: {path.name}")
            if not isinstance(raw_repo, str):
                raise WebInputError(f"target_repoが不正です: {path.name}")
            companion_path = _resolve_companion_locked(
                private_notes,
                reservation=reservation,
                target_repo=_resolve_repo_id(raw_repo),
                target_filename=path.name,
            )
            loaded.append((path, data, body, reservation, companion_path))
        for path, data, body, reservation, companion_path in loaded:
            companion_path.unlink()
            _clear_reservation(data, reservation, add_depends_on=dependencies)
            _atomic_write_text(path, _frontmatter.serialize_frontmatter(data, body))
            shutil.move(path, inbox_dir / path.name)
        _commit_and_push(private_notes, f"chore: release {len(paths)} queue reservations", list(MQ_STATES))
        return [
            _add._read_saved_entry_details(inbox_dir / path.name)  # pylint: disable=protected-access
            for path in paths
        ]


def recover_reservation(
    private_notes: pathlib.Path,
    *,
    repo_path: str,
    filename: str,
    expected_generation: int | None = None,
    expected_expires_at: str | None = None,
    invalid: bool = False,
    add_depends_on: tuple[str, ...] = (),
    tbd_message: str | None = None,
    tbd_question_type: str | None = None,
    tbd_choices: str | None = None,
    tbd_scope: str | None = None,
    tbd_source: str | None = None,
    now: datetime.datetime | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None] | None:
    """期限切れ又は不正予約をCAS検証して原子的に回収する。"""
    if invalid == (expected_generation is not None or expected_expires_at is not None):
        raise WebInputError("--invalid又は世代・期限の組のいずれか一方を指定してください")
    if not invalid and (expected_generation is None or expected_expires_at is None):
        raise WebInputError("期限切れ回収にはexpected_generationとexpected_expires_atが必要です")
    if (tbd_message is None) != (tbd_question_type is None):
        raise WebInputError("TBD本文とquestion_typeは同時に指定してください")
    if tbd_message is None and any(value is not None for value in (tbd_choices, tbd_scope, tbd_source)):
        raise WebInputError("TBD専用metadataはTBD本文と同時に指定してください")
    target_repo, worktree = _add.resolve_add_target(repo_path)
    if worktree is None:
        raise WebInputError("recover-reservationにはローカルworktreeを指定してください")
    inbox_dir = private_notes / MQ_STATE_INBOX
    dependencies = list(_canonical_dependencies(add_depends_on, inbox_dir))
    effective_now = (now or datetime.datetime.now(datetime.UTC)).astimezone(datetime.UTC)
    parsed_tbd = [_add.parse_entry_message(tbd_message, entry_type=MQ_TYPE_TBD)] if tbd_message is not None else None
    if tbd_question_type == "choice" and not tbd_choices:
        raise WebInputError("choice形式にはchoicesが必要です")
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        path = _find_active_path(private_notes, _validate_filename(filename, inbox_dir).name)
        if path is None:
            raise ReservationConflict(f"回収対象が存在しません: {filename}")
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(f"frontmatter修復経路を使用してください: {path.name}")
        data, body = parsed
        companion_metadata = parse_reservation_companion(data, entry_type=_require_type(path, text))
        if companion_metadata is not None:
            if not invalid or companion_metadata["target_repo"] != target_repo:
                raise ReservationConflict(f"内部companionの回収条件が一致しません: {path.name}")
            target = _find_active_path(private_notes, companion_metadata["target_filename"])
            if target is not None:
                target_parsed = _frontmatter.parse_frontmatter(target.read_text(encoding="utf-8"))
                target_reservation = (
                    parse_reservation(
                        target_parsed[0],
                        state=target.parent.name,
                        entry_type=_require_type(target, target.read_text(encoding="utf-8")),
                    )[0]
                    if target_parsed is not None
                    else None
                )
                target_data = target_parsed[0] if target_parsed is not None else None
                target_repo_value = target_data.get("target_repo") if target_data is not None else None
                if (
                    target_reservation is not None
                    and isinstance(target_repo_value, str)
                    and _resolve_repo_id(target_repo_value) == companion_metadata["target_repo"]
                    and target_reservation.companion == path.name
                    and target_reservation.token_hash == companion_metadata["token_hash"]
                ):
                    raise ReservationConflict(f"有効な予約へ変化したため回収できません: {path.name}")
            path.unlink()
            _commit_and_push(private_notes, "chore: recover orphan reservation companion", list(MQ_STATES))
            return None
        raw_repo = data.get("target_repo")
        if not isinstance(raw_repo, str) or _resolve_repo_id(raw_repo) != target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}")
        reservation, reservation_invalid = parse_reservation(
            data,
            state=path.parent.name,
            entry_type=_require_type(path, text),
        )
        if invalid:
            matching_companion = (
                _matching_companion_path(
                    private_notes,
                    reservation=reservation,
                    target_repo=target_repo,
                    target_filename=path.name,
                )
                if reservation is not None
                else None
            )
            if not reservation_invalid and matching_companion is not None:
                raise ReservationConflict(f"予約が有効な状態へ変化したため回収できません: {path.name}")
            if matching_companion is not None:
                assert reservation is not None
                matching_companion.unlink()
                _clear_reservation(data, reservation)
            elif reservation is not None:
                _clear_reservation(data, reservation, remove_companion_dependency=False)
            else:
                data.pop("reservation", None)
        else:
            if reservation is None or reservation_invalid:
                raise ReservationConflict(f"予約が不正な状態へ変化したためCAS回収できません: {path.name}")
            if reservation.generation != expected_generation or _utc_text(reservation.expires_at) != expected_expires_at:
                raise ReservationConflict(f"予約の世代又は期限が変化したためCAS回収できません: {path.name}")
            if reservation.expires_at > effective_now:
                raise ReservationConflict(f"予約期限が更新されたためCAS回収できません: {path.name}")
            companion_path = _resolve_companion_locked(
                private_notes,
                reservation=reservation,
                target_repo=target_repo,
                target_filename=path.name,
            )
            companion_path.unlink()
            _clear_reservation(data, reservation)
        if parsed_tbd is not None:
            generated = _add._add_entries_locked(  # pylint: disable=protected-access
                private_notes,
                parsed_messages=parsed_tbd,
                target_repo=target_repo,
                source=tbd_source,
                now=effective_now,
                entry_type=MQ_TYPE_TBD,
                scope=tbd_scope,
                question_type=tbd_question_type,
                choices=tbd_choices,
            )
            dependencies.extend(generated)
        existing = data.get("depends_on")
        merged_dependencies = [value for value in existing if isinstance(value, str)] if isinstance(existing, list) else []
        merged_dependencies.extend(value for value in dependencies if value not in merged_dependencies)
        if merged_dependencies:
            data["depends_on"] = merged_dependencies
        else:
            data.pop("depends_on", None)
        _atomic_write_text(path, _frontmatter.serialize_frontmatter(data, body))
        if path.parent.name == MQ_STATE_PROCESSING:
            shutil.move(path, inbox_dir / path.name)
        _commit_and_push(private_notes, "chore: recover queue reservation", list(MQ_STATES))
        return _add._read_saved_entry_details(inbox_dir / path.name)  # pylint: disable=protected-access


def _cmd_reserve_inbox(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """reserve-inboxサブコマンドを実行する。"""
    token = reserve_inbox_entries(
        private_notes,
        repo_path=args.repo_path,
        filenames=args.filenames,
        reason=args.reason,
        plan_file=args.plan_file,
        lease_minutes=args.lease_minutes,
    )
    print(f"reservation_token: {token}")


def _cmd_renew_reservation(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """renew-reservationサブコマンドを実行する。"""
    renew_reservations(
        private_notes,
        filenames=args.filenames,
        reservation_token=args.reservation_token,
        target_repo=args.target_repo,
        lease_minutes=args.lease_minutes,
    )
    print(f"{len(args.filenames)}件の予約期限を更新しました。")


def _cmd_merge_inbox(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """merge-inboxサブコマンドを実行する。"""
    details = merge_inbox_entry(
        private_notes,
        repo_path=args.repo_path,
        filename=args.filename,
        message=args.message,
        reservation_token=args.reservation_token,
        plan_file=args.plan_file,
        depends_on=tuple(args.depends_on) if args.depends_on is not None else None,
        supersede=tuple(args.supersede or ()),
    )
    print(f"inboxへ統合: {args.filename}")
    _add._print_entry_details(details)  # pylint: disable=protected-access


def _cmd_release_reservation(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """release-reservationサブコマンドを実行する。"""
    release_reservations(
        private_notes,
        filenames=args.filenames,
        reservation_token=args.reservation_token,
        target_repo=args.target_repo,
        add_depends_on=tuple(args.add_depends_on or ()),
    )
    print(f"{len(args.filenames)}件の予約を解除しました。")


def _cmd_recover_reservation(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """recover-reservationサブコマンドを実行する。"""
    details = recover_reservation(
        private_notes,
        repo_path=args.repo_path,
        filename=args.filename,
        expected_generation=args.expected_generation,
        expected_expires_at=args.expected_expires_at,
        invalid=args.invalid,
        add_depends_on=tuple(args.add_depends_on or ()),
        tbd_message=args.tbd_message,
        tbd_question_type=args.tbd_question_type,
        tbd_choices=args.tbd_choices,
        tbd_scope=args.tbd_scope,
        tbd_source=args.tbd_source,
    )
    print(f"予約を回収: {args.filename}")
    if details is not None:
        _add._print_entry_details(details)  # pylint: disable=protected-access


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
    （`agent-toolkit:process-feedbacks`「3. 保留」節参照）。
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
