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
PERMISSIONS = "usage_reset_permissions"
ISSUED = "_atk_shared_issued"
RETIRED = "_atk_shared_retired"

type Status = Literal["pending", "success", "closed"]


class ReservationError(_next_action.ActionableError):
    """共有先で予約を確定できない。消費を送ってはならない。  pyfltr: allow-comment"""


def shared_notes() -> pathlib.Path | None:
    """同期先を持つprivate-notesを返す。無い環境は端末内の記録だけで数える。  pyfltr: allow-comment"""
    path = config_settings.shared_settings_path()
    if path is None or not git_sync.has_remote(path.parent):
        return None
    return path.parent


def permission_entries(settings: dict[str, str]) -> list[dict[str, Any]]:
    return json.loads(settings.get(PERMISSIONS, "[]"))


def encode_permissions(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, ensure_ascii=False, sort_keys=True)


def _public_grant(grant: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in grant.items() if key not in {ISSUED, RETIRED}}


def public_permissions(value: str) -> str:
    entries = json.loads(value)
    if any(ISSUED in item or RETIRED in item for item in entries):
        return encode_permissions([_public_grant(item) for item in entries if item.get(RETIRED) is not True])
    return value


def retire_grant(grant: dict[str, Any]) -> dict[str, Any]:
    return dict(grant, count=0) | {RETIRED: True}


def _with_retirements(current: dict[str, str], entries: list[dict[str, Any]]) -> str:
    identifiers = {item["id"] for item in entries}
    for old in permission_entries(current):
        # 別端末から古い一覧を保存しても、停止した旧許可を新規許可として復活させない。  # pyfltr: allow-comment
        if old.get(RETIRED) is True and old["id"] in identifiers:
            raise ValueError("発行し直した旧許可は復元できません。共有の値を使ってください")
        if old["engine"] == "codex" and old.get(ISSUED) is not True and old["id"] not in identifiers:
            entries.append(retire_grant(old))
    return encode_permissions(entries)


def migrate_permissions(current: dict[str, str], value: str) -> str:
    issued = {
        grant["id"]: grant
        for grant in permission_entries(current)
        if grant.get(ISSUED) is True and grant.get(RETIRED) is not True
    }
    entries = []
    for item in json.loads(public_permissions(value)):
        grant = issued.get(item["id"])
        entries.append(grant if grant is not None and _public_grant(grant) == item else item)
    return _with_retirements(current, entries)


def mark_new_permissions(current: dict[str, str], value: str) -> str:
    previous = {item["id"]: item.get(ISSUED) is True for item in permission_entries(current)}
    entries = [{key: val for key, val in item.items() if key not in {ISSUED, RETIRED}} for item in json.loads(value)]
    for item in entries:
        if item["engine"] == "codex" and previous.get(item["id"], True):
            item[ISSUED] = True
    return _with_retirements(current, entries)


def _active_grant(notes: pathlib.Path, permission: dict[str, Any], *, upstream: bool = False) -> dict[str, Any]:
    path = notes / config_settings.SHARED_SETTINGS_FILENAME
    if upstream:
        result = git_command.run(["show", f"@{{u}}:{path.name}"], cwd=notes, capture_output=True, text=True, check=False)
        current = json.loads(result.stdout) if result.returncode == 0 else {}
    else:
        current = config_settings.load_shared_settings(path)
    for entry in permission_entries(current):
        if entry.get(RETIRED) is not True and _public_grant(entry) == permission:
            return entry
    return {}


def is_issued(notes: pathlib.Path, permission: dict[str, Any]) -> bool:
    return _active_grant(notes, permission).get(ISSUED) is True


def _legacy_unconfirmed() -> ReservationError:
    return ReservationError(
        "旧端末の消費回数を確認できないため、この許可から新しい消費は送らない",
        next_action="設定画面の利用枠で旧許可を確認し、必要な回数を共有許可として発行し直す",
    )


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


def local_unshared_usage(state_dir: pathlib.Path, permission_id: str) -> int:
    root = state_dir / "agents-server" / "codex-auto-reset"
    try:
        paths = list(root.iterdir())
    except FileNotFoundError:
        return 0
    total = 0
    for path in paths:
        if path.suffix != ".json":
            continue
        try:
            attempt = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if not isinstance(attempt, dict):
            raise ValueError("端末のCodex reset記録が不正です")
        carried = attempt.get("carried_permissions", [])
        used_permissions = attempt.get("used_permissions", {})
        if not isinstance(carried, list) or not isinstance(used_permissions, dict):
            raise ValueError("端末のCodex reset記録が不正です")
        if permission_id not in carried:
            count = used_permissions.get(permission_id, 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("端末のCodex reset記録の使用回数が不正です")
            total += count
    return total


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
        if git_command.optional_stdout(["ls-tree", "HEAD", "--", FILENAME], notes):
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
    permission: dict[str, Any],
    key: str,
    account: str,
    resets_at: int,
    carried: int,
    permissions: list[dict[str, Any]],
    now: float | None = None,
    retry: bool = False,
) -> None:
    """共有先で予約を確定する。上限を超える場合と共有先が受理しない場合は`ReservationError`を送出する。  pyfltr: allow-comment

    `carried`は共有を始める前に端末内で数えた使用数で、初回の予約と同時に台帳へ移す。
    """
    with wi_sync.repo_lock(notes):
        wi_sync.pull(notes)
        permission_id, limit = permission["id"], permission["count"]

        def check_grant(*, upstream: bool = False) -> None:
            grant = _active_grant(notes, permission, upstream=upstream)
            if not grant or (not retry and grant.get(ISSUED) is not True):
                raise _legacy_unconfirmed()

        check_grant()
        upstream_records = _read_upstream(notes)
        if retry and any(
            item.get("key") == key
            and item["permission_id"] == permission_id
            and item.get("account") == account
            and item["status"] == "pending"
            for item in upstream_records
        ):
            check_grant(upstream=True)
            return
        records = _prune(_read(notes), permissions, time.time() if now is None else now)
        if retry and any(item.get("key") == key for item in records):
            raise _legacy_unconfirmed()
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

        def verify() -> None:
            check_grant(upstream=True)
            check(_read_upstream(notes))

        _commit(notes, "chore: reserve usage reset", verify)


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
