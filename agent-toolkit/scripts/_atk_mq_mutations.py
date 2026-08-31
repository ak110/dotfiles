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
import _atk_mq_user_comment as _user_comment
import _plan_file
import _plan_format
from _atk_mq_common import (
    MQ_PROCESSABLE_STATES,
    MQ_STATE_ADOPTED,
    MQ_STATE_HOLD,
    MQ_STATE_INBOX,
    MQ_STATE_PLANNING,
    MQ_STATE_PROCESSING,
    MQ_STATE_REJECTED,
    MQ_STATES,
    MQ_TYPE_FEEDBACK,
    MQ_TYPE_TBD,
    TRANSITION_EXPLICIT_STATES,
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
    is_agent_environment,
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
_AGENT_USER_COMMENT_ERROR = (
    "ユーザーコメントはユーザーだけが書き込みます。エージェント環境から起動したatkではユーザーコメントを変更できません。"
)


def _reject_agent_user_comment_change(original: str, updated: str) -> bool:
    """エージェント環境からのユーザーコメント変更を拒否した場合に真を返す。"""
    if not is_agent_environment():
        return False
    try:
        changed = _user_comment.extract_user_comment(original) != _user_comment.extract_user_comment(updated)
    except _user_comment.UserCommentError:
        changed = True
    if not changed:
        return False
    print(_AGENT_USER_COMMENT_ERROR, file=sys.stderr)
    return True


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

    ユーザーが$EDITORまたはWeb APIで内部管理用frontmatterを書き換えることを防ぐ。
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
    if action not in {"start-planning", "start-processing", "return-to-inbox", "hold", "unhold", "adopt", "reject", "remove"}:
        raise WebInputError(f"未知のエントリ操作です: {action}")
    if cooldown_days is not None and (action != "return-to-inbox" or cooldown_days < 3):
        raise WebInputError("cooldown_daysはreturn-to-inboxで3以上を指定してください")
    accepted_states = TRANSITION_EXPLICIT_STATES.get(action, ())
    state_is_valid = state in accepted_states
    if state is not None and not state_is_valid:
        rendered_states = "、".join(accepted_states) if accepted_states else "なし"
        raise WebInputError(f"操作{action}はstate={state}を受理しません。明示stateとして受理する状態: {rendered_states}")
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
    if action == "start-planning":
        return _resolve_feedback_targets(filenames, inbox_dir, missing_is_conflict=missing_is_conflict)
    if action == "start-processing":
        return _resolve_feedback_targets(filenames, inbox_dir, missing_is_conflict=missing_is_conflict)
    if action == "return-to-inbox":
        return _resolve_feedback_targets(filenames, processing_dir, missing_is_conflict=missing_is_conflict)
    if action == "unhold":
        return _resolve_feedback_targets(
            filenames,
            _subdir(private_notes, MQ_STATE_HOLD),
            missing_is_conflict=missing_is_conflict,
        )
    if action == "remove":
        return _resolve_removable_targets(
            filenames,
            inbox_dir,
            processing_dir,
            _subdir(private_notes, MQ_STATE_PLANNING),
            missing_is_conflict=missing_is_conflict,
        )
    return _resolve_processable_targets(filenames, inbox_dir, processing_dir, missing_is_conflict=missing_is_conflict)


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
        if action in {"start-planning", "start-processing"}:
            entry_type = _require_type(path, content)
            # `--target-repo`未指定でもtarget_repo欠落とfrontmatter解析不能を拒否するため、
            # 不一致判定を`_verify_target_repo_content`へ委ねる一方でこの必須検査は残す。
            _entry_target_repo(path, content)
            if action == "start-planning":
                if entry_type != MQ_TYPE_FEEDBACK:
                    raise WebInputError(f"通常型フィードバックだけをplanningへ移動できます: {path.name}")
                parsed = _frontmatter.parse_frontmatter(content)
                if parsed is None or "plan_file" in parsed[0]:
                    raise WebInputError(f"既存の計画型フィードバックはplanningへ移動できません: {path.name}")
        _verify_target_repo_content(path, content, normalized_target_repo)
        if (
            path.parent.name == MQ_STATE_PLANNING
            and action == "return-to-inbox"
            and _require_type(path, content) != MQ_TYPE_FEEDBACK
        ):
            raise WebInputError(f"TBDをplanningから差し戻すことはできません: {path.name}")
    if action == "start-planning":
        repositories = {_entry_target_repo(path, path.read_text(encoding="utf-8")) for path in paths}
        if len(repositories) != 1:
            raise WebInputError("start-planningの対象は同一target_repoで指定してください")
    if cooldown_days is not None:
        non_feedback = [
            path.name for path in paths if _require_type(path, path.read_text(encoding="utf-8")) != MQ_TYPE_FEEDBACK
        ]
        if non_feedback:
            raise WebInputError(f"--`cooldown-days`はフィードバック専用です: {', '.join(non_feedback)}")
    if action == "remove" and not force:
        protected = [path.name for path in paths if path.parent.name in {MQ_STATE_PLANNING, MQ_STATE_PROCESSING}]
        if protected:
            print(
                "planning・processing状態のファイルは既定で削除を保護します。"
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


def _strip_result_section(path: pathlib.Path) -> None:
    """末尾にある最後の`## 処理結果`節を取り除く。"""
    text = path.read_text(encoding="utf-8")
    matches = tuple(re.finditer(r"(?m)^## 処理結果[ \t]*\r?\n", text))
    if not matches:
        return
    updated = text[: matches[-1].start()].rstrip() + "\n"
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
        "start-planning": MQ_STATE_PLANNING,
        "start-processing": MQ_STATE_PROCESSING,
        "return-to-inbox": MQ_STATE_INBOX,
        "hold": MQ_STATE_HOLD,
        "unhold": MQ_STATE_INBOX,
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
        if action == "return-to-inbox":
            _strip_result_section(path)
        if action in {"adopt", "reject"}:
            _stamp_result(path, outcome=destination_name, now=now, commit=commit_values[path], note=note)
        shutil.move(path, destination / path.name)


def _transition_commit_message(action: str, count: int, note: str | None) -> str:
    """状態遷移の管理repo用コミットメッセージを返す。"""
    item_word = "entry" if count == 1 else "entries"
    note_suffix = f" (理由: {note})" if action == "remove" and note else ""
    return {
        "start-planning": f"chore: start planning {count} {item_word}",
        "start-processing": f"chore: start processing {count} {item_word}",
        "return-to-inbox": f"chore: return {count} {item_word} to inbox",
        "hold": f"chore: hold {count} {item_word}",
        "unhold": f"chore: unhold {count} {item_word}",
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
    skip_push: bool = False,
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
        if not skip_push:
            _push_pending_commits(private_notes)
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
        for state_name in MQ_STATES:
            _subdir(private_notes, state_name)
        _commit_and_push(
            private_notes,
            _transition_commit_message(action, len(paths), note),
            list(MQ_STATES),
            skip_push=skip_push,
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
    """平引数でフィードバック本文を更新する。

    保存前に新旧frontmatterを比較し、内部管理用予約キーの追加・変更・削除を禁止する。
    """
    if state not in {MQ_STATE_INBOX, MQ_STATE_PROCESSING, MQ_STATE_HOLD}:
        raise WebInputError("編集可能状態はinbox、processing又はholdです")

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
    if state not in {MQ_STATE_INBOX, MQ_STATE_PROCESSING, MQ_STATE_HOLD}:
        raise WebInputError("追記可能状態はinbox、processing又はholdです")

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


def _read_plan_input_filenames(plan_path: pathlib.Path) -> tuple[str, ...]:
    """正規パーサーで検証した`提示素材`からキュー項目名を返す。"""
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError as error:
        raise WebInputError(f"plan_fileを読み込めません: {plan_path}") from error
    except UnicodeError as error:
        raise WebInputError(f"plan_fileをUTF-8として読み込めません: {plan_path}") from error

    materials, errors = _plan_format.parse_plan_materials(text)
    if errors:
        raise WebInputError("計画の提示素材が不正です: " + "; ".join(errors))
    if materials is None:
        raise WebInputError("計画の提示素材を解析できません")
    filenames = materials.material_paths if materials.is_human_readable else materials.feedback_queue_ids
    return tuple(sorted(filenames))


def _plan_feedback_paths(
    private_notes: pathlib.Path,
    filenames: tuple[str, ...],
) -> tuple[pathlib.Path, ...]:
    """提示素材を検証し、planningにある変換元feedbackだけを返す。"""
    feedback_paths: list[pathlib.Path] = []
    for filename in filenames:
        candidates = tuple((state, private_notes / state / filename) for state in MQ_STATES)
        existing = tuple((state, path) for state, path in candidates if path.is_file())
        if len(existing) != 1:
            raise WebInputError(f"計画の提示素材を一意に特定できません: {filename}")
        state, path = existing[0]
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        if parsed is None:
            raise WebInputError(f"計画の提示素材のfrontmatterが破損しています: {filename}")
        entry_type = parsed[0].get("type")
        if entry_type == MQ_TYPE_FEEDBACK:
            if state != MQ_STATE_PLANNING:
                raise WebInputError(f"変換元feedbackがplanningに存在しません: {filename}")
            if "plan_file" in parsed[0]:
                raise WebInputError(f"計画の提示素材が既に計画型です: {filename}")
            feedback_paths.append(path)
            continue
        if entry_type == MQ_TYPE_TBD:
            if state not in MQ_PROCESSABLE_STATES:
                raise WebInputError(f"計画の提示素材TBDがactive状態ではありません: {filename}")
            continue
        raise WebInputError(f"計画の提示素材のtypeが不正です: {filename}")
    if not feedback_paths:
        raise WebInputError("計画の提示素材に変換元feedbackがありません")
    return tuple(feedback_paths)


def _resolve_plan_base_commit(plan_path: pathlib.Path, local_worktree: pathlib.Path) -> str:
    """計画メタ情報の一意なベースコミットを対象作業ツリーで解決する。"""
    try:
        content = plan_path.read_text(encoding="utf-8")
    except OSError as error:
        raise WebInputError(f"plan_fileを読み込めません: {plan_path}") from error
    except UnicodeError as error:
        raise WebInputError(f"plan_fileをUTF-8として読み込めません: {plan_path}") from error
    metadata, errors = _plan_format.parse_plan_metadata(content)
    if errors:
        raise WebInputError("計画メタ情報を一意に解析できません: " + "; ".join(errors))
    if metadata is None:
        raise WebInputError("計画メタ情報にベースコミットがありません")
    candidates = tuple(dict.fromkeys(metadata.base_commit_candidates))
    if len(candidates) != 1:
        raise WebInputError("計画メタ情報のベースコミットを一意に特定できません")
    candidate = candidates[0].lower()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", candidate) is None:
        raise WebInputError("計画メタ情報のベースコミットは完全OIDで指定してください")
    return _resolve_commit(local_worktree, candidate)


def _entry_dependencies(path: pathlib.Path, data: dict[str, object]) -> tuple[str, ...]:
    """エントリの依存先を文字列列として検証して返す。"""
    raw_dependencies = data.get("depends_on", [])
    if not isinstance(raw_dependencies, list) or not all(isinstance(value, str) for value in raw_dependencies):
        raise WebInputError(f"depends_onが不正です: {path.name}")
    return tuple(raw_dependencies)


def edit_entry_to_plan(
    private_notes: pathlib.Path,
    *,
    filename: str,
    content: str,
    plan_file: str,
    target_commit: str,
    depends_on: tuple[str, ...] = (),
    target_repo: str | None = None,
    lock_timeout: float = -1,
    expected_content: str | None = None,
) -> dict[str, object | None]:
    """planningの最古項目を計画型feedbackへ編集し、inboxへ原子的に移動する。"""
    try:
        plan_path = _plan_file.resolve_plan_file(plan_file, private_notes=private_notes)
        stored_plan_file = _plan_file.normalize_plan_file(plan_file, private_notes=private_notes)
    except ValueError as error:
        raise WebInputError(f"plan_fileを解決できません: {plan_file}（{error}）") from error
    try:
        if not plan_path.is_file():
            raise WebInputError(f"plan_fileが実在する通常ファイルではありません: {plan_file}")
    except OSError as error:
        raise WebInputError(f"plan_fileを検証できません: {plan_file}") from error
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target_commit) is None:
        raise WebInputError("target_commitは40桁または64桁の完全OIDで指定してください")

    inbox_dir = private_notes / MQ_STATE_INBOX
    _validate_filenames_only([filename, *depends_on], inbox_dir)
    normalized_filename = _validate_filename(filename, inbox_dir).name
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        material_names = _read_plan_input_filenames(plan_path)
        normalized_material_names = tuple(dict.fromkeys(_validate_filename(name, inbox_dir).name for name in material_names))
        if not normalized_material_names:
            raise WebInputError("計画の提示素材に変換元feedbackがありません")
        if normalized_filename not in normalized_material_names:
            raise WebInputError(f"指定項目が計画の提示素材に含まれません: {normalized_filename}")
        material_paths = _plan_feedback_paths(private_notes, normalized_material_names)
        feedback_names = tuple(path.name for path in material_paths)
        if normalized_filename not in feedback_names:
            raise WebInputError(f"指定項目が計画の変換元feedbackに含まれません: {normalized_filename}")
        oldest_material = min(feedback_names)
        if normalized_filename != oldest_material:
            raise WebInputError(f"計画型へ変換できるのは変換元feedbackの昇順最古だけです: {oldest_material}")
        planning_path = _validate_filename(normalized_filename, private_notes / MQ_STATE_PLANNING)
        previous = planning_path.read_text(encoding="utf-8")
        if expected_content is not None and previous != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        _verify_target_repo_content(planning_path, previous, normalized_target_repo)
        parsed = _frontmatter.parse_frontmatter(previous)
        if parsed is None:
            raise WebInputError(f"frontmatterが破損しているため計画型へ編集できません: {planning_path.name}")
        stored_data, _stored_body = parsed
        if _require_type(planning_path, previous) != MQ_TYPE_FEEDBACK:
            raise WebInputError(f"フィードバックだけを計画型へ編集できます: {planning_path.name}")
        if "plan_file" in stored_data:
            raise WebInputError(f"既に計画型のため再変換できません: {planning_path.name}")

        material_repositories: set[str] = set()
        dependencies: list[str] = []
        for material_path in material_paths:
            material_text = material_path.read_text(encoding="utf-8")
            material_parsed = _frontmatter.parse_frontmatter(material_text)
            if material_parsed is None:
                raise WebInputError(f"変換元feedbackのfrontmatterが破損しています: {material_path.name}")
            material_data, _material_body = material_parsed
            material_repo = _entry_target_repo(material_path, material_text)
            material_repositories.add(material_repo)
            dependencies.extend(
                _validate_filename(value, inbox_dir).name for value in _entry_dependencies(material_path, material_data)
            )
        if len(material_repositories) != 1:
            raise WebInputError("計画の提示素材は同一target_repoである必要があります")
        material_repo = next(iter(material_repositories))
        if normalized_target_repo is not None and material_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: 期待={normalized_target_repo} 実際={material_repo}")

        message_frontmatter, message_body = _add.parse_entry_message(content, entry_type=MQ_TYPE_FEEDBACK)
        requested_type = message_frontmatter.get("type")
        if requested_type is not None and requested_type != MQ_TYPE_FEEDBACK:
            raise WebInputError(f"計画型編集のtypeはfeedbackで指定してください: {planning_path.name}")
        for key in ("target_commit", "depends_on", "plan_file", "queue_schedule", "cooldown_until"):
            if key in message_frontmatter:
                raise WebInputError(f"{key}は計画型編集が管理する予約キーです")
        updates = dict(message_frontmatter)
        if "target_repo" in updates:
            raw_target_repo = updates["target_repo"]
            if not isinstance(raw_target_repo, str):
                raise WebInputError("target_repoは文字列で指定してください")
            updates["target_repo"] = _resolve_repo_id(raw_target_repo)
            if updates["target_repo"] != material_repo:
                raise WebInputError(f"target_repoが一致しません: 期待={material_repo} 実際={updates['target_repo']}")

        updated_data = {**stored_data, **updates}
        updated_data["source"] = "plan-and-add-feedback"
        updated_data["plan_file"] = stored_plan_file
        updated_data["target_commit"] = target_commit
        updated_data.pop("queue_schedule", None)
        updated_data.pop("cooldown_until", None)
        all_dependencies = dependencies + list(depends_on)
        canonical_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in all_dependencies))
        excluded_inputs = set(feedback_names)
        canonical_dependencies = tuple(
            value for value in canonical_dependencies if value not in excluded_inputs and value != planning_path.name
        )
        dependency_graph = _active_dependency_graph(
            inbox_dir,
            _subdir(private_notes, MQ_STATE_PROCESSING),
            _subdir(private_notes, MQ_STATE_PLANNING),
        )
        dependency_graph[planning_path.name] = set(canonical_dependencies)
        if any(_dependency_reaches(dependency_graph, dependency, planning_path.name) for dependency in canonical_dependencies):
            raise WebInputError(f"循環する依存を指定できません: {planning_path.name}")
        if canonical_dependencies:
            updated_data["depends_on"] = list(canonical_dependencies)
        else:
            updated_data.pop("depends_on", None)
        updated_text = _frontmatter.serialize_frontmatter(
            updated_data,
            "\n" + message_body.strip("\n").rstrip() + "\n",
        )

        inbox_path = inbox_dir / planning_path.name
        if inbox_path.exists():
            raise WebInputError(f"inboxに同名エントリが既に存在します: {planning_path.name}")
        _atomic_write_text(inbox_path, updated_text)
        planning_path.unlink()
        _commit_and_push(
            private_notes,
            "chore: convert feedback item to plan",
            [str(planning_path.relative_to(private_notes)), str(inbox_path.relative_to(private_notes))],
        )
        return _add._read_saved_entry_details(inbox_path)  # pylint: disable=protected-access


def commit_entries(private_notes: pathlib.Path, *, lock_timeout: float = -1) -> bool:
    """平引数でinbox・processing配下の外部編集差分をcommit・pushする。

    対象配下に差分がない場合も滞留commitをpushし、外部編集によるcommitを行ったかを返す。
    """
    with _repo_lock(private_notes, timeout=lock_timeout):
        _push_pending_commits(private_notes)
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
            _push_pending_commits(private_notes)
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


def _resolve_conversion_targets(
    filenames: tuple[str, ...],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    planning_dir: pathlib.Path,
) -> tuple[str, list[pathlib.Path]]:
    """convert-to-planの入力を状態ごとに解決し、混在を拒否する。"""
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        inbox_path = _validate_filename(name, inbox_dir)
        processing_path = _validate_filename(inbox_path.name, processing_dir)
        planning_path = _validate_filename(inbox_path.name, planning_dir)
        if planning_path.exists():
            if inbox_path.exists() or processing_path.exists():
                raise WebInputError(f"異なる状態の同名項目が存在するため変換できません: {planning_path.name}")
            resolved.append(planning_path)
        elif processing_path.exists():
            resolved.append(processing_path)
        elif inbox_path.exists():
            resolved.append(inbox_path)
        else:
            missing.append(inbox_path.name)
    if missing:
        for name in missing:
            print(f"inbox・processing・planningのいずれにも存在しません: {name}", file=sys.stderr)
        sys.exit(2)
    states = {path.parent.name for path in resolved}
    if len(states) != 1:
        raise WebInputError("異なる状態の入力を混在させて変換できません")
    return next(iter(states)), resolved


def _resolve_removable_targets(
    filenames: list[str],
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    planning_dir: pathlib.Path,
    *,
    missing_is_conflict: bool = False,
) -> list[pathlib.Path]:
    """rmの対象をprocessing、planning、inboxの優先順で解決する。"""
    resolved: list[pathlib.Path] = []
    missing: list[str] = []
    for name in filenames:
        normalized = _validate_filename(name, inbox_dir).name
        candidates = (
            processing_dir / normalized,
            planning_dir / normalized,
            inbox_dir / normalized,
        )
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            missing.append(normalized)
        else:
            resolved.append(path)
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        for name in missing:
            print(f"inbox・processingのいずれにも存在しません（planningも探索対象です）: {name}", file=sys.stderr)
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


def _entry_dependencies_for_conversion(path: pathlib.Path, data: dict[str, object]) -> tuple[str, ...]:
    """変換時にトップレベル又は意味を保てる旧形式の依存先を返す。"""
    if "depends_on" in data:
        return _entry_dependencies(path, data)
    schedule = data.get("queue_schedule")
    if not isinstance(schedule, dict):
        return ()
    dependency = schedule.get("dependency")
    if not isinstance(dependency, dict) or dependency.get("kind") in (None, "none"):
        return ()
    if dependency.get("kind") != "entries":
        raise WebInputError(f"旧形式の依存を計画実装型へ移行できません: {path.name}")
    filenames = dependency.get("filenames")
    if not isinstance(filenames, list) or not filenames or any(not isinstance(value, str) or not value for value in filenames):
        raise WebInputError(f"旧形式の依存が不正なため変換できません: {path.name}")
    return tuple(dict.fromkeys(filenames))


def _git_head(private_notes: pathlib.Path) -> str:
    """管理repoのHEADを完全OIDで返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=private_notes,
        capture_output=True,
        text=True,
        check=True,
    )
    commit = result.stdout.strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        raise RuntimeError(f"管理repoのHEADが完全OIDではありません: {commit!r}")
    return commit


def _assert_conversion_worktree_clean(private_notes: pathlib.Path) -> None:
    """計画変換前に管理repoの作業ツリーとindex全体がcleanであることを確認する。"""
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=private_notes,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise WebInputError("計画変換前に管理repoの作業ツリーとindexをcleanにしてください")


def _assert_conversion_targets_tracked(
    private_notes: pathlib.Path,
    paths: list[pathlib.Path],
) -> None:
    """計画変換対象が開始時HEADに登録済みであることを確認する。"""
    relative_paths = [str(path.relative_to(private_notes)) for path in paths]
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *relative_paths],
        cwd=private_notes,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WebInputError("計画変換対象が管理repoの開始時HEADに登録されていません")


def _restore_conversion_paths(
    private_notes: pathlib.Path,
    start_head: str,
    relative_paths: tuple[str, ...],
    remove_relative_paths: tuple[str, ...] = (),
) -> None:
    """commit前失敗時に変換対象だけを開始時のHEADへ戻す。"""
    try:
        if _git_head(private_notes) != start_head:
            return
        subprocess.run(["git", "reset", "--mixed", start_head], cwd=private_notes, check=True)
        tracked = subprocess.run(
            ["git", "ls-files", "--", *relative_paths],
            cwd=private_notes,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if tracked:
            subprocess.run(
                ["git", "restore", f"--source={start_head}", "--staged", "--worktree", "--", *tracked],
                cwd=private_notes,
                check=True,
            )
        for relative_path in remove_relative_paths:
            (private_notes / relative_path).unlink(missing_ok=True)
        for relative_path in (*relative_paths, *remove_relative_paths):
            status = subprocess.run(
                ["git", "status", "--porcelain", "--", relative_path],
                cwd=private_notes,
                capture_output=True,
                text=True,
                check=True,
            )
            if status.stdout.strip():
                raise RuntimeError(f"計画変換対象の復元後に差分が残っています: {relative_path}")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"計画変換対象の復元に失敗しました。手動確認が必要です: {error}", file=sys.stderr)


def _convert_planning_entries(
    private_notes: pathlib.Path,
    *,
    paths: list[pathlib.Path],
    plan_path: pathlib.Path,
    message: str,
    normalized_dependencies: tuple[str, ...],
    normalized_target_repo: str | None,
    local_worktree: pathlib.Path | None,
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    planning_dir: pathlib.Path,
    skip_push: bool,
) -> dict[str, object]:
    """planningの全入力を最古の1件へ統合し、inboxへ原子的に保存する。"""
    material_names = _read_plan_input_filenames(plan_path)
    normalized_material_names = tuple(_validate_filename(name, inbox_dir).name for name in material_names)
    if len(set(normalized_material_names)) != len(normalized_material_names):
        raise WebInputError("計画の提示素材に重複したファイル名があります")
    material_paths = _plan_feedback_paths(private_notes, normalized_material_names)
    input_names = tuple(path.name for path in paths)
    feedback_names = tuple(path.name for path in material_paths)
    if tuple(sorted(input_names)) != tuple(sorted(feedback_names)):
        raise WebInputError("convert-to-planの入力と計画の提示素材が一致しません")
    if local_worktree is None:
        raise WebInputError("planningの変換には対象リポジトリのローカルworktreeが必要です")
    _assert_conversion_targets_tracked(private_notes, paths)
    snapshots = [(path, path.read_text(encoding="utf-8")) for path in sorted(paths, key=lambda item: item.name)]

    repositories: set[str] = set()
    dependencies: list[str] = []
    parsed_entries: dict[pathlib.Path, tuple[dict[str, object], str]] = {}
    for path, text in snapshots:
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
        repositories.add(entry_repo)
        if normalized_target_repo is not None and entry_repo != normalized_target_repo:
            raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")
        if "plan_file" in data:
            raise WebInputError(f"既に計画型のため再変換できません: {path.name}")
        entry_dependencies = _entry_dependencies_for_conversion(path, data)
        dependencies.extend(_validate_filename(value, inbox_dir).name for value in entry_dependencies)
        parsed_entries[path] = (data, body)
    if len(repositories) != 1:
        raise WebInputError("変換対象は同一target_repoで指定してください")
    material_repo = next(iter(repositories))
    if normalized_target_repo is not None and material_repo != normalized_target_repo:
        raise WebInputError(f"target_repoが一致しません: 期待={normalized_target_repo} 実際={material_repo}")
    if _local_worktree_repo_id(local_worktree) != material_repo:
        raise WebInputError("planningの変換対象repoとローカルworktreeが一致しません")
    target_commit = _resolve_plan_base_commit(plan_path, local_worktree)

    message_frontmatter, message_body = _add.parse_entry_message(message, entry_type=MQ_TYPE_FEEDBACK)
    requested_type = message_frontmatter.get("type")
    if requested_type is not None and requested_type != MQ_TYPE_FEEDBACK:
        raise WebInputError("planningの統合本文のtypeはfeedbackで指定してください")
    for key in ("target_commit", "depends_on", "plan_file", "queue_schedule", "cooldown_until"):
        if key in message_frontmatter:
            raise WebInputError(f"{key}はplanningの統合処理が管理する予約キーです")
    updates = dict(message_frontmatter)
    if "target_repo" in updates:
        raw_target_repo = updates["target_repo"]
        if not isinstance(raw_target_repo, str):
            raise WebInputError("target_repoは文字列で指定してください")
        updates["target_repo"] = _resolve_repo_id(raw_target_repo)
        if updates["target_repo"] != material_repo:
            raise WebInputError(f"target_repoが一致しません: 期待={material_repo} 実際={updates['target_repo']}")

    oldest_path = min(snapshots, key=lambda item: item[0].name)[0]
    oldest_data, _oldest_body = parsed_entries[oldest_path]
    updated_data = {**oldest_data, **updates}
    updated_data["source"] = "plan-and-add-feedback"
    updated_data["plan_file"] = str(plan_path)
    updated_data["target_commit"] = target_commit
    updated_data.pop("queue_schedule", None)
    updated_data.pop("cooldown_until", None)
    all_dependencies = dependencies + list(normalized_dependencies)
    excluded_inputs = set(input_names)
    canonical_dependencies = tuple(
        value for value in dict.fromkeys(all_dependencies) if value not in excluded_inputs and value != oldest_path.name
    )
    dependency_graph = _active_dependency_graph(inbox_dir, processing_dir, planning_dir)
    dependency_graph[oldest_path.name] = set(canonical_dependencies)
    if any(_dependency_reaches(dependency_graph, dependency, oldest_path.name) for dependency in canonical_dependencies):
        raise WebInputError(f"循環する依存を指定できません: {oldest_path.name}")
    if canonical_dependencies:
        updated_data["depends_on"] = list(canonical_dependencies)
    else:
        updated_data.pop("depends_on", None)
    updated_text = _frontmatter.serialize_frontmatter(
        updated_data,
        "\n" + message_body.strip("\n").rstrip() + "\n",
    )

    inbox_path = inbox_dir / oldest_path.name
    if inbox_path.exists():
        raise WebInputError(f"inboxに同名エントリが既に存在します: {oldest_path.name}")
    source_paths = tuple(path for path, _text in snapshots)
    source_relative_paths = tuple(str(path.relative_to(private_notes)) for path in source_paths)
    destination_relative_path = str(inbox_path.relative_to(private_notes))
    commit_paths = (*source_relative_paths, destination_relative_path)
    start_head = _git_head(private_notes)
    try:
        _atomic_write_text(inbox_path, updated_text)
        for path in source_paths:
            path.unlink()
        try:
            _commit_and_push(
                private_notes,
                "chore: convert feedback items to plans",
                commit_paths,
                skip_push=skip_push,
            )
        except (OSError, subprocess.SubprocessError) as error:
            if _git_head(private_notes) == start_head:
                _restore_conversion_paths(
                    private_notes,
                    start_head,
                    source_relative_paths,
                    (destination_relative_path,),
                )
            raise error
        if not inbox_path.is_file() or any(path.exists() for path in source_paths):
            raise RuntimeError("計画型変換後の保存集合を検証できません")
        return {
            "entries": [_add._read_saved_entry_details(inbox_path)],  # pylint: disable=protected-access
            "plan_file": str(plan_path),
            "commit": _git_head(private_notes),
            "planning": True,
        }
    except Exception as error:
        command = getattr(error, "cmd", ())
        push_failure = any(part == "push" for part in command)
        if _git_head(private_notes) == start_head and not push_failure:
            _restore_conversion_paths(
                private_notes,
                start_head,
                source_relative_paths,
                (destination_relative_path,),
            )
        raise


def convert_entries_to_plan(
    private_notes: pathlib.Path,
    *,
    filenames: tuple[str, ...],
    plan_file: str,
    message: str | None = None,
    depends_on: tuple[str, ...] | None = None,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    skip_push: bool = False,
    local_worktree: pathlib.Path | None = None,
) -> dict[str, object]:
    """状態別のfeedbackを計画実装型へ変換し、planningは1件へ統合する。"""
    if not filenames:
        raise WebInputError("変換するFILENAMEを1件以上指定してください")
    try:
        plan_path = _plan_file.resolve_plan_file(plan_file, private_notes=private_notes)
        stored_plan_file = _plan_file.normalize_plan_file(plan_file, private_notes=private_notes)
    except ValueError as error:
        raise WebInputError(f"plan_fileを解決できません: {plan_file}（{error}）") from error
    inbox_dir = private_notes / MQ_STATE_INBOX
    processing_dir = private_notes / MQ_STATE_PROCESSING
    normalized_filenames = tuple(dict.fromkeys(_validate_filename(name, inbox_dir).name for name in filenames))
    if len(normalized_filenames) != len(filenames):
        raise WebInputError("同じFILENAMEを重複して指定できません")
    normalized_dependencies = tuple(dict.fromkeys(_validate_filename(value, inbox_dir).name for value in (depends_on or ())))
    normalized_target_repo = _resolve_repo_id(target_repo) if target_repo is not None else None

    with _repo_lock(private_notes, timeout=lock_timeout):
        _assert_conversion_worktree_clean(private_notes)
        _push_pending_commits(private_notes)
        _pull(private_notes)
        try:
            if not plan_path.is_file():
                raise WebInputError(f"plan_fileが実在する通常ファイルではありません: {plan_file}")
        except OSError as error:
            raise WebInputError(f"plan_fileを検証できません: {plan_file}") from error
        planning_dir = _subdir(private_notes, MQ_STATE_PLANNING)
        state, paths = _resolve_conversion_targets(
            normalized_filenames,
            inbox_dir,
            processing_dir,
            planning_dir,
        )
        if state == MQ_STATE_PLANNING:
            if message is None:
                raise WebInputError("planningの入力には--messageを指定してください")
            return _convert_planning_entries(
                private_notes,
                paths=paths,
                plan_path=plan_path,
                message=message,
                normalized_dependencies=normalized_dependencies,
                normalized_target_repo=normalized_target_repo,
                local_worktree=local_worktree,
                inbox_dir=inbox_dir,
                processing_dir=processing_dir,
                planning_dir=planning_dir,
                skip_push=skip_push,
            )
        if message is not None:
            raise WebInputError("inbox・processingの入力には--messageを指定できません")
        if len(paths) != len(normalized_filenames):
            raise WebInputError("変換対象を一意に特定できません")
        _assert_conversion_targets_tracked(private_notes, paths)
        snapshots = [(path, path.read_text(encoding="utf-8")) for path in paths]
        dependency_graph = _active_dependency_graph(inbox_dir, processing_dir)
        if normalized_dependencies:
            dependency_graph.update({path.name: set(normalized_dependencies) for path, _text in snapshots})
            for path, _text in snapshots:
                if path.name in normalized_dependencies:
                    raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
            if any(
                _dependency_reaches(dependency_graph, dependency, path.name)
                for path, _text in snapshots
                for dependency in normalized_dependencies
            ):
                raise WebInputError("循環する依存を指定できません")

        updated: list[tuple[pathlib.Path, str, str]] = []
        repositories: set[str] = set()
        for path, text in snapshots:
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
            repositories.add(entry_repo)
            if normalized_target_repo is not None and entry_repo != normalized_target_repo:
                raise WebInputError(f"target_repoが一致しません: {path.name}は{entry_repo}、指定値は{normalized_target_repo}")
            if "plan_file" in data:
                raise WebInputError(f"既に計画型のため再変換できません: {path.name}")
            if depends_on is None:
                stored_dependencies = _entry_dependencies_for_conversion(path, data)
                if stored_dependencies:
                    data["depends_on"] = list(stored_dependencies)
            if depends_on is not None:
                if path.name in normalized_dependencies:
                    raise WebInputError(f"自分自身を依存先へ指定できません: {path.name}")
                if normalized_dependencies:
                    data["depends_on"] = list(normalized_dependencies)
                else:
                    data.pop("depends_on", None)
            data["plan_file"] = stored_plan_file
            data.pop("queue_schedule", None)
            updated_text = _frontmatter.serialize_frontmatter(data, body)
            updated.append((path, text, updated_text))
        if len(repositories) != 1:
            raise WebInputError("変換対象は同一target_repoで指定してください")

        start_head = _git_head(private_notes)
        relative_paths = tuple(str(path.relative_to(private_notes)) for path, _old, _new in updated)
        try:
            for path, _old, new in updated:
                if new != _old:
                    _atomic_write_text(path, new)
            changed_paths = tuple(path for path, old, new in updated if old != new)
            if not changed_paths:
                return {
                    "entries": [
                        _add._read_saved_entry_details(path)  # pylint: disable=protected-access
                        for path, _old, _new in updated
                    ],
                    "plan_file": stored_plan_file,
                    "commit": None,
                    "planning": False,
                }
            try:
                _commit_and_push(
                    private_notes,
                    "chore: convert feedback items to plans",
                    relative_paths,
                    skip_push=skip_push,
                )
            except (OSError, subprocess.SubprocessError) as error:
                if _git_head(private_notes) == start_head:
                    _restore_conversion_paths(private_notes, start_head, relative_paths)
                raise error
            commit_oid = _git_head(private_notes)
            return {
                "entries": [
                    _add._read_saved_entry_details(path)  # pylint: disable=protected-access
                    for path, _old, _new in updated
                ],
                "plan_file": stored_plan_file,
                "commit": commit_oid,
                "planning": False,
            }
        except Exception as error:
            command = getattr(error, "cmd", ())
            push_failure = any(part == "push" for part in command)
            if _git_head(private_notes) == start_head and not push_failure:
                _restore_conversion_paths(private_notes, start_head, relative_paths)
            raise


def convert_entry_to_plan(
    private_notes: pathlib.Path,
    *,
    filename: str,
    plan_file: str,
    depends_on: tuple[str, ...] | None = None,
    target_repo: str | None = None,
    lock_timeout: float = -1,
    skip_push: bool = False,
) -> dict[str, object]:
    """既存の単一項目APIを一括変換経路へ委譲する。"""
    result = convert_entries_to_plan(
        private_notes,
        filenames=(filename,),
        plan_file=plan_file,
        depends_on=depends_on,
        target_repo=target_repo,
        lock_timeout=lock_timeout,
        skip_push=skip_push,
    )
    entries = result["entries"]
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    return entry


def _cmd_convert_to_plan(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """convert-to-planサブコマンドを実行する。"""
    message = getattr(args, "message", None)
    target_repo, local_worktree = _add.resolve_add_target(args.target_repo)
    if message is not None and local_worktree is None:
        local_worktree = _candidate_local_worktree(args.target_repo)
    skip_push = getattr(args, "skip_push", False)
    raw_filenames = args.filename
    filenames = (raw_filenames,) if isinstance(raw_filenames, str) else tuple(raw_filenames)
    single_compat = len(filenames) == 1
    dependencies = tuple(args.depends_on) if args.depends_on is not None else None
    try:
        result = convert_entries_to_plan(
            private_notes,
            filenames=filenames,
            plan_file=args.plan_file,
            message=message,
            depends_on=dependencies,
            target_repo=target_repo,
            skip_push=skip_push,
            local_worktree=local_worktree,
        )
        entries = result["entries"]
        if not isinstance(entries, list):
            raise RuntimeError("複数変換結果のentriesがリストではありません")
    except WebInputError as error:
        print(f"変換を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    for filename in filenames:
        print(f"計画実装型へ変換: {filename}")
    if not single_compat or result.get("planning") is True:
        commit_oid = result.get("commit")
        if commit_oid:
            print(f"変換commit: {commit_oid}")
        print("push: 省略 (--skip-push)" if skip_push else "push: 完了")
    for details in entries:
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


def _active_dependency_graph(
    inbox_dir: pathlib.Path,
    processing_dir: pathlib.Path,
    planning_dir: pathlib.Path | None = None,
) -> dict[str, set[str]]:
    """ロック内で取得したactiveなフィードバックの依存グラフを返す。"""
    entries: dict[str, pathlib.Path] = {}
    directories = (inbox_dir, processing_dir) if planning_dir is None else (inbox_dir, planning_dir, processing_dir)
    for directory in directories:
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
        skip_push=args.skip_push,
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
        skip_push=args.skip_push,
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


def _cmd_hold(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """holdサブコマンド: 処理可能な項目をholdへ移動する。"""
    args.filenames = _dedup_positional_filenames(args.filenames, "hold")
    filenames = transition_entries(
        private_notes,
        action="hold",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件保留: {', '.join(filenames)}")


def _cmd_unhold(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """unholdサブコマンド: hold項目をinboxへ戻す。"""
    args.filenames = _dedup_positional_filenames(args.filenames, "unhold")
    filenames = transition_entries(
        private_notes,
        action="unhold",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件保留解除: {', '.join(filenames)}")


def _cmd_start_planning(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """start-planningサブコマンド: inboxからplanning/へ移動しcommit・pushする。"""
    args.filenames = _dedup_positional_filenames(args.filenames, "start-planning")
    filenames = transition_entries(
        private_notes,
        action="start-planning",
        filenames=sorted(args.filenames),
        now=now,
        target_repo=args.target_repo,
    )
    print(f"{len(filenames)}件計画作成を開始: {', '.join(filenames)}")


def _cmd_return_to_inbox(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """return-to-inboxサブコマンド: processingからinbox/へ戻しcommit・push。

    保留判定でprocessing化済みの対象を未処理状態へ戻す用途で使う
    （`agent-toolkit:process-feedbacks`のpicker起動契約「同一セッション中にTBDの回答を受領した場合」参照）。
    位置引数の重複は`_dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    args.filenames = _dedup_positional_filenames(args.filenames, "return-to-inbox")
    filenames = transition_entries(
        private_notes,
        action="return-to-inbox",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        state=args.state,
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
    if args.depends_on and args.plan_file is None:
        args.subparser.error("--depends-onは--plan-fileとともに指定してください。")
    if args.plan_file is not None:
        if args.filename is None or message is None:
            args.subparser.error("--plan-fileではFILENAMEとMESSAGEを指定してください。")
        if args.append:
            args.subparser.error("--plan-fileと--appendは併用できません。")
        assert args.filename is not None
        assert message is not None
        try:
            _add.reject_message_file_path(
                message,
                file_input_hint="ファイル内容を本文にする場合はMESSAGEを省略し、エディターで貼り付けてください。",
            )
            target_repo, local_worktree = _add.resolve_add_target(args.target_repo)
            if local_worktree is None:
                local_worktree = _candidate_local_worktree(args.target_repo)
            if local_worktree is None:
                raise WebInputError("計画型編集には対象リポジトリのローカルworktreeが必要です")
            if _local_worktree_repo_id(local_worktree) != target_repo:
                raise WebInputError("計画型編集の対象repoとローカルworktreeが一致しません")
            plan_path = _plan_file.resolve_plan_file(args.plan_file, private_notes=private_notes)
            target_commit = _resolve_plan_base_commit(plan_path, local_worktree)
        except WebInputError as error:
            print(f"計画型編集を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)

        inbox_dir = private_notes / MQ_STATE_INBOX
        _validate_filenames_only([args.filename], inbox_dir)
        with _repo_lock(private_notes):
            _pull(private_notes)
            snapshot_path = _validate_filename(args.filename, private_notes / MQ_STATE_PLANNING)
            if not snapshot_path.is_file():
                print(f"planningに存在しません: {snapshot_path.name}", file=sys.stderr)
                sys.exit(2)
            snapshot = snapshot_path.read_text(encoding="utf-8")
            _verify_target_repo_content(snapshot_path, snapshot, target_repo)
        if _reject_agent_user_comment_change(snapshot, message):
            sys.exit(1)
        try:
            details = edit_entry_to_plan(
                private_notes,
                filename=snapshot_path.name,
                content=message,
                plan_file=args.plan_file,
                target_commit=target_commit,
                depends_on=tuple(args.depends_on or ()),
                target_repo=target_repo,
                expected_content=snapshot,
            )
        except RuntimeError:
            print(
                f"編集中に他プロセスが対象を変更しました: {snapshot_path.name}。"
                "指定したMESSAGEは反映されていません。同じFILENAMEとMESSAGEで再実行してください。",
                file=sys.stderr,
            )
            sys.exit(1)
        except WebInputError as error:
            print(f"計画型編集を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
        print(f"計画型編集反映: {snapshot_path.name}")
        _add._print_entry_details(details)  # pylint: disable=protected-access
        return
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
    if _reject_agent_user_comment_change(original, edited):
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        sys.exit(1)
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
    _add._print_entry_details(  # pylint: disable=protected-access
        _add._read_saved_entry_details(path)  # pylint: disable=protected-access
    )


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
    if _reject_agent_user_comment_change(snapshot.decode("utf-8"), content.decode("utf-8")):
        sys.exit(1)
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
    _add._print_entry_details(  # pylint: disable=protected-access
        _add._read_saved_entry_details(path)  # pylint: disable=protected-access
    )


def _cmd_commit(private_notes: pathlib.Path) -> None:
    """commitサブコマンド: 外部編集後のinbox・processing配下未コミット変更をコミット・push。

    inbox・processing配下に未コミット変更がない場合も滞留commitをpushする。
    """
    if commit_entries(private_notes):
        print("外部編集分をコミット・pushしました。")
    else:
        print("差分なし。滞留commitをpushしました。")
