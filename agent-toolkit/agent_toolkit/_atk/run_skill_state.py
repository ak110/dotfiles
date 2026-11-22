from __future__ import annotations

import contextlib
import json
import pathlib
import re
import threading
import time
from collections.abc import Callable, Generator
from typing import Annotated, Literal

import pydantic

from agent_toolkit._atk import outcome
from agent_toolkit._common import claude_connection, private_directory, reclaim_records, session_registry, state_paths
from agent_toolkit._common.atomic_file import atomic_write

_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"


class StartupInput(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)

    source: Literal["run-skill"] = "run-skill"
    run_id: Annotated[str, pydantic.Field(pattern=_RUN_ID_PATTERN)]
    skill: str
    arguments: str | None
    mode: str | None
    free_text: str | None
    accepted_scopes: list[str]
    target_repo: str | None
    batch_manifest: str | None


class RunRecord(pydantic.BaseModel):
    """認証値を含めず、元の公開入力と実行条件を同じrun IDへ保存する。"""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    input: StartupInput
    work_dir: pathlib.Path
    log_path: pathlib.Path
    engine: Literal["claude", "codex", "agy"]
    model: str
    effort: str
    session_id: str | None
    connection: claude_connection.Connection | None
    remaining: Annotated[float, pydantic.Field(ge=0)]
    deadline: float | None = None
    executor: session_registry.ProcessIdentity | None = None
    status: Literal["running", "interrupted", "completed"] = "interrupted"


def _path(run_id: str) -> pathlib.Path:
    if re.fullmatch(_RUN_ID_PATTERN, run_id) is None:
        raise ValueError("実行識別子はrun-skillが出力した32文字の小文字16進数で指定する")
    return state_paths.state_dir() / "run-skill" / "runs" / f"{run_id}.json"


def load(run_id: str) -> RunRecord:
    text = _path(run_id).read_text(encoding="utf-8")
    payload = json.loads(text)
    connection = payload.get("connection") if isinstance(payload, dict) else None
    if isinstance(connection, dict) and "isolation_env_file" in connection:
        legacy = connection.pop("isolation_env_file")
        connection.setdefault("isolation_env_files", [] if legacy is None else [legacy])
        text = json.dumps(payload)
    record = RunRecord.model_validate_json(text)
    if record.input.run_id != run_id or not record.work_dir.is_absolute() or not record.log_path.is_absolute():
        raise ValueError("保存された実行識別子または絶対パスが不正")
    if record.log_path.parent != state_paths.state_dir() / "run-skill" or record.log_path.suffix != ".log":
        raise ValueError("保存されたログのパスがrun-skillの領域外")
    return record


def save(record: RunRecord) -> None:
    path = _path(record.input.run_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=private_directory.MODE)
    atomic_write(path, record.model_dump_json() + "\n", fsync=True)


def mark_completed(run_id: str) -> None:

    if re.fullmatch(_RUN_ID_PATTERN, run_id) is not None and _path(run_id).is_file():
        record = load(run_id)
        RecordWriter(record).finish(record.remaining, completed=True)


def sweep_records(*, cutoff: float) -> set[pathlib.Path]:
    """期限を過ぎた完了記録を回収し、未完了が参照するログを返す。

    読めない記録は残して診断し、参照先を特定できないためrun-skillの全ログを保護する。
    回収の失敗で新しい実行の起動を止めないためである。
    """
    protected: set[pathlib.Path] = set()
    unreadable: list[str] = []
    for path in (state_paths.state_dir() / "run-skill/runs").glob("*.json"):
        try:
            record = load(path.stem)
        except (OSError, ValueError) as error:
            unreadable.append(f"{path}（{' '.join(str(error).split())}）")
            continue
        if record.status != "completed":
            protected.add(record.log_path)
        elif path.stat().st_mtime < cutoff:
            path.unlink()
    if unreadable:
        protected.update((state_paths.state_dir() / "run-skill").glob("*.log"))
        outcome.report_warning(
            f"読めないrun-skillの実行記録があるため、ログの期限回収を見送った: {', '.join(unreadable)}",
            next_action="記録の内容を確認し、再開しない記録なら削除する",
        )
    return protected


class RecordWriter:
    def __init__(self, record: RunRecord) -> None:
        self.record = record
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def running(
        self, remaining: float, connection: claude_connection.Connection | None, *, completed: Callable[[], bool]
    ) -> Generator[None]:
        with self._lock:
            self.record = self.record.model_copy(
                update={
                    "remaining": remaining,
                    "deadline": time.time() + remaining,
                    "connection": connection,
                    "status": "running",
                }
            )
            save(self.record)
        started_at = time.monotonic()
        try:
            yield
        except BaseException:
            self.finish(max(0.0, remaining - (time.monotonic() - started_at)), completed=False)
            raise
        else:
            self.finish(max(0.0, remaining - (time.monotonic() - started_at)), completed=completed())

    def attach(self, executor: session_registry.ProcessIdentity | None) -> None:
        with self._lock:
            self.record = self.record.model_copy(update={"executor": executor})
            save(self.record)

    def set_session_id(self, identifier: str, executor: session_registry.ProcessIdentity | None) -> None:
        with self._lock:
            if self.record.executor != executor or self.record.status != "running":
                return
            self.record = self.record.model_copy(update={"session_id": identifier})
            save(self.record)

    def finish(self, remaining: float, *, completed: bool) -> None:
        with self._lock:
            executor = self.record.executor
            if executor is not None and (executor.exists() or reclaim_records.pending(executor.pid, executor.create_time)):
                # 終了処理が失敗して旧CLIか回収待ちの子孫が残る場合は、次のresumeにもPID世代と元deadlineを渡す。
                # resumeは記録したPID世代から回収待ちを引き当てて回収を再試行する。
                self.record = self.record.model_copy(update={"remaining": remaining, "status": "running"})
                save(self.record)
                return
            self.record = self.record.model_copy(
                update={
                    "remaining": remaining,
                    "deadline": None,
                    "executor": None,
                    "status": "completed" if completed else "interrupted",
                }
            )
            save(self.record)
