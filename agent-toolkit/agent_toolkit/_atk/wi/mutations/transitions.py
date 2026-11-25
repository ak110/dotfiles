from __future__ import annotations

import argparse
import dataclasses
import datetime
import pathlib
import shlex
import sys
import typing
import uuid

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import attachments as _attachments
from agent_toolkit._atk.wi import bulk as _bulk
from agent_toolkit._atk.wi import effect_check as _effect_check
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import processing_owner as _owner
from agent_toolkit._atk.wi import show as _wi_show
from agent_toolkit._atk.wi import snapshot as _wi_snapshot
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import (
    BULK_SOURCE_STATES,
    TRANSITION_EXPLICIT_STATES,
    WI_AGENT_REMOVABLE_STATES,
    WI_STATE_ADOPTED,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_STATE_REJECTED,
    WI_TYPE_AWI,
    WI_USER_REMOVABLE_STATES,
    normalized_wi_type,
)
from agent_toolkit._atk.wi.entries import CommitMetadata
from agent_toolkit._atk.wi.mutations.targets import (
    atomic_write_text,
    candidate_local_worktree,
    commit_values_by_path,
    entry_target_repo,
)
from agent_toolkit._atk.wi.repo import (
    resolve_repo_id,
    verify_target_repo_content,
)
from agent_toolkit._atk.wi.web_input import WebInputError

_AGENT_REMOVE_NEXT_ACTION = "削除はユーザーへ依頼する。不要になった項目なら`atk wi reject`で不採用にする"

_AGENT_PROCESSING_HOLD_NEXT_ACTION = (
    "別セッションが処理中の要求を改訂する場合は保留せず、`agent-toolkit:atk-mode/references/states-and-dependencies.md`「状態と依存」の処理中の項目の扱いに従い、"
    "書き換えたい内容を新しい項目として投入するか`atk wi edit --append`で追記する。"
    "自セッションの作業`process-wi`（`/atk-mode process-wi`）が処理中の項目を回答待ちなどで保留する場合は"
    "`--state=processing`を付けて再実行する"
)

_IMPLICIT_TERMINAL_STATE_PRIORITY = (WI_STATE_PROCESSING, WI_STATE_INBOX, WI_STATE_HOLD)
"""`adopt`・`reject`のファイル名指定で同名が複数の状態にある場合の優先順。

`set-dependencies`で指定を省いた場合の順序、`answer`の回答対象の解決順と同じ順とする。
"""


def _implicit_terminal_source_states(action: str) -> tuple[str, ...]:
    """`adopt`・`reject`のファイル名指定が探索する状態を優先順で返す。

    状態の集合は`--all`の候補と同じ`BULK_SOURCE_STATES`から導き、本関数は順序だけを定める。
    両方の処理が状態を別々に列挙すると、状態の追加が片方にだけ反映されるためである。
    """
    return tuple(
        sorted(
            BULK_SOURCE_STATES[action],
            key=lambda state: (
                _IMPLICIT_TERMINAL_STATE_PRIORITY.index(state)
                if state in _IMPLICIT_TERMINAL_STATE_PRIORITY
                else len(_IMPLICIT_TERMINAL_STATE_PRIORITY)
            ),
        )
    )


def _validate_transition_options(
    action: str,
    filenames: list[str],
    *,
    state: str | None,
    expected_content: str | None,
    cooldown_days: int | None,
) -> None:
    """状態遷移オプション間の制約を検証する。"""
    if action not in {"start-processing", "return-to-inbox", "hold", "unhold", "adopt", "reject", "remove"}:
        raise WebInputError(
            f"未知のエントリ操作です: {action}",
            next_action="start-processing・return-to-inbox・hold・unhold・adopt・reject・removeのいずれかを指定する",
        )
    if cooldown_days is not None and (action != "return-to-inbox" or cooldown_days < 3):
        raise WebInputError(
            "cooldown_daysはreturn-to-inboxで3以上を指定してください",
            next_action="return-to-inboxで3以上の日数を指定するか、cooldown_daysの指定を外して再実行する",
        )
    accepted_states = TRANSITION_EXPLICIT_STATES.get(action, ())
    state_is_valid = state in accepted_states
    if state is not None and not state_is_valid:
        rendered_states = "、".join(accepted_states) if accepted_states else "なし"
        raise WebInputError(
            f"操作{action}はstate={state}を受理しません。明示stateとして受理する状態: {rendered_states}",
            next_action=(
                f"stateの指定を外すか、{rendered_states}のいずれかを指定して再実行する"
                if accepted_states
                else "stateの指定を外して再実行する"
            ),
        )
    if expected_content is not None and (action != "remove" or len(filenames) != 1):
        raise WebInputError(
            "expected_contentはremoveで1件を指定する場合に限り使用できます",
            next_action="expected_contentの指定を外すか、removeで対象を1件だけ指定して再実行する",
        )


