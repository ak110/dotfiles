"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_mutations.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import os
import pathlib
import re
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
from _atk_mq_repo import (
    _normalize_remote_url,
    _resolve_repo_id,
    _verify_target_repo_content,
)
from _atk_mq_repo import append_entry as _append_entry
from _atk_mq_repo import edit_entry as _edit_entry

_GIT_TIMEOUT_SECONDS = 10.0
_RESERVED_FRONTMATTER_KEYS_FOR_EDITING = (
    "target_commit",
    "depends_on",
    "cooldown_until",
    "repair_target",
    "repair_kind",
    "plan_file",
)


def _entry_target_repo(path: pathlib.Path, text: str) -> str:
    """エントリの`target_repo`を検証し、正規化した識別子を返す。"""
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        print(f"frontmatterを解析できないため処理を停止しました: {path}", file=sys.stderr)
        sys.exit(2)
    raw_target_repo = parsed[0].get("target_repo")
    if not isinstance(raw_target_repo, str) or not raw_target_repo:
        print(f"frontmatterにtarget_repoがないため処理を停止しました: {path}", file=sys.stderr)
        sys.exit(2)
    return _resolve_repo_id(raw_target_repo)


def _candidate_local_worktree(target_repo: str | None) -> pathlib.Path | None:
    """実在パスの引数を優先し、それ以外は現在位置から対応候補の作業ツリーを返す。"""
    if target_repo is not None:
        path = pathlib.Path(target_repo).expanduser()
        if path.exists():
            return path.resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout.strip()
    return pathlib.Path(output) if result.returncode == 0 and output else None


