"""origin単位の処理所有を専用起動とall-reposで共有する。"""

import dataclasses
import hashlib
import json
import os
import pathlib
import uuid
from typing import Any

import filelock

from agent_toolkit._common import session_registry as _registry
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import remote as _remote


@dataclasses.dataclass(frozen=True)
class Ownership:
    """短い排他で取得した処理所有。previousは未完了の前担当を保持する。"""

    repo_id: str
    token: str
    path: pathlib.Path
    session_id: str | None
    previous: dict[str, Any] | None = None

    @property
    def lane_session_id(self) -> str:
        """異常終了からの再開では前担当のレーン所有を引き継ぐ。"""
        previous_id = self.previous.get("lane_session_id") if self.previous is not None else None
        return previous_id if isinstance(previous_id, str) else self.token


def _path(repo_id: str) -> pathlib.Path:
    digest = hashlib.sha256(repo_id.encode("utf-8")).hexdigest()
    return ownership_directory() / f"{digest}.json"


def ownership_directory() -> pathlib.Path:
    """所有待機が監視する有限のorigin別記録の配置先を返す。"""
    return _state_paths.state_dir() / "process-loop-ownership"


def _read(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("token"), str):
        raise ValueError(f"処理所有記録を解釈できない: {path}")
    return record


def _live(record: dict[str, Any]) -> bool:
    if record.get("completed") is True or record.get("released") is True:
        return False
    pid, created = record.get("pid"), record.get("create_time")
    if not isinstance(pid, int) or not isinstance(created, (int, float)):
        raise ValueError("処理所有記録のプロセス識別が不正")
    if record.get("held") is not True and _registry.ProcessIdentity(pid, created).exists():
        return True
    session_id = record.get("session_id")
    if isinstance(session_id, str):
        resolution = _registry.resolve(session_id)
        return resolution.state in {_registry.Resolution.RUNNING, _registry.Resolution.UNREADABLE}
    return False


def _unfinished_record(record: dict[str, Any]) -> dict[str, Any]:
    """同じ担当が取得し直した記録は前の記録へまとめ、別担当が残した未完了の記録の連鎖を保つ。"""
    previous = record.get("previous")
    if isinstance(previous, dict) and (
        record.get("session_id") is None or record.get("session_id") == previous.get("session_id")
    ):
        return _unfinished_record(previous)
    return record


def try_acquire(
    target_repo_id: str,
    *,
    worktree: pathlib.Path,
    session_id: str | None = None,
    mode: str = "dedicated",
) -> Ownership | None:
    """生存所有者が無いoriginだけを取得し、異常終了の再開情報を返す。"""
    repo_id = _remote.normalize_remote_url(target_repo_id)
    common = _git_command.run(
        ["-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if common.returncode != 0:
        raise ValueError(f"処理所有のGit共通ディレクトリを取得できない: {worktree}: {common.stderr.strip()}")
    path = _path(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(str(path.with_suffix(".lock")), timeout=10):
        prior = _read(path)
        if prior is not None and _live(prior):
            return None
        if prior is not None and prior.get("held") is True:
            prior.update(completed=False, released=True, held=False)
            atomic_write(path, json.dumps(prior, ensure_ascii=False) + "\n")
        identity = _registry.ProcessIdentity.of(os.getpid())
        if identity is None:
            raise RuntimeError("処理所有者のPIDと起動時刻を取得できない")
        token = str(uuid.uuid4())
        previous = _unfinished_record(prior) if prior is not None and prior.get("completed") is not True else None
        record = {
            "repo_id": repo_id,
            "token": token,
            "pid": identity.pid,
            "create_time": identity.create_time,
            "worktree": str(worktree.resolve()),
            "session_id": session_id,
            "mode": mode,
            "common_dir": str(pathlib.Path(common.stdout.strip()).resolve()),
            "completed": False,
            "released": False,
        }
        if previous is not None:
            record["previous"] = previous
        record["lane_session_id"] = previous.get("lane_session_id", token) if previous is not None else token
        atomic_write(path, json.dumps(record, ensure_ascii=False) + "\n")
        return Ownership(repo_id, token, path, session_id, previous)


def resume_repository(session_id: str, repositories: tuple[str, ...]) -> str:
    """指定sessionを登録集合内のall-repos担当へ対応付ける。"""
    for repo_id in repositories:
        record = _read(_path(_remote.normalize_remote_url(repo_id)))
        if (
            record is not None
            and record.get("mode") == "all-repos"
            and record.get("session_id") == session_id
            and record.get("completed") is not True
        ):
            return repo_id
    raise ValueError(f"登録集合内のall-repos担当として再開できないsession: {session_id}")


def unfinished_repository_ids(repositories: tuple[str, ...]) -> set[str]:
    """登録集合内で未完了のall-repos担当がある対象だけを返す。"""
    unfinished: set[str] = set()
    for repo_id in repositories:
        record = _read(_path(_remote.normalize_remote_url(repo_id)))
        if record is not None and record.get("mode") == "all-repos" and record.get("completed") is not True:
            unfinished.add(repo_id)
    return unfinished


def update_session(ownership: Ownership, session_id: str, *, lane_session_id: str | None = None) -> Ownership:
    """同じ所有tokenに担当sessionを結び、再開先を保存する。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたためsessionを更新しなかった")
        record["session_id"] = session_id
        if lane_session_id is not None:
            record["lane_session_id"] = lane_session_id
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")
    return dataclasses.replace(ownership, session_id=session_id)


def release(ownership: Ownership, *, completed: bool = True) -> None:
    """同じtokenだけを解放し、未完了なら成果と再開sessionを保持する。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたため解放しなかった")
        record.update(completed=completed, released=True, held=False)
        if not completed and record.get("session_id") is None and isinstance(record.get("previous"), dict):
            record["session_id"] = record["previous"].get("session_id")
            record["mode"] = record["previous"].get("mode", record["mode"])
        if completed:
            record.pop("previous", None)
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")


def hold(ownership: Ownership) -> None:
    """停止を確認できない担当の所有を保持し、生存照会を担当sessionへ切り替える。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたため保持しなかった")
        record.update(held=True, completed=False, released=False)
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")