def _resolve_transition_paths(
    private_notes: pathlib.Path,
    action: str,
    filenames: list[str],
    state: str | None,
    *,
    missing_is_conflict: bool,
    actor_is_agent: bool,
) -> list[pathlib.Path]:
    removable_states = WI_AGENT_REMOVABLE_STATES if actor_is_agent else WI_USER_REMOVABLE_STATES
    if action == "remove" and state is not None and state not in removable_states:
        raise WebInputError(f"エージェント環境ではstate={state}の項目を削除できません", next_action=_AGENT_REMOVE_NEXT_ACTION)
    try:
        records, missing = _wi_entries.read_named_entries(
            private_notes, _wi_entries.validate_named_filenames(private_notes, filenames), target_repo=None
        )
    except (OSError, UnicodeError) as error:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
        raise
    for path, _, _, actual_state, _ in records:
        if action == "remove" and actual_state not in removable_states:
            raise WebInputError(
                f"エージェント環境ではstate={actual_state}の項目を削除できません", next_action=_AGENT_REMOVE_NEXT_ACTION
            )
        if action == "hold" and state is None and actor_is_agent and actual_state == WI_STATE_PROCESSING:
            raise WebInputError(
                f"エージェント環境では処理中（processing）の項目を--state=processingの指定なしに保留できません: {path.name}",
                next_action=_AGENT_PROCESSING_HOLD_NEXT_ACTION,
            )
    accepted = (
        (state,)
        if state is not None
        else {
            "start-processing": (WI_STATE_INBOX,),
            "return-to-inbox": (WI_STATE_PROCESSING,),
            "unhold": (WI_STATE_HOLD,),
            "hold": (WI_STATE_INBOX, WI_STATE_PROCESSING),
            "adopt": _implicit_terminal_source_states("adopt"),
            "reject": _implicit_terminal_source_states("reject"),
            "remove": removable_states,
        }[action]
    )
    missing.extend(path.name for path, _, _, actual, _ in records if actual not in accepted)
    if missing:
        if missing_is_conflict:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        _outcome.report_failure(
            f"{'・'.join(accepted)}に存在しない: {', '.join(missing)}",
            next_action="`atk wi list --state=all`でファイル名と保存状態を確認して再実行する",
        )
        sys.exit(2)
    return [record[0] for record in records]


