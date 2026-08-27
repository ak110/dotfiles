"""`atk mq rm --all`の対象選択・確認・削除を提供する。"""

import pathlib
import sys
import typing

from _atk_mq_common import (
    MQ_STATE_EDITING,
    MQ_STATE_HOLD,
    MQ_STATE_INBOX,
    MQ_STATE_PLANNING,
    MQ_STATE_PROCESSING,
    MQ_STATES,
    MQ_TYPE_TBD,
    MQ_TYPES,
    _commit_and_push,
    _iter_entries,
    _pull,
    _push_pending_commits,
    _repo_lock,
    _subdir,
    calculate_readiness,
)
from _atk_mq_list import QueueEntryDisplay, _print_entries
from _atk_mq_repo import _resolve_repo_id

_REMOVABLE_COMMIT_STATES = tuple(state for state in MQ_STATES if state not in {MQ_STATE_EDITING, MQ_STATE_HOLD})


class CandidateKey(typing.NamedTuple):
    """候補の同一性を判定する組。"""

    state: str
    name: str
    target_repo: str
    text: str
    entry_type: str


type CandidateSnapshot = tuple[CandidateKey, ...]


def _select_candidates(
    private_notes: pathlib.Path,
    target_repo: str,
) -> list[QueueEntryDisplay]:
    """削除対象の状態から同じ正規リポジトリと有効な種別を選択する。"""
    removable_states = (MQ_STATE_INBOX, MQ_STATE_PLANNING, MQ_STATE_PROCESSING)
    return [
        entry
        for entry in _iter_entries(private_notes, removable_states, target_repo, "all")
        if entry[4] in MQ_TYPES
        and entry[3] not in {MQ_STATE_EDITING, MQ_STATE_HOLD}
        and not (entry[3] == MQ_STATE_PLANNING and entry[4] == MQ_TYPE_TBD)
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
    """planningまたはprocessing候補がある場合に明示的な保護解除を要求する。"""
    protected = [
        path.name for path, _repo, _text, state, _type in candidates if state in {MQ_STATE_PLANNING, MQ_STATE_PROCESSING}
    ]
    if protected and not force:
        print(
            "planning・processing状態のファイルは既定で削除を保護します。"
            f"削除するには--forceを指定してください: {', '.join(protected)}",
            file=sys.stderr,
        )
        sys.exit(2)


def _confirm_removal(count: int) -> bool:
    """対話端末で一括削除を1回確認する。"""
    if not sys.stdin.isatty():
        print("非対話環境で一括削除するには--yesを指定してください。", file=sys.stderr)
        sys.exit(2)
    answer = input(f"上記{count}件を削除します。続行しますか？ [y/N]: ")
    return answer.strip().casefold() in {"y", "yes"}


def _remove_candidates(
    private_notes: pathlib.Path,
    candidates: list[QueueEntryDisplay],
    *,
    note: str | None,
) -> list[str]:
    """ロック保持下で候補を削除し、単一commit・pushへまとめる。"""
    for path, _repo, _text, _state, _type in candidates:
        path.unlink()
    for state_name in _REMOVABLE_COMMIT_STATES:
        _subdir(private_notes, state_name)
    count = len(candidates)
    item_word = "entry" if count == 1 else "entries"
    note_suffix = f" (理由: {note})" if note else ""
    _commit_and_push(
        private_notes,
        f"chore: remove {count} {item_word}{note_suffix}",
        list(_REMOVABLE_COMMIT_STATES),
    )
    return [path.name for path, _repo, _text, _state, _type in candidates]


def _remove_confirmed_candidates(
    private_notes: pathlib.Path,
    normalized_repo: str,
    confirmed: CandidateSnapshot,
    *,
    note: str | None,
) -> list[str]:
    """remote同期後の候補を確認済み記録と突合し、内容が変わらない項目だけ削除する。

    同期後に新規出現した項目は確認を経ていないため削除しない。
    確認済みでも状態移動・本文変更・消失で記録と一致しなくなった項目は削除せず標準出力へ報告する。
    """
    confirmed_keys = set(confirmed)
    with _repo_lock(private_notes):
        _push_pending_commits(private_notes)
        _pull(private_notes)
        current = _select_candidates(private_notes, normalized_repo)
        current_keys = {_candidate_key(entry) for entry in current}
        removable = [entry for entry in current if _candidate_key(entry) in confirmed_keys]
        changed = [key.name for key in confirmed if key not in current_keys]
        if changed:
            print(f"確認後に変更されたため削除しません: {', '.join(changed)}")
        if not removable:
            print(f"削除対象なし: {normalized_repo}")
            return []
        return _remove_candidates(private_notes, removable, note=note)


def remove_all_entries(
    private_notes: pathlib.Path,
    *,
    target_repo: str,
    assume_yes: bool,
    force: bool,
    note: str | None,
    skip_pull: bool,
) -> list[str]:
    """対象リポジトリのactive項目を一覧表示し、確認後に一括削除する。

    `skip_pull`が真の場合は候補選定・一覧表示・確認をローカル状態で行う。
    削除直前は`skip_pull`によらずremote同期し、確認済みで内容が変わらない項目だけを削除する。
    """
    normalized_repo = _resolve_repo_id(target_repo)
    with _repo_lock(private_notes):
        _push_pending_commits(private_notes)
        if not skip_pull:
            _pull(private_notes)
        candidates = _select_candidates(private_notes, normalized_repo)
        readiness = calculate_readiness(private_notes, normalized_repo)
        confirmed_snapshot = _snapshot(candidates)

    _print_entries(candidates, readiness)
    if not candidates:
        print(f"削除対象なし: {normalized_repo}")
        return []
    _ensure_processing_is_explicit(candidates, force=force)
    if not assume_yes and not _confirm_removal(len(candidates)):
        print("削除を中止しました。")
        return []
    return _remove_confirmed_candidates(private_notes, normalized_repo, confirmed_snapshot, note=note)
