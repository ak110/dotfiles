from __future__ import annotations

import pathlib
import sys

from agent_toolkit._atk import session_records as _session_records
from agent_toolkit._common import next_action as _next_action

_NO_CANDIDATE_NEXT_ACTION = (
    "`--auto-resume`を外して`--resume <session_id>`で再開するセッションを指定するか、"
    "`--auto-resume`と`--resume`のどちらも付けずに新規に起動する"
)

_EXIT_SESSION_LABELS: dict[bool | None, str] = {True: "あり", False: "なし", None: "判定不能"}


def _collect_candidates(target_repo_id: str) -> list[tuple[pathlib.Path, str, str]]:
    dated: list[tuple[float, pathlib.Path, str, str]] = []
    for path, engine, session_id in _session_records.candidate_paths():
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            continue
        dated.append((modified_at, path, engine, session_id))

    repository_cache: dict[str, str | None] = {}
    selected: list[tuple[float, pathlib.Path, str, str]] = []
    for modified_at, path, engine, session_id in dated:
        cwd = _session_records.session_cwd(path, engine)
        if cwd is None or _session_records.resolved_repo(cwd, repository_cache) != target_repo_id:
            continue
        if not _session_records.invoked_process_wi(path, engine):
            continue
        selected.append((modified_at, path, engine, session_id))
    selected.sort(reverse=True)
    return [(path, engine, session_id) for _modified_at, path, engine, session_id in selected]


def _print_candidate(rank: int, total: int, path: pathlib.Path, engine: str, session_id: str) -> None:
    exit_session_label = _EXIT_SESSION_LABELS[_session_records.exit_session_reached(path, engine)]
    print(f"候補 {rank}/{total}: engine={engine} session_id={session_id}")
    print(f"  最終更新: {_session_records.format_modified_at(path)}")
    print(f"  exit-session到達: {exit_session_label}")
    print(f"  最初のユーザー入力: {_session_records.first_user_input(path, engine)}")
    print(f"  最後のエージェント発言: {_session_records.last_agent_message(path, engine)}")


def select_session(target_repo_id: str, target_repo_path: pathlib.Path) -> str:
    candidates = _collect_candidates(target_repo_id)
    if not candidates:
        _next_action.report(
            f"error: 対象リポジトリで作業process-wiを起動したセッション記録が見つからない: {target_repo_path}",
            next_action=_NO_CANDIDATE_NEXT_ACTION,
        )
        sys.exit(1)

    total = len(candidates)
    for rank, (path, engine, session_id) in enumerate(candidates, start=1):
        _print_candidate(rank, total, path, engine, session_id)
        try:
            answer = input("このセッションを再開しますか [y/n]: ")
        except EOFError:
            _next_action.report(
                "error: 標準入力が終了したため候補を選択できない。",
                next_action=f"`--auto-resume`を外し、`--resume {session_id}`を指定して再実行してください",
            )
            sys.exit(1)
        if answer.strip().lower() == "y":
            return session_id

    _next_action.report(f"error: すべての候補が拒否された: {total}件", next_action=_NO_CANDIDATE_NEXT_ACTION)
    sys.exit(1)