def _local_worktree_repo_id(local_worktree: pathlib.Path) -> str | None:
    """作業ツリーのoriginから対象リポジトリ識別子を返す。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(local_worktree), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return _normalize_remote_url(result.stdout.strip())
    except ValueError:
        return None


def _resolve_commit(local_worktree: pathlib.Path, revision: str) -> str:
    """作業ツリーでrevisionを完全なcommit OIDへ解決する。"""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(local_worktree),
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{revision}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    commit = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        print(
            f"対応commitを解決できませんでした。対象作業ツリーでrevisionを取得して再実行してください: "
            f"{local_worktree} ({revision})",
            file=sys.stderr,
        )
        sys.exit(2)
    return commit


def _commit_values_by_path(
    paths: list[pathlib.Path],
    revision: str | None,
    local_worktree: pathlib.Path | None,
) -> dict[pathlib.Path, str | None]:
    """対象ごとに記録するcommitを解決し、対応不能な群は警告する。"""
    if revision is None:
        return {path: None for path in paths}
    target_repos = {path: _entry_target_repo(path, path.read_text(encoding="utf-8")) for path in paths}
    candidate_repo = _local_worktree_repo_id(local_worktree) if local_worktree is not None else None
    resolved = (
        _resolve_commit(local_worktree, revision)
        if local_worktree is not None and candidate_repo in target_repos.values()
        else None
    )
    values: dict[pathlib.Path, str | None] = {}
    warned: set[str] = set()
    for path, target_repo in target_repos.items():
        if resolved is not None and target_repo == candidate_repo:
            values[path] = resolved
            continue
        values[path] = revision
        if target_repo not in warned:
            print(
                f"警告: 対象リポジトリに対応するローカル作業ツリーを特定できないため、"
                f"対応commitを未検証のまま記録します: {target_repo}",
                file=sys.stderr,
            )
            warned.add(target_repo)
    return values


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


def _validate_transition_options(
    action: str,
    filenames: list[str],
    *,
    state: str | None,
    expected_content: str | None,
    cooldown_days: int | None,
) -> None:
    """状態遷移オプション間の制約を検証する。"""
    if action not in {"start-processing", "return-to-inbox", "adopt", "reject", "remove"}:
        raise WebInputError(f"未知のエントリ操作です: {action}")
    if cooldown_days is not None and (action != "return-to-inbox" or cooldown_days < 3):
        raise WebInputError("cooldown_daysはreturn-to-inboxで3以上を指定してください")
    state_is_valid = (action == "remove" and state in {MQ_STATE_INBOX, MQ_STATE_PROCESSING}) or (
        action == "reject" and state == MQ_STATE_INBOX
    )
    if state is not None and not state_is_valid:
        raise WebInputError("stateはremove、又はinbox限定のrejectでのみ使用できます")
    if expected_content is not None and (action != "remove" or len(filenames) != 1):
        raise WebInputError("expected_contentはremoveで1件を指定する場合に限り使用できます")


def _resolve_transition_paths(
    private_notes: pathlib.Path,
    action: str,
    filenames: list[str],
    state: str | None,
    *,
    missing_is_conflict: bool,
) -> list[pathlib.Path]:
    """操作種別と明示状態から対象エントリを解決する。"""
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    if state is not None:
        return _resolve_feedback_targets(filenames, private_notes / state, missing_is_conflict=missing_is_conflict)
    if action == "start-processing":
        return _resolve_feedback_targets(filenames, inbox_dir, missing_is_conflict=missing_is_conflict)
    if action == "return-to-inbox":
        return _resolve_feedback_targets(filenames, processing_dir, missing_is_conflict=missing_is_conflict)
    return _resolve_processable_targets(
        filenames,
        inbox_dir,
        processing_dir,
        missing_is_conflict=missing_is_conflict,
    )


def _validate_transition_targets(
    paths: list[pathlib.Path],
    *,
    action: str,
    target_repo: str | None,
    expected_content: str | None,
    cooldown_days: int | None,
    force: bool,
) -> str | None:
    """解決済み対象の内容・対象repo・状態保護を検証する。"""
    current_content: str | None = None
    if action == "remove" and expected_content is not None:
        try:
            current_content = paths[0].read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
        if current_content != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None
    for path in paths:
        content = current_content if current_content is not None else path.read_text(encoding="utf-8")
        if action == "start-processing":
            _require_type(path, content)
            # `--target-repo`未指定でもtarget_repo欠落とfrontmatter解析不能を拒否するため、
            # 不一致判定を`_verify_target_repo_content`へ委ねる一方でこの必須検査は残す。
            _entry_target_repo(path, content)
        _verify_target_repo_content(path, content, normalized_target_repo)
    if cooldown_days is not None:
        non_feedback = [
            path.name for path in paths if _require_type(path, path.read_text(encoding="utf-8")) != MQ_TYPE_FEEDBACK
        ]
        if non_feedback:
            raise WebInputError(f"--`cooldown-days`はフィードバック専用です: {', '.join(non_feedback)}")
    if action == "remove" and not force:
        protected = [path.name for path in paths if path.parent.name == MQ_STATE_PROCESSING]
        if protected:
            print(
                "processing状態のファイルは既定で削除を保護します。"
                f"削除するには--force（Web APIはforce指定）を指定してください: {', '.join(protected)}",
                file=sys.stderr,
            )
            sys.exit(2)
    return current_content


def _update_transition_metadata(
    paths: list[pathlib.Path],
    *,
    action: str,
    now: datetime.datetime,
    cooldown_days: int | None,
) -> None:
    """active状態間の移動前にcooldownメタデータを更新する。"""
    if action not in {"start-processing", "return-to-inbox"}:
        return
    for path in paths:
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            continue
        data, body = parsed
        if action == "return-to-inbox" and cooldown_days is not None:
            deadline = now.astimezone(datetime.UTC) + datetime.timedelta(days=cooldown_days)
            data["cooldown_until"] = deadline.isoformat()
        else:
            data.pop("cooldown_until", None)
        updated = _frontmatter.serialize_frontmatter(data, body)
        if updated != text:
            _atomic_write_text(path, updated)


def _apply_transition(
    private_notes: pathlib.Path,
    paths: list[pathlib.Path],
    *,
    action: str,
    now: datetime.datetime,
    note: str | None,
    commit_values: dict[pathlib.Path, str | None],
    cooldown_days: int | None,
) -> None:
    """検証済みエントリを削除又は目的状態へ移動する。"""
    destination_name = {
        "start-processing": MQ_STATE_PROCESSING,
        "return-to-inbox": MQ_STATE_INBOX,
        "adopt": MQ_STATE_ADOPTED,
        "reject": MQ_STATE_REJECTED,
    }.get(action)
    if destination_name is None:
        for path in paths:
            path.unlink()
        return
    destination = _subdir(private_notes, destination_name)
    conflicts = [path.name for path in paths if (destination / path.name).exists()]
    if conflicts:
        print(
            f"移動先（{destination_name}）に同名エントリが既に存在します: {', '.join(conflicts)}",
            file=sys.stderr,
        )
        sys.exit(2)
    _update_transition_metadata(paths, action=action, now=now, cooldown_days=cooldown_days)
    for path in paths:
        if action in {"adopt", "reject"}:
            _stamp_result(path, outcome=destination_name, now=now, commit=commit_values[path], note=note)
        shutil.move(path, destination / path.name)


def _transition_commit_message(action: str, count: int, note: str | None) -> str:
    """状態遷移の管理repo用コミットメッセージを返す。"""
    item_word = "entry" if count == 1 else "entries"
    note_suffix = f" (理由: {note})" if action == "remove" and note else ""
    return {
        "start-processing": f"chore: start processing {count} {item_word}",
        "return-to-inbox": f"chore: return {count} {item_word} to inbox",
        "adopt": f"chore: process {count} {item_word} (adopted)",
        "reject": f"chore: process {count} {item_word} (rejected)",
        "remove": f"chore: remove {count} {item_word}{note_suffix}",
    }[action]


def transition_entries(
    private_notes: pathlib.Path,
    *,
    action: str,
    filenames: list[str],
    now: datetime.datetime,
    target_repo: str | None = None,
    note: str | None = None,
    commit: str | None = None,
    lock_timeout: float = -1,
    force: bool = False,
    state: str | None = None,
    expected_content: str | None = None,
    cooldown_days: int | None = None,
    local_worktree: pathlib.Path | None = None,
) -> list[str]:
    """平引数でエントリの一括状態遷移又は削除を実行する。

    `action="remove"`かつ`force=False`（既定）の場合、processing状態のファイルが
    対象に含まれるとexit 2で拒否する（`atk mq rm`の既定保護。処理中ファイルの
    意図しない削除を防ぐ。解除するには`force=True`を渡す）。
    """
    _validate_transition_options(
        action,
        filenames,
        state=state,
        expected_content=expected_content,
        cooldown_days=cooldown_days,
    )
    inbox_dir = private_notes / MQ_STATE_INBOX
    _validate_filenames_only(filenames, inbox_dir)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        missing_is_conflict = action == "remove" and expected_content is not None
        paths = _resolve_transition_paths(
            private_notes,
            action,
            filenames,
            state,
            missing_is_conflict=missing_is_conflict,
        )
        _validate_transition_targets(
            paths,
            action=action,
            target_repo=target_repo,
            expected_content=expected_content,
            cooldown_days=cooldown_days,
            force=force,
        )
        commit_values = (
            _commit_values_by_path(paths, commit, local_worktree)
            if action in {"adopt", "reject"}
            else {path: commit for path in paths}
        )
        _apply_transition(
            private_notes,
            paths,
            action=action,
            now=now,
            note=note,
            commit_values=commit_values,
            cooldown_days=cooldown_days,
        )
        _commit_and_push(private_notes, _transition_commit_message(action, len(paths), note), list(MQ_STATES))
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
    """平引数でフィードバック本文を更新する。

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


