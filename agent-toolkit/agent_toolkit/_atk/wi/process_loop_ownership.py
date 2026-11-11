"""統括の処理所有をorigin単位で保持し、未完了成果を再開へ渡す。"""

import dataclasses
import enum
import hashlib
import json
import os
import pathlib
import uuid
from collections.abc import Iterable
from typing import Any

import filelock
import psutil

from agent_toolkit._atk.wi import snapshot as _wi_snapshot
from agent_toolkit._atk.wi.entries import EntryRecord
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import process_identity
from agent_toolkit._common import process_tree as _process_tree
from agent_toolkit._common import session_launchers as _launchers
from agent_toolkit._common import session_registry as _registry
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import remote as _remote


class OwnershipStage(enum.Enum):
    """永続所有の段階。プロセスとsessionの生存は別に照会する。"""

    OWNED = (False, False, False)
    HELD = (False, False, True)
    RELEASED_INCOMPLETE = (False, True, False)
    RELEASED_COMPLETED = (True, True, False)


@dataclasses.dataclass
class _Record:
    stage: OwnershipStage
    data: dict[str, Any]

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> _Record:
        data = dict(payload)
        if "completed" not in data or "released" not in data:
            raise ValueError("処理所有記録の段階が欠落している")
        flags = tuple(data.pop(key, False) for key in ("completed", "released", "held"))
        if any(not isinstance(value, bool) for value in flags):
            raise ValueError("処理所有記録の段階にはboolを指定する")
        try:
            stage = OwnershipStage(flags)
        except ValueError as error:
            raise ValueError("処理所有記録の段階の組合せが不正") from error
        if isinstance(data.get("previous"), dict):
            cls.parse(data["previous"])
        return cls(stage, data)

    def payload(self) -> dict[str, Any]:
        return dict(self.data, **dict(zip(("completed", "released", "held"), self.stage.value, strict=True)))

    def release(self, *, completed: bool) -> None:
        self.stage = OwnershipStage.RELEASED_COMPLETED if completed else OwnershipStage.RELEASED_INCOMPLETE


def _save(path: pathlib.Path, record: _Record) -> None:
    atomic_write(path, json.dumps(record.payload(), ensure_ascii=False) + "\n")


def _owned_record(ownership: Ownership) -> _Record:
    record = _read(ownership.path)
    if record is None or record.data["token"] != ownership.token:
        raise ValueError("処理所有者が変更されたため更新しなかった")
    if record.stage not in {OwnershipStage.OWNED, OwnershipStage.HELD}:
        raise ValueError("処理所有は解放済みのため更新しなかった")
    return record


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


def _read(path: pathlib.Path) -> _Record | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("token"), str):
        raise ValueError(f"処理所有記録を解釈できない: {path}")
    residual = record.get(_RESIDUAL_KEY, [])
    if not isinstance(residual, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("pid"), int) for entry in residual
    ):
        # PIDを欠く項目もあり得るため、PIDに頼らない確認手段（起動コマンドか対象リポジトリのパスによる対象限定）も示す。
        raise ValueError(
            f"処理所有記録の`{_RESIDUAL_KEY}`が不正なため、残存子孫の生存を判定できない: {path}。"
            "PIDがある項目は、そのPIDのプロセスが無いか、項目の`command`と異なる起動コマンドの別プロセスであることを確かめる。"
            "PIDが無い項目は、項目の`command`（無ければ記録の`repo_id`の"
            "リポジトリのパス）を含むプロセスをプロセス一覧（POSIXは`ps -eo pid,lstart,args`、"
            "Windowsは`Get-CimInstance Win32_Process | Select-Object ProcessId,CreationDate,CommandLine`）で探す。"
            f"該当するプロセスが残っていないか、残りを終了させたことを確かめてから、記録から`{_RESIDUAL_KEY}`を削除する"
        )
    return _Record.parse(record)


