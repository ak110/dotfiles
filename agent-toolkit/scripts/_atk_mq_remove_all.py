"""`atk mq rm --all`の対象選択・確認・削除を提供する。"""

import pathlib
import sys

from _atk_mq_common import (
    MQ_ACTIVE_STATES,
    MQ_STATE_PROCESSING,
    MQ_STATES,
    MQ_TYPES,
    _commit_and_push,
    _iter_entries,
    _pull,
    _repo_lock,
    calculate_readiness,
)
from _atk_mq_list import QueueEntryDisplay, _print_entries
from _atk_mq_repo import _resolve_repo_id

type CandidateSnapshot = tuple[tuple[str, str, str, str, str], ...]


def _select_candidates(
    private_notes: pathlib.Path,
    target_repo: str,
) -> list[QueueEntryDisplay]:
    """active項目から対象リポジトリと種別を完全一致で選択する。"""
    return [
        entry
        for entry in _iter_entries(private_notes, MQ_ACTIVE_STATES, None, "all")
        if entry[1] == target_repo and entry[4] in MQ_TYPES
    ]


def _snapshot(candidates: list[QueueEntryDisplay]) -> CandidateSnapshot:
    """候補の状態・名前・対象リポジトリ・本文・種別を比較可能な記録へ変換する。"""
    return tuple(
        (state, path.name, target_repo, text, entry_type)
        for path, target_repo, text, state, entry_type in candidates
        if entry_type is not None
    )


def _ensure_processing_is_explicit(
    candidates: list[QueueEntryDisplay],
    *,
    force: bool,
) -> None:
    """processing候補がある場合に明示的な保護解除を要求する。"""
    protected = [path.name for path, _repo, _text, state, _type in candidates if state == MQ_STATE_PROCESSING]
    if protected and not force:
        print(
            f"processing状態のファイルは既定で削除を保護します。削除するには--forceを指定してください: {', '.join(protected)}",
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
    count = len(candidates)
    item_word = "entry" if count == 1 else "entries"
    note_suffix = f" (理由: {note})" if note else ""
    _commit_and_push(
        private_notes,
        f"chore: remove {count} {item_word}{note_suffix}",
        list(MQ_STATES),
    )
    return [path.name for path, _repo, _text, _state, _type in candidates]


def remove_all_entries(
    private_notes: pathlib.Path,
    *,
    target_repo: str,
    assume_yes: bool,
    force: bool,
    note: str | None,
) -> list[str]:
    """対象リポジトリのactive項目を一覧表示し、確認後に一括削除する。"""
    normalized_repo = _resolve_repo_id(target_repo)
    with _repo_lock(private_notes):
        _pull(private_notes)
        candidates = _select_candidates(private_notes, normalized_repo)
        ready = frozenset(calculate_readiness(private_notes, normalized_repo).ready)
        if assume_yes:
            _print_entries(candidates, ready)
            if not candidates:
                print(f"削除対象なし: {normalized_repo}")
                return []
            _ensure_processing_is_explicit(candidates, force=force)
            return _remove_candidates(private_notes, candidates, note=note)
        confirmed_snapshot = _snapshot(candidates)

    _print_entries(candidates, ready)
    if not candidates:
        print(f"削除対象なし: {normalized_repo}")
        return []
    _ensure_processing_is_explicit(candidates, force=force)
    if not _confirm_removal(len(candidates)):
        print("削除を中止しました。")
        return []

    with _repo_lock(private_notes):
        _pull(private_notes)
        current = _select_candidates(private_notes, normalized_repo)
        if _snapshot(current) != confirmed_snapshot:
            print(
                "確認後に削除対象が変更されたため削除しません。再実行して一覧を確認してください。",
                file=sys.stderr,
            )
            sys.exit(2)
        return _remove_candidates(private_notes, current, note=note)