def append_entry_content(
    private_notes: pathlib.Path,
    *,
    state: str,
    filename: str,
    content: bytes,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    expected_content: bytes | None = None,
) -> bool:
    """フィードバック本文をraw bytesのまま追記する。TBDは拒否する。"""
    if state not in {MQ_STATE_INBOX, MQ_STATE_PROCESSING}:
        raise WebInputError("追記可能状態はinbox又はprocessingです")

    directory = private_notes / state
    path = directory / filename

    def validate(previous: str, updated: str) -> None:
        _validate_no_reserved_frontmatter_modification(previous, updated)
        if _require_type(path, previous) == MQ_TYPE_TBD:
            raise WebInputError("TBDには追記できません")

    return _append_entry(
        private_notes,
        directory=directory,
        filename=filename,
        content=content,
        target_repo=target_repo,
        lock_timeout=lock_timeout,
        expected_content=expected_content,
        commit_message="chore: append feedback item",
        content_validator=validate,
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
            raise WebInputError(f"フィードバックでは指定できないメタデータです: {', '.join(tbd_only_keys)}")

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


def _legacy_entry_dependencies_for_conversion(data: dict[str, object], filename: str) -> tuple[str, ...]:
    """変換時に意味を保てる旧形式の依存先を返す。"""
    schedule = data.get("queue_schedule")
    if not isinstance(schedule, dict):
        return ()
    dependency = schedule.get("dependency")
    if not isinstance(dependency, dict) or dependency.get("kind") in (None, "none"):
        return ()
    if dependency.get("kind") != "entries":
        raise WebInputError(f"旧形式の依存を計画実装型へ移行できません: {filename}")
    filenames = dependency.get("filenames")
    if not isinstance(filenames, list) or not filenames or any(not isinstance(value, str) or not value for value in filenames):
        raise WebInputError(f"旧形式の依存が不正なため変換できません: {filename}")
    return tuple(dict.fromkeys(filenames))


def convert_entry_to_plan(
    private_notes: pathlib.Path,
    *,
    filename: str,
    plan_file: str,
    depends_on: tuple[str, ...] | None = None,
    target_repo: str | None = None,
    lock_timeout: float = -1,
) -> dict[str, object | None]:
    """既存フィードバックを計画実装型へ変換し、保存済みメタデータを返す。"""
    plan_path = pathlib.Path(plan_file)
    if not plan_path.is_absolute():
        raise WebInputError("plan_fileは絶対パスで指定してください")
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    _validate_filenames_only([filename, *(depends_on or ())], inbox_dir)
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
            raise WebInputError(f"フィードバックだけを計画実装型へ変換できます: {path.name}")
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
        if depends_on is None and "depends_on" not in data:
            legacy_dependencies = _legacy_entry_dependencies_for_conversion(data, path.name)
            if legacy_dependencies:
                data["depends_on"] = list(legacy_dependencies)
        data["plan_file"] = str(plan_path)
        data.pop("queue_schedule", None)
        if depends_on is not None:
            canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in depends_on))
            if path.name in canonical_dependencies:
                raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
            dependency_graph = _active_dependency_graph(inbox_dir, processing_dir)
            dependency_graph[path.name] = set(canonical_dependencies)
            if any(_dependency_reaches(dependency_graph, dependency, path.name) for dependency in canonical_dependencies):
                raise WebInputError(f"循環する依存を指定できません: {path.name}")
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
    target_repo, _local_worktree = _add.resolve_add_target(args.target_repo)
    try:
        details = convert_entry_to_plan(
            private_notes,
            filename=args.filename,
            plan_file=args.plan_file,
            depends_on=tuple(args.depends_on) if args.depends_on is not None else None,
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
    """既存フィードバックの明示依存だけを更新し、保存済みメタデータを返す。"""
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
            raise WebInputError(f"フィードバックだけ依存を更新できます: {path.name}")
        raw_entry_repo = data.get("target_repo")
        if not isinstance(raw_entry_repo, str):
            raise WebInputError(f"target_repoが不正です: {path.name}")
        entry_repo = _resolve_repo_id(raw_entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")

        canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in depends_on))
        if path.name in canonical_dependencies:
            raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
        dependency_graph = _active_dependency_graph(inbox_dir, processing_dir)
        dependency_graph[path.name] = set(canonical_dependencies)
        if any(_dependency_reaches(dependency_graph, dependency, path.name) for dependency in canonical_dependencies):
            raise WebInputError(f"循環する依存を指定できません: {path.name}")
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