def _live(record: _Record) -> bool:
    if record.stage in {OwnershipStage.RELEASED_COMPLETED, OwnershipStage.RELEASED_INCOMPLETE}:
        return False
    if _executor_live(record.data) or _residual_processes(record.data):
        return True
    pid, created = record.data.get("pid"), record.data.get("create_time")
    if not isinstance(pid, int) or not isinstance(created, (int, float)):
        raise ValueError("処理所有記録のプロセス識別が不正")
    if record.stage is OwnershipStage.OWNED and _registry.ProcessIdentity(pid, created, record.data.get("start_id")).exists():
        return True
    sessions = {
        value for value in [record.data.get("session_id"), *record.data.get("writer_sessions", [])] if isinstance(value, str)
    }
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
    return _registry.ProcessIdentity(executor["pid"], executor["create_time"], executor.get("start_id")).exists()


_RESIDUAL_KEY = "residual_processes"
"""統括CLIの停止で回収できなかった子孫の識別（PID・起動時刻・起動コマンド）を保存する所有記録の項目。

process-loopは巡回ごとに自己再起動するため、プロセス内の変数では保持できない。記録に残し、
消滅を確認するまで所有を保持して同じ対象の次の統括を起動しない。
"""


def _residual_processes(record: dict[str, Any], *, conservative: bool = True) -> list[psutil.Process]:
    """保存した残存子孫のうち、同じPIDと起動時刻のまま生存しているものを返す。項目の形式は`_read`が検査済みである。"""
    alive: list[psutil.Process] = []
    for entry in record.get(_RESIDUAL_KEY, []):
        try:
            process = psutil.Process(entry["pid"])
            if (
                process_identity.matches(process, entry["create_time"], entry.get("start_id"), conservative=conservative)
                and process.status() != psutil.STATUS_ZOMBIE
            ):
                alive.append(process)
        except psutil.Error:
            continue
    return alive


def record_residual(ownership: Ownership, processes: list[psutil.Process]) -> None:
    """統括CLIの停止で回収できなかった子孫を、自己再起動をまたいで保持するため所有記録へ加える。"""
    entries = []
    for process in processes:
        try:
            entries.append(
                {**process_identity.ProcessIdentity.capture(process).payload(), "command": _process_tree.describe(process)}
            )
        except psutil.Error:
            # 記録の前に消滅した子孫は保持の対象にしない。
            continue
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        saved = record.data.setdefault(_RESIDUAL_KEY, [])
        saved.extend(entry for entry in entries if entry not in saved)
        _save(ownership.path, record)


def residual_running(ownership: Ownership) -> bool:
    """保存した残存子孫が生存しているかを返す。"""
    return bool(_residual_processes(_owned_record(ownership).data))


def _reclaim_residual(path: pathlib.Path, record: _Record) -> None:
    """保存した残存子孫を同じ識別で回収し直し、消滅を確認した子孫を記録から外す。残る間は対象と次の操作を示す。"""
    if not record.data.get(_RESIDUAL_KEY):
        return
    _process_tree.reclaim_processes(_residual_processes(record.data, conservative=False))
    remaining = [entry for entry in record.data[_RESIDUAL_KEY] if _residual_processes({_RESIDUAL_KEY: [entry]})]
    record.data[_RESIDUAL_KEY] = remaining
    _save(path, record)
    if remaining:
        _next_action.report(
            f"{record.data.get('repo_id')}: 前の統括の停止で回収できなかった子孫が残るため、"
            f"所有を保持して次の統括を起動しない: {[entry.get('command') for entry in remaining]}",
            next_action=(
                "一覧のPIDと起動コマンドで対象を確かめ、不要なら終了させる"
                "（POSIXは`kill -KILL <PID>`、Windowsは`taskkill /PID <PID> /T /F`）。"
                "消滅後の巡回でprocess-loopが所有を解放して次の統括を起動する"
            ),
        )


