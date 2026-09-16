"""`atk wi`の状態遷移コマンドの`--all`経路が共有する候補選定・確認・再照合を提供する。

候補の選定、一覧表示、確認及び確認済み記録との再照合は操作に依存しない。
操作ごとに変わるのは遷移元状態集合、メッセージの操作名、および確定した候補へ適用する処理だけとする。
適用処理は呼び出し側が`apply_fn`で渡す。状態遷移の適用は`mutations/transitions.py`が持つため、
本モジュールから当該モジュールをimportすると循環参照になるためである。
"""

import pathlib
import sys
import typing
from collections.abc import Callable, Iterable

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi.common import (
    WI_STATE_PROCESSING,
    WI_STATES,
    WI_TYPES,
    _commit_and_push,
    _pull,
    _repo_lock,
    _subdir,
    calculate_readiness,
)
from agent_toolkit._atk.wi.constants import BULK_ACTION_LABELS, bulk_source_states
from agent_toolkit._atk.wi.listing import QueueEntryDisplay, _print_entries, _select_entries
from agent_toolkit._atk.wi.repo import _resolve_repo_id


class CandidateKey(typing.NamedTuple):
    """候補の同一性を判定する組。"""

    state: str
    name: str
    target_repo: str
    text: str
    entry_type: str


type CandidateSnapshot = tuple[CandidateKey, ...]
type ApplyCandidates = Callable[[pathlib.Path, list[QueueEntryDisplay]], list[str]]


def _select_candidates(
    private_notes: pathlib.Path,
    target_repo: Iterable[str],
    *,
    status: Iterable[str],
    entry_type: Iterable[str],
    answered: Iterable[str],
    source: Iterable[str] | None,
    source_states: tuple[str, ...],
) -> list[QueueEntryDisplay]:
    """一覧条件と当該操作の遷移元状態集合がともに一致する項目を選択する。"""
    return [
        entry
        for entry in _select_entries(
            private_notes,
            status=status,
            target_repo=target_repo,
            entry_type=entry_type,
            answered=answered,
            source=source,
        )
        if entry[3] in source_states and entry[4] in WI_TYPES
    ]


def _candidate_key(entry: QueueEntryDisplay) -> CandidateKey:
    """候補1件を状態・名前・対象リポジトリ・本文・種別の比較可能な組へ変換する。"""
    path, target_repo, text, state, entry_type = entry
    assert entry_type is not None  # `_select_candidates`が有効な種別だけを返す。
    return CandidateKey(state, path.name, target_repo, text, entry_type)


def _snapshot(candidates: list[QueueEntryDisplay]) -> CandidateSnapshot:
    """候補一覧を確認時点の記録へ変換する。"""
    return tuple(_candidate_key(entry) for entry in candidates)


def _ensure_processing_is_explicit(
    candidates: list[QueueEntryDisplay],
    *,
    force: bool,
) -> None:
    """processing候補がある場合に明示的な保護解除を要求する。"""
    protected = [path.name for path, _repo, _text, state, _type in candidates if state == WI_STATE_PROCESSING]
    if protected and not force:
        _outcome.report_failure(
            f"processing状態のファイルは既定で削除を保護する: {', '.join(protected)}。削除するには--forceを指定する"
        )
        sys.exit(2)


def _confirm(count: int, label: str) -> bool:
    """対話端末で一括操作を1回確認する。"""
    if not sys.stdin.isatty():
        _outcome.report_failure(f"非対話環境で一括{label}するには--yesを指定する")
        sys.exit(2)
    answer = input(f"上記{count}件を{label}します。続行しますか？ [Y/n]: ")
    return answer.strip().casefold() in {"", "y", "yes"}


def _remove_candidates(
    private_notes: pathlib.Path,
    candidates: list[QueueEntryDisplay],
    *,
    note: str | None,
) -> list[str]:
    """ロック保持下で候補を削除し、単一commit・pushへまとめる。"""
    for path, _repo, _text, _state, _type in candidates:
        path.unlink()
    for state_name in WI_STATES:
        _subdir(private_notes, state_name)
    count = len(candidates)
    item_word = "entry" if count == 1 else "entries"
    note_suffix = f" (理由: {note})" if note else ""
    _commit_and_push(
        private_notes,
        f"chore: remove {count} {item_word}{note_suffix}",
        list(WI_STATES),
    )
    return [path.name for path, _repo, _text, _state, _type in candidates]


