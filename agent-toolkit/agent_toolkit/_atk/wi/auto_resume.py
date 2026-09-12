"""process-loopの中断セッションを自動特定し、確認のうえ再開するための対話処理。

対象リポジトリで`agent-toolkit:process-wi`を起動した本体セッションをmtime降順に列挙し、
候補ごとの情報を表示して標準入力からy/nを受け付ける。全候補が拒否された場合と候補が
存在しない場合はいずれも標準エラーへ診断を出力し、`sys.exit(1)`で終了する。
"""

from __future__ import annotations

import pathlib
import sys

from agent_toolkit._atk import session_records as _session_records

_EXIT_SESSION_LABELS: dict[bool | None, str] = {True: "あり", False: "なし", None: "判定不能"}


def _collect_candidates(target_repo_id: str) -> list[tuple[pathlib.Path, str, str]]:
    """対象リポジトリでprocess-wiを起動した候補をmtime降順で返す。"""
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
    """確定文面（候補情報の書式）に従い候補を1件表示する。"""
    exit_session_label = _EXIT_SESSION_LABELS[_session_records.exit_session_reached(path, engine)]
    print(f"候補 {rank}/{total}: engine={engine} session_id={session_id}")
    print(f"  最終更新: {_session_records.format_modified_at(path)}")
    print(f"  exit-session到達: {exit_session_label}")
    print(f"  最初のユーザー入力: {_session_records.first_user_input(path, engine)}")
    print(f"  最後のエージェント発言: {_session_records.last_agent_message(path, engine)}")


def select_session(target_repo_id: str, target_repo_path: pathlib.Path) -> str:
    """対象リポジトリの中断セッションを表示・確認し、確定したsession_idを返す。

    候補が無い場合と全候補が拒否された場合は診断を表示して`sys.exit(1)`する。
    """
    candidates = _collect_candidates(target_repo_id)
    if not candidates:
        print(
            f"error: 対象リポジトリでagent-toolkit:process-wiを起動したセッション記録が見つからない: {target_repo_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    total = len(candidates)
    for rank, (path, engine, session_id) in enumerate(candidates, start=1):
        _print_candidate(rank, total, path, engine, session_id)
        answer = input("このセッションを再開しますか [y/n]: ")
        if answer.strip().lower() == "y":
            return session_id

    print(f"error: すべての候補が拒否された: {total}件", file=sys.stderr)
    sys.exit(1)