def executor_running(ownership: Ownership) -> bool:
    """会話IDの検出前でも、保存済みの統括CLIの生存を確認する。"""
    return _executor_live(_owned_record(ownership).data)


def update_process(ownership: Ownership, process: _registry.ProcessIdentity) -> None:
    """Popenが開始した実行主体の寿命を取得済み所有へ結び付ける。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        record.data["executor_process"] = _registry.process_payload(process)
        _save(ownership.path, record)


def _unfinished_record(record: dict[str, Any]) -> dict[str, Any]:
    """同じ担当が取得し直した記録は前の記録へまとめ、別担当が残した未完了の記録の連鎖を保つ。"""
    previous = record.get("previous")
    if isinstance(previous, dict) and (
        record.get("session_id") is None or record.get("session_id") == previous.get("session_id")
    ):
        retained = dict(_unfinished_record(previous))
        for key in ("manifest", "prepared_worktree", "snapshot_file"):
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
        raise _next_action.ActionableError(
            f"処理所有のGit共通ディレクトリを取得できない: {worktree}: {common.stderr.strip()}",
            next_action="対象がGit作業ツリーかとGitの状態を確かめ、同じコマンドを再実行する",
        )
    path = _path(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(str(path.with_suffix(".lock")), timeout=10):
        prior = _read(path)
        if prior is not None and prior.stage in {OwnershipStage.OWNED, OwnershipStage.HELD}:
            _reclaim_residual(path, prior)
        if prior is not None and _live(prior):
            return None
        if prior is not None and prior.stage is OwnershipStage.HELD:
            prior.release(completed=False)
            _save(path, prior)
        identity = _registry.ProcessIdentity.of(os.getpid())
        if identity is None:
            raise _next_action.ActionableError(
                "処理所有者のPIDと起動時刻を取得できない",
                next_action="同じコマンドを再実行し、続く場合はこの診断を添えて不具合として報告する",
            )
        token = str(uuid.uuid4())
        previous = (
            _unfinished_record(prior.payload())
            if prior is not None and prior.stage is not OwnershipStage.RELEASED_COMPLETED
            else None
        )
        record = _Record(
            OwnershipStage.OWNED,
            {
                "repo_id": repo_id,
                "token": token,
                **identity.payload(),
                "worktree": str(worktree.resolve()),
                "session_id": session_id,
                "mode": mode,
                "common_dir": str(pathlib.Path(common.stdout.strip()).resolve()),
            },
        )
        if previous is not None:
            record.data["previous"] = previous
        record.data["lane_session_id"] = previous.get("lane_session_id", token) if previous is not None else token
        _save(path, record)
        return Ownership(repo_id, token, path, session_id, previous)


def coordinator_session(repo_id: str) -> str | None:
    """未完了の統括会話だけを返し、旧リポジトリ担当を区別する。"""
    record = _read(_path(repo_id))
    if (
        record is not None
        and record.data.get("mode") == "coordinator"
        and record.stage is not OwnershipStage.RELEASED_COMPLETED
    ):
        value = record.data.get("session_id")
        return value if isinstance(value, str) else None
    return None


def resume_repositories(session_id: str, repositories: tuple[str, ...]) -> set[str]:
    """台帳に元集合があれば再利用し、無ければ現在の登録集合で明示resumeする。"""
    return {repo for repo in repositories if coordinator_session(repo) == session_id} or set(repositories)


def update_prepared(ownership: Ownership, worktree: pathlib.Path, manifest: pathlib.Path) -> None:
    """準備済み実体と配送原本を、取得済みの所有tokenへ結び付ける。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        record.data.update(prepared_worktree=str(worktree), manifest=str(manifest))
        _save(ownership.path, record)


def unfinished_repository_ids(repositories: tuple[str, ...]) -> set[str]:
    """登録集合内で未完了のall-repos担当がある対象だけを返す。"""
    unfinished: set[str] = set()
    for repo_id in repositories:
        record = _read(_path(_remote.normalize_remote_url(repo_id)))
        if (
            record is not None
            and record.data.get("mode") in {"all-repos", "coordinator"}
            and record.stage is not OwnershipStage.RELEASED_COMPLETED
        ):
            unfinished.add(repo_id)
    return unfinished


