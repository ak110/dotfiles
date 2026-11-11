"""Claudeの初期化診断とCLIプロセスの観測。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import asyncio
import contextlib
import datetime
import os
import pathlib
from typing import Any

lazy import psutil

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import LaunchKind, append_bounded
from agent_toolkit._common import process_tree
from agent_toolkit._common import state_paths as _state_paths

_INITIALIZATION_STDERR_LIMIT_CHARS = 4000


# 初期化の停止位置を特定するため、受信したメッセージを識別できる要約を件数と長さの上限内で保持する。
_INITIALIZATION_MESSAGE_LIMIT = 20


_INITIALIZATION_MESSAGE_SUMMARY_LIMIT_CHARS = 200


# 委譲先CLIの診断ログを置く場所と、自動削除の対象を決める保持世代数。
_DEBUG_LOG_DIR_NAME = "delegate-debug"


_DEBUG_LOG_RETENTION = 20


class _InitializationDiagnostic:
    """Claude SDK初期化の到達段階と、件数と長さに上限を設けた観測値を保持する。"""

    def __init__(self) -> None:
        self.stage = "created"
        self.received_message_types: dict[str, int] = {}
        self.received_messages: list[str] = []
        self.child_pid: int | None = None
        self.child_process: psutil.Process | None = None
        self.debug_file: str | None = None
        self.stderr = ""
        self.exception_type: str | None = None
        self.exception_body: str | None = None
        self.mcp_recovery_task: asyncio.Task[None] | None = None

    def capture_stderr(self, text: str) -> None:
        self.stderr = append_bounded(self.stderr, text, _INITIALIZATION_STDERR_LIMIT_CHARS)

    def capture_client(self, client: Any) -> None:
        transport = getattr(client, "_transport", None)
        process = getattr(transport, "_process", None)
        pid = getattr(process, "pid", None)
        self.child_pid = pid if isinstance(pid, int) else None
        if self.child_pid is not None:
            try:
                self.child_process = psutil.Process(self.child_pid)
            except psutil.NoSuchProcess:
                self.child_process = None

    def record_message(self, message: Any) -> None:
        name = _message_name(message)
        self.received_message_types[name] = self.received_message_types.get(name, 0) + 1
        if len(self.received_messages) < _INITIALIZATION_MESSAGE_LIMIT:
            self.received_messages.append(f"{name}: {_message_summary(message)}")

    def record_exception(self, error: BaseException) -> None:
        self.exception_type = type(error).__name__
        self.exception_body = str(error) or self.exception_type

    async def public_async(self) -> dict[str, Any]:
        """子孫の照会を待ち、診断のsnapshotをloopで組み立てる。"""
        children = await run_file_operation(_describe_child_processes, self.child_pid)
        return self.public(child_processes=children)

    def public(self, *, child_processes: list[str] | None = None) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "received_message_types": dict(sorted(self.received_message_types.items())),
            "received_messages": list(self.received_messages),
            "child_pid": self.child_pid,
            "child_processes": _describe_child_processes(self.child_pid) if child_processes is None else child_processes,
            "debug_file": self.debug_file,
            "stderr": self.stderr.strip(),
            "exception_type": self.exception_type,
            "exception_body": self.exception_body,
        }


def _message_summary(message: Any) -> str:
    """受信メッセージを識別できる要約を、長さの上限内で返す。"""
    for attribute in ("subtype", "hook_event_name", "event", "name"):
        value = getattr(message, attribute, None)
        if isinstance(value, str) and value:
            return value[:_INITIALIZATION_MESSAGE_SUMMARY_LIMIT_CHARS]
    return repr(message)[:_INITIALIZATION_MESSAGE_SUMMARY_LIMIT_CHARS]


def _describe_child_processes(pid: int | None) -> list[str]:
    """委譲先プロセスが起動した子プロセスをPIDと起動コマンドで列挙する。

    `/proc`を持たない実行環境や、列挙の途中で終了したプロセスでは、その分を空として扱う。
    診断の付随情報であり、取得できないことを初期化の失敗として扱わない。
    """
    if pid is None:
        return []
    children: list[str] = []
    try:
        task_dir = pathlib.Path(f"/proc/{pid}/task")
        child_pids = {
            int(child) for task in task_dir.iterdir() for child in (task / "children").read_text(encoding="utf-8").split()
        }
    except OSError, ValueError:
        return []
    for child_pid in sorted(child_pids):
        try:
            cmdline = pathlib.Path(f"/proc/{child_pid}/cmdline").read_bytes()
        except OSError:
            continue
        command = " ".join(os.fsdecode(part) for part in cmdline.split(b"\0") if part)
        children.append(f"{child_pid}: {command[:_INITIALIZATION_MESSAGE_SUMMARY_LIMIT_CHARS]}")
    return children


def _owned_runtime_processes(
    root: psutil.Process | None,
    known: tuple[psutil.Process, ...] = (),
    *,
    pins: process_tree.ProcessIdentityPins | None = None,
) -> list[psutil.Process]:
    """親終了後も観測済みhandleを保ち、それぞれの子孫を所有へ加える。"""
    owned = dict.fromkeys((*known, *((root,) if root is not None else ())))
    for parent in tuple(owned):
        try:
            if pins is not None and not pins.retain(parent):
                continue
            if parent.is_running():
                children = parent.children(recursive=True)
                owned.update(dict.fromkeys(children if pins is None else [child for child in children if pins.retain(child)]))
        except psutil.NoSuchProcess:
            continue
    return list(owned)


def _prepare_debug_file(launch_kind: LaunchKind) -> pathlib.Path:
    """委譲先CLIの診断ログの保存先を用意し、保持世代を超えた記録を削除する。

    session識別子は初期化の完了まで確定しないため、開始時点では時刻を名前に使う。
    確定後の改名は`rename_debug_file_for_session`が行う。
    """
    directory = _state_paths.state_dir() / _DEBUG_LOG_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(directory.glob("*.log"), key=lambda path: path.stat().st_mtime)
    for stale in existing[: max(0, len(existing) - _DEBUG_LOG_RETENTION + 1)]:
        with contextlib.suppress(OSError):
            stale.unlink()
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%f")
    return directory / f"{stamp}-{launch_kind}.log"


def rename_debug_file_for_session(debug_file: pathlib.Path, session_id: str, launch_kind: LaunchKind) -> pathlib.Path:
    """診断ログの名前がsession識別子を持つ形へ改名し、確定後の絶対パスを返す。

    委譲先CLIが対象ファイルを開いたまま改名する。改名できない実行環境では元の名前を保ち、
    session識別子との対応は終了時の結果の`debugFile`から解決する。
    """
    stamp = debug_file.name.split("-", 1)[0]
    renamed = debug_file.with_name(f"{stamp}-{session_id}-{launch_kind}.log")
    try:
        debug_file.rename(renamed)
    except OSError:
        return debug_file
    return renamed


def _message_name(message: Any) -> str:
    return message.__class__.__name__
