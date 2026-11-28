"""同じprivate-notesを使う全端末の利用枠リセットを排他する予約台帳。  pyfltr: allow-comment

消費の前に共有先へ予約を書き、同期先が受理したことを確かめてから消費を送る。
未確定の試行中や、同じアカウントの週次窓で成功後は、新しい消費を送らない。
応答を失った予約はpendingのまま残し、同じ冪等キーで再送する。
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any, Literal

from agent_toolkit._atk import config_settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
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


class ReservationDenied(ReservationError):
    """共有先でこの試行を受け付けないことが確定した。  pyfltr: allow-comment"""


def shared_notes() -> pathlib.Path | None:
    """同期先を持つprivate-notesを返す。同期先がないときは消費の予約を作らない。  pyfltr: allow-comment"""
    path = config_settings.shared_settings_path()
    if path is None or not git_sync.has_remote(path.parent):
        return None
    return path.parent


def permission_entries(settings: dict[str, str]) -> list[dict[str, Any]]:
    return json.loads(settings.get(PERMISSIONS, "[]"))


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


def _weekly_upstream(notes: pathlib.Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    if not git_sync.has_remote(notes):
        raise ReservationError(
            "利用枠のreset予約に必要な共有先がありません",
            next_action="private-notesの共有先を設定する",
        )
    upstream = git_command.output(["rev-parse", "@{u}"], notes)
    names = git_command.output(
        ["ls-tree", "--name-only", upstream, "--", config_settings.SHARED_SETTINGS_FILENAME, FILENAME], notes
    ).splitlines()
    settings = (
        json.loads(git_command.output(["show", f"{upstream}:{config_settings.SHARED_SETTINGS_FILENAME}"], notes))
        if config_settings.SHARED_SETTINGS_FILENAME in names
        else {}
    )
    if not isinstance(settings, dict):
        raise ValueError("共有の利用枠設定の形式が不正")
    records = _parse(git_command.output(["show", f"{upstream}:{FILENAME}"], notes)) if FILENAME in names else []
    for item in records:
        if item["status"] == "pending" or any(name in item for name in ("key", "account", "resets_at")):
            valid = (
                isinstance(item.get("key"), str)
                and bool(item["key"])
                and isinstance(item.get("account"), str)
                and bool(item["account"])
                and type(item.get("resets_at")) is int
                and item["resets_at"] > 0
                and ("credit_id" not in item or item["credit_id"] is None or isinstance(item["credit_id"], str))
            )
            if not valid:
                raise ValueError(f"{FILENAME}の試行の形式が不正")
    return settings, records


def reserve_weekly(
    notes: pathlib.Path,
    *,
    key: str,
    account: str,
    resets_at: int,
    credit_id: str | None,
    legacy_permission_id: str | None = None,
) -> Status | None:
    """同一内容の記録があれば状態を返し、新規予約を確定した場合はNoneを返す。  pyfltr: allow-comment"""
    attempt = {"key": key, "account": account, "resets_at": resets_at, "credit_id": credit_id}

    def check() -> tuple[Status | None, list[dict[str, Any]]]:
        settings, records = _weekly_upstream(notes)
        if settings.get("codex_auto_reset", "false") != "true":
            raise ReservationError(
                "共有のCodex自動リセット設定が有効ではありません",
                next_action="同じprivate-notesの共有設定を確認する",
            )
        if any(item.get(ISSUED) is True and item.get(RETIRED) is not True for item in permission_entries(settings)):
            raise ReservationError(
                "旧版向けの共有許可が残っているため、新しい試行を開始しません",
                next_action="全端末の更新後に旧共有許可と台帳を確認する",
            )
        for item in records:
            if item.get("key") == key:
                if all(item.get(name) == attempt[name] for name in ("account", "resets_at")) and (
                    ("credit_id" in item and item["credit_id"] == credit_id)
                    or ("credit_id" not in item and item["permission_id"] == legacy_permission_id)
                ):
                    return item["status"], records
                raise ReservationError(
                    "同じキーの共有予約と試行内容が一致しません",
                    next_action="既存の共有予約と送信結果を確認する",
                )
        if any(item["status"] == "pending" for item in records) or any(
            item["status"] == "success" and item.get("account") == account and item["resets_at"] >= resets_at
            for item in records
        ):
            raise ReservationDenied(
                "未確定の試行または同じ週次窓以降の成功が共有先にあります",
                next_action="既存の予約と利用枠の最新状態を確認する",
            )
        return None, records

    with wi_sync.repo_lock(notes):
        wi_sync.pull(notes)
        status, records = check()
        if status is not None:
            return status
        if legacy_permission_id is not None:
            raise ReservationError(
                "旧試行の未確定予約を共有先で確認できません",
                next_action="新しい消費を送らず、旧版の送信結果と共有台帳を確認する",
            )
        if _read(notes) != records:
            raise ReservationError(
                "共有先とこの端末のリセット台帳が一致しません",
                next_action="private-notesのGit同期を確認する",
            )
        _write(notes, [*records, attempt | {"permission_id": "weekly", "status": "pending"}])

        def verify() -> None:
            concurrent_status, _ = check()
            if concurrent_status is not None:
                raise ReservationError(
                    "同じ試行が先に共有先へ書き込まれました",
                    next_action="同じキーと内容で共有予約を確認する",
                )

        _commit(notes, "chore: reserve weekly usage reset", verify)
        return None


def finish(notes: pathlib.Path, key: str, status: Status) -> None:
    """未確定の予約だけを確定する。同期できなければ共有の未確定予約が残る。  pyfltr: allow-comment"""
    with wi_sync.repo_lock(notes):
        wi_sync.pull(notes)
        records = _read(notes)
        changed = [
            dict(item, status=status) if item.get("key") == key and item["status"] == "pending" else item for item in records
        ]
        if changed == records:
            return
        _write(notes, changed)
        _commit(notes, f"chore: record usage reset {status}", lambda: None)