def update_session(ownership: Ownership, session_id: str, *, lane_session_id: str | None = None) -> Ownership:
    """同じ所有tokenに担当sessionを結び、再開先を保存する。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        record.data["session_id"] = session_id
        if lane_session_id is not None:
            record.data["lane_session_id"] = lane_session_id
        _save(ownership.path, record)
    return dataclasses.replace(ownership, session_id=session_id)


def save_snapshot(ownership: Ownership, entries: Iterable[EntryRecord]) -> pathlib.Path:
    """初回取得の集合を保存し、未完了の同じ実行では元の基準を再利用する。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        prior = record.data
        while True:
            if "snapshot_file" in prior:
                path = pathlib.Path(prior["snapshot_file"])
                _wi_snapshot.read(path)
                return path
            if not isinstance(prior.get("previous"), dict):
                break
            prior = prior["previous"]
        directory = ownership.path.parent / "inputs"
        directory.mkdir(exist_ok=True)
        path = directory / f"{ownership.path.stem}.{ownership.token}.{uuid.uuid4()}.snapshot.json"
        _wi_snapshot.save(path, entries)
        record.data["snapshot_file"] = str(path)
        _save(ownership.path, record)
        return path


def save_candidates(ownership: Ownership, candidates: list[dict[str, Any]]) -> pathlib.Path:
    """凍結した候補を上書きせず保存し、担当の所有記録へ寿命を結び付ける。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        directory = ownership.path.parent / "inputs"
        directory.mkdir(exist_ok=True)
        path = directory / f"{ownership.path.stem}.{ownership.token}.{uuid.uuid4()}.candidates.json"
        atomic_write(path, json.dumps(candidates, ensure_ascii=False) + "\n")
        record.data.setdefault("candidate_files", []).append(str(path))
        _save(ownership.path, record)
        return path


def _candidate_files(record: dict[str, Any]) -> list[pathlib.Path]:
    result = [pathlib.Path(value) for value in record.get("candidate_files", [])]
    if "snapshot_file" in record:
        result.append(pathlib.Path(record["snapshot_file"]))
    previous = record.get("previous")
    if isinstance(previous, dict):
        result.extend(_candidate_files(previous))
    return result


def release(ownership: Ownership, *, completed: bool = True) -> None:
    """同じtokenだけを解放し、未完了なら成果と再開sessionを保持する。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        record.release(completed=completed)
        finished_inputs = _candidate_files(record.data) if completed else []
        if not completed and record.data.get("session_id") is None and isinstance(record.data.get("previous"), dict):
            record.data["session_id"] = record.data["previous"].get("session_id")
            record.data["mode"] = record.data["previous"].get("mode", record.data["mode"])
        if completed:
            record.data.pop("previous", None)
            record.data.pop("candidate_files", None)
            record.data.pop("snapshot_file", None)
        _save(ownership.path, record)
        for path in finished_inputs:
            # 同じoriginの生成物だけを回収し、所有記録に異常なパスがあっても範囲を広げない。
            if (
                path.parent == ownership.path.parent / "inputs"
                and path.name.startswith(ownership.path.stem + ".")
                and path.name.endswith((".candidates.json", ".snapshot.json"))
            ):
                path.unlink(missing_ok=True)


def hold(ownership: Ownership, *, writers: list[str] | None = None) -> None:
    """停止を確認できない担当の所有を保持し、生存照会を担当sessionへ切り替える。"""
    with filelock.FileLock(str(ownership.path.with_suffix(".lock")), timeout=10):
        record = _owned_record(ownership)
        record.stage = OwnershipStage.HELD
        if writers is not None:
            record.data["writer_sessions"] = writers
        _save(ownership.path, record)
