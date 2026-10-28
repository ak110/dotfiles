"""統括の処理所有をorigin単位で保持し、未完了成果を再開へ渡す。"""

import dataclasses
import hashlib
import json
import os
import pathlib
import uuid
from typing import Any

import filelock

from agent_toolkit._common import session_launchers as _launchers
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
    if _executor_live(record):
        return True
    pid, created = record.get("pid"), record.get("create_time")
    if not isinstance(pid, int) or not isinstance(created, (int, float)):
        raise ValueError("処理所有記録のプロセス識別が不正")
    if record.get("held") is not True and _registry.ProcessIdentity(pid, created).exists():
        return True
    sessions = {value for value in [record.get("session_id"), *record.get("writer_sessions", [])] if isinstance(value, str)}
    for writer in descendant_sessions(sessions):
        if _registry.resolve(writer).state in {
            _registry.Resolution.RUNNING,
            _registry.Resolution.UNREADABLE,
        }:
            return True
    return False


def descendant_sessions(roots: set[str]) -> set[str]:
    """統括と申告済みwriterに属する配下sessionを、既存の起動元記録から取得する。"""
    paths = tuple(_registry.registry_directory().glob("*.json"))
    descendants = set(roots)
    while True:
        children = {path.stem for path in paths if _launchers.read_launcher(path) in descendants}
        if children <= descendants:
            return descendants
        descendants.update(children)


def _executor_live(record: dict[str, Any]) -> bool:
    executor = record.get("executor_process")
    if executor is None:
        return False
    if (
        not isinstance(executor, dict)
        or not isinstance(executor.get("pid"), int)
        or not isinstance(executor.get("create_time"), (int, float))
    ):
        raise ValueError("統括CLIのプロセス識別が不正")
    return _registry.ProcessIdentity(executor["pid"], executor["create_time"]).exists()


def executor_running(ownership: Ownership) -> bool:
    """会話IDの検出前でも、保存済みの統括CLIの生存を確認する。"""
    record = _read(ownership.path)
    if record is None or record["token"] != ownership.token:
        raise ValueError("処理所有者が変更されたため統括CLIを確認できない")
    return _executor_live(record)


def update_process(ownership: Ownership, process: _registry.ProcessIdentity) -> None:
    """Popenが開始した実行主体の寿命を取得済み所有へ結び付ける。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたため統括CLIを保存しなかった")
        record["executor_process"] = _registry.process_payload(process)
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")


def _unfinished_record(record: dict[str, Any]) -> dict[str, Any]:
    """同じ担当が取得し直した記録は前の記録へまとめ、別担当が残した未完了の記録の連鎖を保つ。"""
    previous = record.get("previous")
    if isinstance(previous, dict) and (
        record.get("session_id") is None or record.get("session_id") == previous.get("session_id")
    ):
        retained = dict(_unfinished_record(previous))
        for key in ("manifest", "prepared_worktree"):
            if key in record:
                retained[key] = record[key]
        return retained
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


def coordinator_session(repo_id: str) -> str | None:
    """未完了の統括会話だけを返し、旧リポジトリ担当を区別する。"""
    record = _read(_path(repo_id))
    if record is not None and record.get("mode") == "coordinator" and record.get("completed") is not True:
        value = record.get("session_id")
        return value if isinstance(value, str) else None
    return None


def resume_repositories(session_id: str, repositories: tuple[str, ...]) -> set[str]:
    """指定した統括会話に属する登録済みの未完了集合を返す。"""
    result = {repo for repo in repositories if coordinator_session(repo) == session_id}
    if not result:
        raise ValueError(f"登録集合の統括会話として再開できないsession: {session_id}")
    return result


def update_prepared(ownership: Ownership, worktree: pathlib.Path, manifest: pathlib.Path) -> None:
    """準備済み実体と配送原本を、取得済みの所有tokenへ結び付ける。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたため準備結果を保存しなかった")
        record.update(prepared_worktree=str(worktree), manifest=str(manifest))
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")


def unfinished_repository_ids(repositories: tuple[str, ...]) -> set[str]:
    """登録集合内で未完了のall-repos担当がある対象だけを返す。"""
    unfinished: set[str] = set()
    for repo_id in repositories:
        record = _read(_path(_remote.normalize_remote_url(repo_id)))
        if record is not None and record.get("mode") in {"all-repos", "coordinator"} and record.get("completed") is not True:
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


def save_candidates(ownership: Ownership, candidates: list[dict[str, Any]]) -> pathlib.Path:
    """凍結した候補を上書きせず保存し、担当の所有記録へ寿命を結び付ける。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたため候補を保存しなかった")
        directory = ownership.path.parent / "inputs"
        directory.mkdir(exist_ok=True)
        path = directory / f"{ownership.path.stem}.{ownership.token}.{uuid.uuid4()}.candidates.json"
        atomic_write(path, json.dumps(candidates, ensure_ascii=False) + "\n")
        record.setdefault("candidate_files", []).append(str(path))
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")
        return path


def _candidate_files(record: dict[str, Any]) -> list[pathlib.Path]:
    result = [pathlib.Path(value) for value in record.get("candidate_files", [])]
    previous = record.get("previous")
    if isinstance(previous, dict):
        result.extend(_candidate_files(previous))
    return result


def release(ownership: Ownership, *, completed: bool = True) -> None:
    """同じtokenだけを解放し、未完了なら成果と再開sessionを保持する。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたため解放しなかった")
        record.update(completed=completed, released=True, held=False)
        finished_inputs = _candidate_files(record) if completed else []
        if not completed and record.get("session_id") is None and isinstance(record.get("previous"), dict):
            record["session_id"] = record["previous"].get("session_id")
            record["mode"] = record["previous"].get("mode", record["mode"])
        if completed:
            record.pop("previous", None)
            record.pop("candidate_files", None)
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")
        for path in finished_inputs:
            # 同じoriginの生成物だけを回収し、所有記録に異常なパスがあっても範囲を広げない。
            if (
                path.parent == ownership.path.parent / "inputs"
                and path.name.startswith(ownership.path.stem + ".")
                and path.name.endswith(".candidates.json")
            ):
                path.unlink(missing_ok=True)


def hold(ownership: Ownership, *, writers: list[str] | None = None) -> None:
    """停止を確認できない担当の所有を保持し、生存照会を担当sessionへ切り替える。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _read(ownership.path)
        if record is None or record["token"] != ownership.token:
            raise ValueError("処理所有者が変更されたため保持しなかった")
        record.update(held=True, completed=False, released=False)
        if writers is not None:
            record["writer_sessions"] = writers
        atomic_write(ownership.path, json.dumps(record, ensure_ascii=False) + "\n")
