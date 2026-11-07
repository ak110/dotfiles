"""run-skillの同一実行再開に必要な入力・会話・時間予算を保持する。"""

from __future__ import annotations

import contextlib
import pathlib
import re
import threading
import time
from collections.abc import Callable, Generator
from typing import Annotated, Literal

import pydantic

from agent_toolkit._common import claude_connection, reclaim_records, session_registry, state_paths
from agent_toolkit._common.atomic_file import atomic_write

_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"


class StartupInput(pydantic.BaseModel):
    """SessionStartへ配送する元の実行入力。再開でもこの値を使う。"""

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
    """公開resumeの識別子と保存された同一実行の入力を検証する。"""
    record = RunRecord.model_validate_json(_path(run_id).read_text(encoding="utf-8"))
    if record.input.run_id != run_id or not record.work_dir.is_absolute() or not record.log_path.is_absolute():
        raise ValueError("保存された実行識別子または絶対パスが不正")
    if record.log_path.parent != state_paths.state_dir() / "run-skill" or record.log_path.suffix != ".log":
        raise ValueError("保存されたログのパスがrun-skillの領域外")
    return record


def save(record: RunRecord) -> None:
    """再開先へ渡す状態を原子的に更新する。"""
    path = _path(record.input.run_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, record.model_dump_json() + "\n", fsync=True)


def mark_completed(run_id: str) -> None:
    """batchの範囲確定後に、その実行の再開記録も完了へ移す。"""
    # 導入前のbatchは再開記録を持たず、既存manifestだけで確定する。
    if re.fullmatch(_RUN_ID_PATTERN, run_id) is not None and _path(run_id).is_file():
        record = load(run_id)
        RecordWriter(record).finish(record.remaining, completed=True)


def sweep_records(*, cutoff: float) -> set[pathlib.Path]:
    """期限を過ぎた完了記録を回収し、未完了が参照するログを返す。"""
    protected: set[pathlib.Path] = set()
    for path in (state_paths.state_dir() / "run-skill/runs").glob("*.json"):
        record = load(path.stem)
        if record.status != "completed":
            protected.add(record.log_path)
        elif path.stat().st_mtime < cutoff:
            path.unlink()
    return protected


class RecordWriter:
    """出力readerと起動側の更新を直列化し、旧CLIの遅い出力で新しい状態を上書きしない。"""

    def __init__(self, record: RunRecord) -> None:
        self.record = record
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def running(
        self, remaining: float, connection: claude_connection.Connection | None, *, completed: Callable[[], bool]
    ) -> Generator[None]:
        """起動から終了処理までの残時間を記録し、中断時も元の実行入力を保持する。"""
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
        """実際に起動したCLIのPID世代を公開する。"""
        with self._lock:
            self.record = self.record.model_copy(update={"executor": executor})
            save(self.record)

    def set_session_id(self, identifier: str, executor: session_registry.ProcessIdentity | None) -> None:
        """同じ実行を所有するreaderが観測した会話IDだけを保存する。"""
        with self._lock:
            if self.record.executor != executor or self.record.status != "running":
                return
            self.record = self.record.model_copy(update={"session_id": identifier})
            save(self.record)

    def finish(self, remaining: float, *, completed: bool) -> None:
        """CLIの生存を確かめ、終了状態と残時間を保存する。"""
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