def _active_dependency_graph(inbox_dir: pathlib.Path, processing_dir: pathlib.Path) -> dict[str, set[str]]:
    """ロック内で取得した`active`なフィードバックの依存グラフを返す。"""
    entries: dict[str, pathlib.Path] = {}
    for directory in (inbox_dir, processing_dir):
        if directory.is_dir():
            entries.update({path.name: path for path in directory.glob("*.md") if path.is_file()})
    graph: dict[str, set[str]] = {}
    for name, entry_path in entries.items():
        entry_text = entry_path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(entry_text)
        if parsed is None:
            raise WebInputError(f"active項目のfrontmatterが破損しているため依存を更新できません: {name}")
        data, _body = parsed
        if _require_type(entry_path, entry_text) != MQ_TYPE_FEEDBACK:
            continue
        raw_dependencies = data.get("depends_on", [])
        if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
            raise WebInputError(f"active項目のdepends_onが不正なため依存を更新できません: {name}")
        graph[name] = {_validate_filename(value, inbox_dir).name for value in raw_dependencies}
    return graph


def _dependency_reaches(graph: dict[str, set[str]], start: str, target: str) -> bool:
    """startからtargetへ到達できる場合に真を返す。"""
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, ()))
    return False


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
    local_worktree = _candidate_local_worktree(args.target_repo) if args.commit is not None else None
    filenames = transition_entries(
        private_notes,
        action="adopt",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
        local_worktree=local_worktree,
    )
    print(f"{len(filenames)}件採用処理: {', '.join(filenames)}")


