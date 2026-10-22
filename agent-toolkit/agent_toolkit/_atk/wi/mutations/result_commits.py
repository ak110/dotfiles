"""終端したAWIの対応commitを、状態と処理日時を保持して公開保存処理へ反映する。"""

from __future__ import annotations

import pathlib
import re

from agent_toolkit._atk import git_sync
from agent_toolkit._atk.wi import entries, sync
from agent_toolkit._atk.wi.mutations import targets
from agent_toolkit._atk.wi.repo import resolve_repo_id, verify_target_repo_content
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import commit_mapping

_PAIR = re.compile(r"(?m)^- 対応commit: ([0-9a-f]{7,64})\n- 対応commit件名: [^\n]*\n")
_RETRY = (
    "private-notesの同期・保存エラーを解消し、同じ引数のplan-rewriteを再実行する。"
    "更新済みの対応は保持され、残りのAWIと計画・引き継ぎ記録へ反映する"
)


def _rewritten_result(text: str, filename: str, worktree: pathlib.Path, rewrite: commit_mapping.RewriteMap) -> str:
    """最後の処理結果の対応commitと件名を更新し、他の本文を保持する。"""
    before, heading, result = text.rpartition("\n## 処理結果\n")
    if not heading:
        return text
    seen: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        old = match[1]
        candidates = [oid for oid in rewrite if oid.startswith(old)]
        if not candidates:
            return match[0]
        if len(candidates) != 1:
            raise ActionableError(f"対応commitの短縮OIDが曖昧です: {filename}: {old}", next_action=_RETRY)
        destinations = commit_mapping.rewrite_destinations(rewrite[candidates[0]])
        assigned = [oid for oid, wis in destinations if wis is None or filename in (wis or set())]
        if not assigned:
            raise ActionableError(f"分割先へのAWI割当が不足しています: {filename}: {old}", next_action=_RETRY)
        lines: list[str] = []
        for oid in assigned:
            if oid in seen:
                continue
            seen.add(oid)
            commit = targets.resolve_commit(worktree, oid)
            lines.extend((f"- 対応commit: {commit.short_oid}\n", f"- 対応commit件名: {commit.subject}\n"))
        return "".join(lines)

    return before + heading + _PAIR.sub(replace, result)


def rewrite_adopted_commits(
    private_notes: pathlib.Path,
    *,
    worktree: pathlib.Path,
    filenames: set[str],
    rewrite: commit_mapping.RewriteMap,
) -> list[str]:
    """指定AWIの処理結果へ対応表を適用し、排他・commit・pushを行い、保存本文との一致を確かめる。

    計画の追記前に呼ぶ。push失敗後の再実行は滞留commitのpushから再開する。
    対応表に無いcommitと採否・日時・メモを保持し、分割先はAWIごとの割当を使う。
    """
    if not filenames or not private_notes.is_dir():
        return []
    with sync.repo_lock(private_notes):
        sync.ensure_mutation_allowed(private_notes)
        sync.push_pending_commits(private_notes)
        sync.pull(private_notes)
        selected, _missing = entries.read_named_entries(
            private_notes, entries.validate_named_filenames(private_notes, sorted(filenames)), target_repo=None
        )
        updates: dict[pathlib.Path, str] = {}
        expected: dict[pathlib.Path, str] = {}
        for path, _repo, text, state, kind in selected:
            if state != "adopted" or kind != "awi":
                continue
            verify_target_repo_content(path, text, resolve_repo_id(str(worktree)))
            updated = _rewritten_result(text, path.name, worktree, rewrite)
            expected[path] = updated
            if updated != text:
                updates[path] = updated
        # 全対象の変換と割当を確定してから保存する。途中で失敗しても再実行は同じ変換になる。
        for path, text in updates.items():
            targets.atomic_write_text(path, text)
        if expected:
            sync.commit_and_push(
                private_notes,
                "chore: 終端AWIの対応commitを履歴書換えへ追随",
                [str(path.relative_to(private_notes)) for path in expected],
            )
            if sync.has_remote(private_notes) and not git_sync.remote_contains_head(private_notes):
                raise ActionableError("終端AWIの保存commitがupstreamへ反映されていません", next_action=_RETRY)
        for path, content in expected.items():
            if path.read_text(encoding="utf-8") != content:
                raise ActionableError(f"保存後の本文が一致しません: {path}", next_action=_RETRY)
        return [path.name for path in updates]