def _validate_transition_targets(
    paths: list[pathlib.Path],
    *,
    action: str,
    target_repo: str | typing.Iterable[str] | None,
    expected_content: str | None,
    cooldown_days: int | None,
    force: bool,
) -> str | None:
    current_content: str | None = None
    if action == "remove" and expected_content is not None:
        try:
            current_content = paths[0].read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
        if current_content != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
    raw_target_repos = (target_repo,) if isinstance(target_repo, str) else tuple(target_repo or ())
    normalized_target_repos = tuple(dict.fromkeys(resolve_repo_id(repo) for repo in raw_target_repos))
    for path in paths:
        content = current_content if current_content is not None else path.read_text(encoding="utf-8")
        if action == "start-processing":
            _wi_entries.entry_type_of(path, content)

            entry_target_repo(path, content)
        if len(normalized_target_repos) <= 1:
            verify_target_repo_content(path, content, normalized_target_repos[0] if normalized_target_repos else None)
        else:
            actual_target_repo = entry_target_repo(path, content)
            if actual_target_repo not in normalized_target_repos:
                _outcome.report_failure(
                    f"target_repoが一致しない: 期待={', '.join(normalized_target_repos)} 実際={actual_target_repo} "
                    f"ファイル={path}",
                    next_action=(
                        f"実際の値を--target-repoへ指定し直すか、`atk wi show {path.name}`で別リポジトリの項目でないか確認する"
                    ),
                )
                sys.exit(2)
    if cooldown_days is not None:
        non_awi = [
            path.name for path in paths if _wi_entries.entry_type_of(path, path.read_text(encoding="utf-8")) != WI_TYPE_AWI
        ]
        if non_awi:
            raise WebInputError(
                f"`--cooldown-days`はAWI専用です: {', '.join(non_awi)}",
                next_action="--cooldown-daysの対象からAWI以外の項目を外して再実行する",
            )
    if action == "remove" and not force:
        protected = [path.name for path in paths if _wi_entries.entry_state_of(path) == WI_STATE_PROCESSING]
        if protected:
            _outcome.report_failure(
                f"processing状態のファイルは、--forceを指定しない限り削除できない: {', '.join(protected)}",
                next_action="削除するには--force（Web APIはforce指定）を指定する",
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
            atomic_write_text(path, updated)


def _apply_transition(
    private_notes: pathlib.Path,
    paths: list[pathlib.Path],
    *,
    action: str,
    now: datetime.datetime,
    note: str | None,
    commit_values: dict[pathlib.Path, CommitMetadata | list[CommitMetadata] | None],
    cooldown_days: int | None,
    effect_check: str | None,
    effect_check_reason: str | None,
    processing_owner: _owner.Owner | None = None,
) -> None:
    del private_notes
    destination_name = {
        "start-processing": WI_STATE_PROCESSING,
        "return-to-inbox": WI_STATE_INBOX,
        "hold": WI_STATE_HOLD,
        "unhold": WI_STATE_INBOX,
        "adopt": WI_STATE_ADOPTED,
        "reject": WI_STATE_REJECTED,
    }.get(action)
    if destination_name is None:
        for path in paths:
            path.unlink()
        return
    tracked_awis = {
        path
        for path in paths
        if effect_check is not None
        and action == "adopt"
        and commit_values[path]
        and _wi_entries.entry_type_of(path, path.read_text(encoding="utf-8")) == WI_TYPE_AWI
    }
    _effect_check.validate_adoption_effect(effect_check, effect_check_reason)
    if processing_owner is not None:
        _owner.remember_process(processing_owner)
    _update_transition_metadata(paths, action=action, now=now, cooldown_days=cooldown_days)
    for path in paths:
        if action in {"adopt", "reject"}:
            _wi_entries.stamp_result(
                path,
                outcome=destination_name,
                now=now,
                commit=commit_values[path],
                note=note,
                effect_check=effect_check if path in tracked_awis else None,
                effect_check_reason=effect_check_reason.strip() if path in tracked_awis and effect_check_reason else None,
            )
        parsed = _frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        data, body = parsed
        if action in {"return-to-inbox", "hold"} and _wi_entries.entry_state_of(path) in {WI_STATE_ADOPTED, WI_STATE_REJECTED}:
            body = _wi_entries.without_last_result_section(body)
        data["state"] = destination_name
        if destination_name != WI_STATE_PROCESSING:
            data.pop(_owner.KEY, None)
        elif processing_owner is not None:
            data[_owner.KEY] = dataclasses.asdict(processing_owner)
        if path in tracked_awis:
            data["effect_check"] = effect_check
        atomic_write_text(path, _frontmatter.serialize_frontmatter(data, body))


def _inbox_names_with_commit(
    states: dict[pathlib.Path, str],
    commit_values: dict[pathlib.Path, CommitMetadata | list[CommitMetadata] | None],
) -> list[str]:
    return [path.name for path, state in states.items() if state == WI_STATE_INBOX and commit_values[path]]


def _warn_skipped_processing(filenames: list[str]) -> None:
    for filename in filenames:
        _outcome.report_warning(
            f"{filename}は作業を示すcommitがあるのに処理中（processing）を経ずにinboxから終了した",
            next_action="次回から項目の作業に着手する時点で`atk wi start-processing <ファイル名>`を実行してください",
        )


def _transition_commit_message(action: str, count: int, note: str | None) -> str:
    item_word = "entry" if count == 1 else "entries"
    note_suffix = f" (理由: {note})" if action == "remove" and note else ""
    if action == "start-processing":
        # 同じ親・内容・時刻でcommitしても、別ホストの着手を同じcommitとして受け付けない。  # pyfltr: allow-comment
        return f"chore: start processing {count} {item_word}\n\nWI-Claim: {uuid.uuid4().hex}"
    return {
        "return-to-inbox": f"chore: return {count} {item_word} to inbox",
        "hold": f"chore: hold {count} {item_word}",
        "unhold": f"chore: unhold {count} {item_word}",
        "adopt": f"chore: process {count} {item_word} (adopted)",
        "reject": f"chore: process {count} {item_word} (rejected)",
        "remove": f"chore: remove {count} {item_word}{note_suffix}",
    }[action]


def _verify_exit_owner(
    private_notes: pathlib.Path,
    paths: list[pathlib.Path],
    *,
    skip_push: bool,
    previous: dict[pathlib.Path, _owner.Owner] | None = None,
) -> dict[pathlib.Path, _owner.Owner]:
    current = _owner.snapshot(paths)
    missing = (previous or {}).keys() - current.keys()
    if missing:
        raise WebInputError(
            "取得後にprocessingの担当が無くなりました: " + "、".join(sorted(path.name for path in missing)),
            next_action="`atk wi show --pull`で対象の現在の状態を確認する",
        )
    if not current:
        return {}
    has_upstream = _wi_sync.has_remote(private_notes)
    if skip_push and has_upstream:
        raise WebInputError(
            "担当付きの項目の終了には送信が必要です。--skip-pushは指定できません",
            next_action="担当の照合と送信を行うには、--skip-pushを外して再実行する",
        )
    _owner.verify_actor(current, _owner.current())
    _wi_sync.ensure_guarded_update_clean(private_notes)
    if has_upstream:
        _owner.verify_upstream(private_notes, current)
    return current


def transition_entries(
    private_notes: pathlib.Path,
    *,
    action: str,
    filenames: list[str],
    now: datetime.datetime,
    target_repo: str | typing.Iterable[str] | None = None,
    note: str | None = None,
    commit: str | list[str] | None = None,
    storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
    force: bool = False,
    state: str | None = None,
    expected_content: str | None = None,
    cooldown_days: int | None = None,
    local_worktree: pathlib.Path | None = None,
    skip_push: bool = False,
    actor_is_agent: bool = False,
    inbox_with_commit: list[str] | None = None,
    effect_check: str | None = None,
    effect_check_reason: str | None = None,
) -> list[str]:
    _validate_transition_options(
        action,
        filenames,
        state=state,
        expected_content=expected_content,
        cooldown_days=cooldown_days,
    )
    inbox_dir = private_notes / WI_STATE_INBOX
    _wi_filenames.validate_filenames_only(filenames, inbox_dir)
    with storage.mutation(private_notes, push_pending=True, skip_push=skip_push) as mutation:
        check_owner = actor_is_agent and action in _owner.EXIT_ACTIONS
        prior: dict[pathlib.Path, _owner.Owner] = {}
        if check_owner:
            records, _ = _wi_entries.read_named_entries(
                private_notes, _wi_entries.validate_named_filenames(private_notes, filenames), target_repo=None
            )
            prior = _owner.snapshot(record[0] for record in records)
            _wi_sync.synchronize_owner_check(private_notes)
        missing_is_conflict = action == "remove" and expected_content is not None
        paths = _resolve_transition_paths(
            private_notes,
            action,
            filenames,
            state,
            missing_is_conflict=missing_is_conflict,
            actor_is_agent=actor_is_agent,
        )
        guarded = _verify_exit_owner(private_notes, paths, skip_push=skip_push, previous=prior) if check_owner else {}
        _validate_transition_targets(
            paths,
            action=action,
            target_repo=target_repo,
            expected_content=expected_content,
            cooldown_days=cooldown_days,
            force=force,
        )
        commit_values: dict[pathlib.Path, CommitMetadata | list[CommitMetadata] | None] = (
            commit_values_by_path(paths, commit, local_worktree)
            if action in {"adopt", "reject"}
            else {path: None for path in paths}
        )
        if inbox_with_commit is not None:
            inbox_with_commit.extend(
                _inbox_names_with_commit({path: _wi_entries.entry_state_of(path) for path in paths}, commit_values)
            )
        attachment_paths = _attachments.paths_for_removal(private_notes, paths) if action == "remove" else []
        _apply_transition(
            private_notes,
            paths,
            action=action,
            now=now,
            note=note,
            commit_values=commit_values,
            cooldown_days=cooldown_days,
            effect_check=effect_check,
            effect_check_reason=effect_check_reason,
            processing_owner=_owner.current() if actor_is_agent and action == "start-processing" else None,
        )
        for path in attachment_paths:
            path.unlink()
        message = _transition_commit_message(action, len(paths), note)
        changed = [str(path.relative_to(private_notes)) for path in (*paths, *attachment_paths)]
        if guarded:
            mutation.commit_guarded(message, changed, lambda: _owner.verify_upstream(private_notes, guarded))
        elif action == "start-processing":
            mutation.commit(message, changed, exclusive_claim=True)
        else:
            mutation.commit(message, changed)
    return [path.name for path in paths]


def _single_target_repo(target_repo: str | typing.Iterable[str] | None) -> str | None:
    if target_repo is None or isinstance(target_repo, str):
        return target_repo
    values = tuple(target_repo)
    return values[0] if len(values) == 1 else None


def _bulk_transition(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    *,
    action: str,
) -> list[str]:
    note = getattr(args, "note", None)
    commit = getattr(args, "commit", None)
    skip_push = getattr(args, "skip_push", False)
    cooldown_days = getattr(args, "cooldown_days", None)
    local_worktree = candidate_local_worktree(_single_target_repo(args.target_repo)) if commit is not None else None
    inbox_with_commit: list[str] = []
    actor_is_agent = is_agent_environment()

    def apply_fn(notes: pathlib.Path, candidates: list[_wi_entries.EntryRecord]) -> list[str]:
        paths = [entry[0] for entry in candidates]
        guarded = (
            _verify_exit_owner(notes, paths, skip_push=skip_push) if actor_is_agent and action in _owner.EXIT_ACTIONS else {}
        )
        # 個別指定と同じ内容検証を適用の直前へ置く。`--cooldown-days`の対象種別の制約もここが判定する。
        # processing状態の保護は候補の確認時に`bulk`が判定済みのため、ここでは再判定しない。
        _validate_transition_targets(
            paths,
            action=action,
            target_repo=args.target_repo,
            expected_content=None,
            cooldown_days=cooldown_days,
            force=True,
        )
        commit_values = commit_values_by_path(paths, commit, local_worktree)
        inbox_with_commit.extend(_inbox_names_with_commit({entry[0]: entry[3] for entry in candidates}, commit_values))
        _apply_transition(
            notes,
            paths,
            action=action,
            now=now,
            note=note,
            commit_values=commit_values,
            cooldown_days=cooldown_days,
            effect_check=getattr(args, "effect_check", None),
            effect_check_reason=getattr(args, "effect_check_reason", None),
            processing_owner=_owner.current() if actor_is_agent and action == "start-processing" else None,
        )
        message = _transition_commit_message(action, len(paths), note)
        changed = [str(path.relative_to(notes)) for path in paths]
        if guarded:
            _wi_sync.commit_guarded(notes, message, changed, lambda: _owner.verify_upstream(notes, guarded))
        else:
            _wi_sync.commit_and_push(
                notes,
                message,
                changed,
                skip_push=skip_push,
                **({"exclusive_claim": True} if action == "start-processing" else {}),
            )
        return [path.name for path in paths]

    filenames = _bulk.bulk_apply_entries(
        private_notes,
        action=action,
        target_repo=args.target_repo,
        assume_yes=args.yes,
        force=getattr(args, "force", False),
        skip_pull=args.skip_pull,
        status=args.status,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
        actor_is_agent=actor_is_agent,
        apply_fn=apply_fn,
    )
    if action == "adopt":
        _warn_skipped_processing(inbox_with_commit)
    return filenames


def _report_user_fields(private_notes: pathlib.Path, filenames: list[str]) -> None:
    if not is_agent_environment():
        return
    for filename in filenames:
        path = _wi_entries.entry_path(private_notes, filename)
        text = path.read_text(encoding="utf-8")
        parsed = _frontmatter.parse_frontmatter(text)
        kind = normalized_wi_type(parsed[0].get("type")) if parsed is not None else None
        field = _wi_show.user_field_location(text, kind)
        if field is not None:
            section, line = field
            command = shlex.join(["atk", "wi", "show", filename, "--section", section])
            print(f"記入欄: {filename} ## {section} あり 開始行: {line}（保存本文上の1始まり） 読取: {command}")


def cmd_adopt(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    if args.all:
        filenames = _bulk_transition(args, private_notes, now, action="adopt")
        if filenames:
            _outcome.report_success(f"{len(filenames)}件をadoptedへ移した")
            for filename in filenames:
                print(_wi_entries.entry_path(private_notes, filename))
            _report_user_fields(private_notes, filenames)
        return
    args.filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "adopt")
    local_worktree = candidate_local_worktree(_single_target_repo(args.target_repo)) if args.commit is not None else None
    inbox_with_commit: list[str] = []
    filenames = transition_entries(
        private_notes,
        action="adopt",
        actor_is_agent=is_agent_environment(),
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
        local_worktree=local_worktree,
        skip_push=args.skip_push,
        inbox_with_commit=inbox_with_commit,
        effect_check=getattr(args, "effect_check", None),
        effect_check_reason=getattr(args, "effect_check_reason", None),
    )
    _warn_skipped_processing(inbox_with_commit)
    _outcome.report_success(f"{len(filenames)}件をadoptedへ移した")
    for filename in filenames:
        print(_wi_entries.entry_path(private_notes, filename))
    _report_user_fields(private_notes, filenames)


def cmd_reject(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    if args.all:
        filenames = _bulk_transition(args, private_notes, now, action="reject")
        if filenames:
            _outcome.report_success(f"{len(filenames)}件をrejectedへ移した")
            for filename in filenames:
                print(_wi_entries.entry_path(private_notes, filename))
            _report_user_fields(private_notes, filenames)
        return
    args.filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "reject")
    local_worktree = candidate_local_worktree(_single_target_repo(args.target_repo)) if args.commit is not None else None
    filenames = transition_entries(
        private_notes,
        action="reject",
        actor_is_agent=is_agent_environment(),
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        note=args.note,
        commit=args.commit,
        state=WI_STATE_INBOX if args.if_inbox else None,
        local_worktree=local_worktree,
        skip_push=args.skip_push,
    )
    _outcome.report_success(f"{len(filenames)}件をrejectedへ移した")
    for filename in filenames:
        print(_wi_entries.entry_path(private_notes, filename))
    _report_user_fields(private_notes, filenames)


def cmd_start_processing(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    if args.all:
        filenames = _bulk_transition(args, private_notes, now, action="start-processing")
        if filenames:
            _wi_snapshot.remember_processing_started(private_notes, filenames)
            _outcome.report_success(f"{len(filenames)}件をprocessingへ移した: {', '.join(filenames)}")
            _report_user_fields(private_notes, filenames)
        return
    args.filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "start-processing")
    filenames = transition_entries(
        private_notes,
        action="start-processing",
        actor_is_agent=is_agent_environment(),
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
    )
    _wi_snapshot.remember_processing_started(private_notes, filenames)
    _outcome.report_success(f"{len(filenames)}件をprocessingへ移した: {', '.join(filenames)}")
    _report_user_fields(private_notes, filenames)


def cmd_take_over(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    actor = _owner.current()
    if actor is None:
        raise WebInputError("現在の会話を解決できないため引き継げません", next_action="担当する会話から実行する")
    filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "take-over")
    _wi_filenames.validate_filenames_only(filenames, private_notes)
    with _wi_sync.repo_lock(private_notes):
        _wi_sync.ensure_mutation_allowed(private_notes)
        paths = _resolve_transition_paths(
            private_notes,
            "return-to-inbox",
            filenames,
            WI_STATE_PROCESSING,
            missing_is_conflict=False,
            actor_is_agent=False,
        )
        previous = _owner.snapshot(paths)
        if len(previous) != len(paths):
            raise WebInputError(
                "担当の記録が無い項目は引き継げません",
                next_action="旧書式の項目であれば現在の担当を確かめてから従来どおり処理する",
            )
        _wi_sync.synchronize_owner_check(private_notes)
        if _owner.snapshot(path for path in paths if path.is_file()) != previous:
            raise WebInputError(
                "取得後に対象の状態か担当が変わりました", next_action="`atk wi show --pull`で現在の状態と担当を確認する"
            )
        if _wi_sync.has_remote(private_notes):
            _owner.verify_upstream(private_notes, previous)
        _wi_sync.ensure_guarded_update_clean(private_notes)
        updates = {}
        for path, owner in previous.items():
            handoff = _owner.takeover_note(path, owner, actor, args.reason)
            parsed = _frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
            assert parsed is not None
            data, body = parsed
            data[_owner.KEY] = dataclasses.asdict(actor)
            updates[path] = _frontmatter.serialize_frontmatter(data, _wi_entries.append_result_lines(body, [handoff]))
        _owner.remember_process(actor)
        for path, text in updates.items():
            atomic_write_text(path, text)
        _wi_sync.commit_guarded(
            private_notes,
            f"chore: take over {len(paths)} WI",
            [str(path.relative_to(private_notes)) for path in paths],
            lambda: _owner.verify_upstream(private_notes, previous),
        )
    _outcome.report_success(f"{len(paths)}件の担当を{actor}へ移した: {', '.join(path.name for path in paths)}")


def cmd_hold(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    if args.all:
        filenames = _bulk_transition(args, private_notes, now, action="hold")
        if filenames:
            _outcome.report_success(f"{len(filenames)}件をholdへ移した: {', '.join(filenames)}")
            _report_user_fields(private_notes, filenames)
        return
    args.filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "hold")
    filenames = transition_entries(
        private_notes,
        action="hold",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        state=args.state,
        actor_is_agent=is_agent_environment(),
    )
    _outcome.report_success(f"{len(filenames)}件をholdへ移した: {', '.join(filenames)}")
    _report_user_fields(private_notes, filenames)


def cmd_unhold(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    if args.all:
        filenames = _bulk_transition(args, private_notes, now, action="unhold")
        if filenames:
            _outcome.report_success(f"{len(filenames)}件をholdからinboxへ戻した: {', '.join(filenames)}")
            _report_user_fields(private_notes, filenames)
        return
    args.filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "unhold")
    filenames = transition_entries(
        private_notes,
        action="unhold",
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
    )
    _outcome.report_success(f"{len(filenames)}件をholdからinboxへ戻した: {', '.join(filenames)}")
    _report_user_fields(private_notes, filenames)


def cmd_return_to_inbox(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> None:
    """return-to-inboxサブコマンド: processingからinbox/へ戻しcommit・push。

    保留判定でprocessing化済みの対象を未処理状態へ戻す用途で使う
    （`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「処理対象WIの追加」参照）。
    位置引数の重複は`_wi_filenames.dedup_positional_filenames`で除去し、除去件数が0より大きい場合は警告する。
    """
    if args.all:
        filenames = _bulk_transition(args, private_notes, now, action="return-to-inbox")
        if filenames:
            _outcome.report_success(f"{len(filenames)}件をinboxへ差し戻した: {', '.join(filenames)}")
            _report_user_fields(private_notes, filenames)
        return
    args.filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "return-to-inbox")
    filenames = transition_entries(
        private_notes,
        action="return-to-inbox",
        actor_is_agent=is_agent_environment(),
        filenames=args.filenames,
        now=now,
        target_repo=args.target_repo,
        state=args.state,
        cooldown_days=args.cooldown_days,
    )
    _outcome.report_success(f"{len(filenames)}件をinboxへ差し戻した: {', '.join(filenames)}")
    _report_user_fields(private_notes, filenames)


def cmd_rm(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    if args.all:
        filenames = _bulk.remove_all_entries(
            private_notes,
            target_repo=args.target_repo,
            assume_yes=args.yes,
            force=args.force,
            note=args.note,
            skip_pull=args.skip_pull,
            status=args.status,
            entry_type=args.type,
            answered=args.answered,
            source=args.source,
            actor_is_agent=is_agent_environment(),
        )
        if filenames:
            _outcome.report_success(f"{len(filenames)}件を削除した: {', '.join(filenames)}")
        return

    args.filenames = _wi_filenames.dedup_positional_filenames(args.filenames, "rm")
    filenames = transition_entries(
        private_notes,
        action="remove",
        filenames=args.filenames,
        now=datetime.datetime.now(),
        target_repo=args.target_repo,
        force=args.force,
        note=args.note,
        state=args.state,
        actor_is_agent=is_agent_environment(),
    )
    _outcome.report_success(f"{len(filenames)}件を削除した: {', '.join(filenames)}")