def _cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """rejectサブコマンド: 不採用としてinboxまたはprocessingからrejected/へ移動しcommit・push。

    移動前に対象ファイル末尾へ`## 処理結果`節を追記する（`--note`・`--commit`が指定された場合のみ該当項目を含む）。
    inbox・processingいずれの起点も許容し、両方に同名ファイルがある場合はprocessingを優先する。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "reject")
    local_worktree = _candidate_local_worktree(args.target_repo) if args.commit is not None else None
    filenames = transition_entries(
        private_notes,
        action="reject",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
        state=MQ_STATE_INBOX if args.if_inbox else None,
        local_worktree=local_worktree,
    )
    print(f"{len(filenames)}件不採用処理: {', '.join(filenames)}")


def _cmd_start_processing(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
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
        now=now,
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件処理開始: {', '.join(filenames)}")


def _cmd_return_to_inbox(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
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
        now=now,
        target_repo=args.target_repo,
        cooldown_days=args.cooldown_days,
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
            skip_pull=args.skip_pull,
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
    if args.append:
        if args.filename is None or message is None:
            args.subparser.error("--appendではFILENAMEとMESSAGEを指定してください。")
        _cmd_append(args, private_notes)
        return
    if message is not None and args.filename is None:
        args.subparser.error("MESSAGEを指定する場合はFILENAMEも指定してください。")
    if message is not None:
        try:
            _add.reject_message_file_path(
                message,
                file_input_hint="ファイル内容を本文にする場合はMESSAGEを省略し、エディターで貼り付けてください。",
            )
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
        snapshot = path.read_bytes()
        normalized_target_repo = _resolve_repo_id(args.target_repo) if args.target_repo is not None else None
        _verify_target_repo_content(path, snapshot.decode("utf-8"), normalized_target_repo)
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


def _cmd_append(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """Edit --appendサブコマンド: 既存raw bytesを保ってMESSAGEを末尾へ追記する。"""
    assert args.filename is not None
    assert args.message is not None
    try:
        _add.reject_message_file_path(
            args.message,
            file_input_hint="MESSAGEには追記する本文を指定してください。",
        )
    except WebInputError as error:
        print(f"追記を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)

    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    with _repo_lock(private_notes):
        _validate_filenames_only([args.filename], inbox_dir)
        _pull(private_notes)
        path = _resolve_processable_targets([args.filename], inbox_dir, processing_dir)[0]
        snapshot = path.read_bytes()
        normalized_target_repo = _resolve_repo_id(args.target_repo) if args.target_repo is not None else None
        _verify_target_repo_content(path, snapshot.decode("utf-8"), normalized_target_repo)

    content = snapshot + b"\n\n" + args.message.encode("utf-8")
    try:
        append_entry_content(
            private_notes,
            state=path.parent.name,
            filename=path.name,
            content=content,
            target_repo=args.target_repo,
            expected_content=snapshot,
        )
    except RuntimeError:
        print(
            f"追記中に他プロセスが対象を変更しました: {path.name}。"
            "指定したMESSAGEは反映されていません。同じFILENAMEとMESSAGEで再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"追記反映: {path.name}")


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のinbox・processing配下未コミット変更をコミット・push。

    inbox・processing配下に未コミット変更が無い場合は早期return。
    """
    if commit_entries(private_notes):
        print("外部編集分をコミット・pushしました。")
    else:
        print("差分なし。")
