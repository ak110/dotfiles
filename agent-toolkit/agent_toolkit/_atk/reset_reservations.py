"""共有した利用枠のreset許可の回数を、同じprivate-notesを使う全端末の合計で数える予約台帳。  pyfltr: allow-comment

消費の前に共有先へ予約を書き、同期先が受理したことを確かめてから消費を送る。
予約の件数は確定（success）と未確定（pending）の合計で数え、拒否が確定した予約（closed）だけを除く。
応答を失った予約はpendingのまま残し、同じ冪等キーで再送する。
"""

from __future__ import annotations

import json
import pathlib
import socket
import subprocess
import time
from collections.abc import Iterable
from typing import Any, Literal

from agent_toolkit._atk import config_settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.usage_settings import deadline
lazy from agent_toolkit._atk import git_sync
lazy from agent_toolkit._atk.wi import sync as wi_sync
lazy from agent_toolkit._git import command as git_command

FILENAME = "atk-reset-reservations.json"

type Status = Literal["pending", "success", "closed"]


class ReservationError(_next_action.ActionableError):
    """共有先で予約を確定できない。消費を送ってはならない。  pyfltr: allow-comment"""


def shared_notes() -> pathlib.Path | None:
    """同期先を持つprivate-notesを返す。無い環境は端末内の記録だけで数える。  pyfltr: allow-comment"""
    path = config_settings.shared_settings_path()
    if path is None or not git_sync.has_remote(path.parent):
        return None
    return path.parent


def _parse(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    records = data.get("reservations") if isinstance(data, dict) else None
    if not isinstance(records, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("permission_id"), str)
        or not isinstance(item.get("count", 1), int)
        or item.get("status") not in ("pending", "success", "closed")
        for item in records
    ):
        raise ValueError(f"{FILENAME}の形式が不正")
    return records


def _read(notes: pathlib.Path) -> list[dict[str, Any]]:
    try:
        return _parse((notes / FILENAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _read_upstream(notes: pathlib.Path) -> list[dict[str, Any]]:
    result = git_command.run(["show", f"@{{u}}:{FILENAME}"], cwd=notes, capture_output=True, text=True, check=False)
    return _parse(result.stdout) if result.returncode == 0 else []


def used(records: Iterable[dict[str, Any]], permission_id: str) -> int:
    return sum(
        int(item.get("count", 1))
        for item in records
        if item["permission_id"] == permission_id and item["status"] in ("pending", "success")
    )


def read_used(notes: pathlib.Path, permission_id: str) -> int:
    """現在の作業ツリーにある台帳の使用数。判定の確定は`reserve`が共有先で行う。  pyfltr: allow-comment"""
    return used(_read(notes), permission_id)


def _prune(records: list[dict[str, Any]], permissions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    # 許可期間が終わった許可の記録は、回数の判定にも同じ冪等キーの再送にも使わないため整理する。  # pyfltr: allow-comment
    open_ids = {item["id"] for item in permissions if now < deadline(item["until"])}
    return [item for item in records if item["permission_id"] in open_ids]


def _write(notes: pathlib.Path, records: list[dict[str, Any]]) -> None:
    atomic_write(notes / FILENAME, json.dumps({"reservations": records}, ensure_ascii=False, indent=2) + "\n", fsync=True)


def _commit(notes: pathlib.Path, message: str, verify: Any) -> None:
    try:
        wi_sync.commit_guarded(notes, message, [FILENAME], verify)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        git_command.run(["checkout", "HEAD", "--", FILENAME], cwd=notes, check=False)
        raise ReservationError(
            f"利用枠のreset予約を共有先へ確定できない: {error}",
            next_action=f"{notes}のGit同期を確認する。予約できるまで消費は送らない",
        ) from error
    if not git_sync.remote_contains_head(notes):
        raise ReservationError(
            "利用枠のreset予約が共有先で確認できない",
            next_action=f"{notes}のGit同期を確認する。予約できるまで消費は送らない",
        )


def reserve(
    notes: pathlib.Path,
    *,
    permission_id: str,
    limit: int,
    key: str,
    account: str,
    resets_at: int,
    carried: int,
    permissions: list[dict[str, Any]],
    now: float | None = None,
) -> None:
    """共有先で予約を確定する。上限を超える場合と共有先が受理しない場合は`ReservationError`を送出する。  pyfltr: allow-comment

    `carried`は共有を始める前に端末内で数えた使用数で、初回の予約と同時に台帳へ移す。
    """
    with wi_sync.repo_lock(notes):
        wi_sync.pull(notes)
        records = _prune(_read(notes), permissions, time.time() if now is None else now)
        additions: list[dict[str, Any]] = []
        if carried:
            additions.append(
                {
                    "permission_id": permission_id,
                    "count": carried,
                    "status": "success",
                    "host": socket.gethostname(),
                    "carried": True,
                }
            )
        additions.append(
            {
                "permission_id": permission_id,
                "key": key,
                "account": account,
                "resets_at": resets_at,
                "status": "pending",
                "host": socket.gethostname(),
            }
        )
        needed = sum(int(item.get("count", 1)) for item in additions)

        def check(current: list[dict[str, Any]]) -> None:
            if used(current, permission_id) + needed > limit:
                raise ReservationError(
                    "共有先で数えたreset許可の回数が上限に達している",
                    next_action="usage_reset_permissionsの許可と共有先の予約台帳を確認する",
                )

        check(records)
        _write(notes, records + additions)
        _commit(notes, "chore: reserve usage reset", lambda: check(_read_upstream(notes)))


def finish(notes: pathlib.Path, key: str, status: Status) -> None:
    """予約の結果を記録する。closedにすると件数から外れる。同期できなくても件数は多い側に残る。  pyfltr: allow-comment"""
    with wi_sync.repo_lock(notes):
        wi_sync.pull(notes)
        records = _read(notes)
        changed = [dict(item, status=status) if item.get("key") == key else item for item in records]
        if changed == records:
            return
        _write(notes, changed)
        _commit(notes, f"chore: record usage reset {status}", lambda: None)