def _apply_confirmed_candidates(
    private_notes: pathlib.Path,
    normalized_repos: tuple[str, ...],
    confirmed: CandidateSnapshot,
    *,
    action: str,
    status: Iterable[str],
    entry_type: Iterable[str],
    answered: Iterable[str],
    source: Iterable[str] | None,
    actor_is_agent: bool,
    apply_fn: ApplyCandidates,
) -> list[str]:
    """remote同期後の候補を確認済み記録と突合し、内容が変わらない項目だけへ適用する。

    同期後に新規出現した項目は確認を経ていないため対象にしない。
    確認済みでも状態移動・本文変更・消失で記録と一致しなくなった項目は対象から外し、標準出力へ報告する。
    """
    label = BULK_ACTION_LABELS[action]
    source_states = bulk_source_states(action, actor_is_agent=actor_is_agent)
    confirmed_keys = set(confirmed)
    with _repo_lock(private_notes):
        _pull(private_notes)
        current = _select_candidates(
            private_notes,
            normalized_repos,
            status=status,
            entry_type=entry_type,
            answered=answered,
            source=source,
            source_states=source_states,
        )
        current_keys = {_candidate_key(entry) for entry in current}
        applicable = [entry for entry in current if _candidate_key(entry) in confirmed_keys]
        changed = [key.name for key in confirmed if key not in current_keys]
        if changed:
            print(f"確認後に変更されたため{label}しません: {', '.join(changed)}")
        if not applicable:
            print(f"{label}対象なし: {', '.join(normalized_repos)}")
            return []
        return apply_fn(private_notes, applicable)


def bulk_apply_entries(
    private_notes: pathlib.Path,
    *,
    action: str,
    target_repo: Iterable[str],
    assume_yes: bool,
    force: bool,
    skip_pull: bool,
    status: Iterable[str],
    entry_type: Iterable[str],
    answered: Iterable[str],
    source: Iterable[str] | None,
    actor_is_agent: bool,
    apply_fn: ApplyCandidates,
) -> list[str]:
    """対象リポジトリの候補を一覧表示し、確認後に一括で当該操作を適用する。

    `skip_pull`が真の場合は候補選定・一覧表示・確認をローカル状態で行う。
    適用の直前は`skip_pull`によらずremote同期し、確認済みで内容が変わらない項目だけへ適用する。
    """
    label = BULK_ACTION_LABELS[action]
    source_states = bulk_source_states(action, actor_is_agent=actor_is_agent)
    normalized_repos = tuple(dict.fromkeys(_resolve_repo_id(repo) for repo in target_repo))
    with _repo_lock(private_notes):
        if not skip_pull:
            _pull(private_notes)
        candidates = _select_candidates(
            private_notes,
            normalized_repos,
            status=status,
            entry_type=entry_type,
            answered=answered,
            source=source,
            source_states=source_states,
        )
        readiness = calculate_readiness(private_notes, normalized_repos[0] if len(normalized_repos) == 1 else None)
        confirmed_snapshot = _snapshot(candidates)

    _print_entries(candidates, readiness)
    if not candidates:
        print(f"{label}対象なし: {', '.join(normalized_repos)}")
        return []
    if action == "remove":
        _ensure_processing_is_explicit(candidates, force=force)
    if not assume_yes and not _confirm(len(candidates), label):
        print(f"{label}を中止しました。")
        return []
    return _apply_confirmed_candidates(
        private_notes,
        normalized_repos,
        confirmed_snapshot,
        action=action,
        status=status,
        entry_type=entry_type,
        answered=answered,
        source=source,
        actor_is_agent=actor_is_agent,
        apply_fn=apply_fn,
    )


def remove_all_entries(
    private_notes: pathlib.Path,
    *,
    target_repo: Iterable[str],
    assume_yes: bool,
    force: bool,
    note: str | None,
    skip_pull: bool,
    status: Iterable[str],
    entry_type: Iterable[str],
    answered: Iterable[str],
    source: Iterable[str] | None,
    actor_is_agent: bool,
) -> list[str]:
    """対象リポジトリの候補を一覧表示し、確認後に一括削除する。"""

    def apply_fn(notes: pathlib.Path, candidates: list[QueueEntryDisplay]) -> list[str]:
        return _remove_candidates(notes, candidates, note=note)

    return bulk_apply_entries(
        private_notes,
        action="remove",
        target_repo=target_repo,
        assume_yes=assume_yes,
        force=force,
        skip_pull=skip_pull,
        status=status,
        entry_type=entry_type,
        answered=answered,
        source=source,
        actor_is_agent=actor_is_agent,
        apply_fn=apply_fn,
    )
