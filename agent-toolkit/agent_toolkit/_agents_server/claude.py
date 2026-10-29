"""Claude Agent SDKを1セッション1長命タスクで所有するバックエンド。

`ClaudeSDKClient`の接続、メッセージ消費、継続入力、切断は同じセッションタスクが担当する。
MCP層はキューへ入力を渡し、クライアントへ直接アクセスしない。
"""

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import os
import pathlib
import re
import shutil
import time
import typing
from collections.abc import Callable
from typing import Any, Literal, cast

lazy import psutil

from agent_toolkit._agents_server import (
    api_credits,
    child_environment,
    claude_mcp,
    claude_retry_record,
    claude_settings,
    context_usage,
    engine_availability,
    lifetime,
    result_contract,
    result_projection,
    resume_waits,
    session_errors,
    session_identity,
    wait_output_tracking,
)
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.launch_prompts import (
    AUTO_RESUME_NOTICE,
    CLAUDE_DELEGATE_SYSTEM_PROMPT,
    CLAUDE_EXPLORE_SYSTEM_PROMPT,
    LAUNCH_SYSTEM_PROMPTS,
    LIGHTWEIGHT_LAUNCH_KINDS,
    SUBAGENT_BASE_INSTRUCTIONS,
    cli_runtime_instructions,
    python_runtime_instructions,
)
from agent_toolkit._agents_server.session_errors import SessionInitializationTimeoutError, SessionOwnerGoneError
from agent_toolkit._agents_server.state import (
    LaunchKind,
    ModelCandidate,
    ResumePrompt,
    SessionState,
    append_bounded,
    begin_reply,
)
from agent_toolkit._atk import claude_connection
from agent_toolkit._common import claude_connection as connection_settings
from agent_toolkit._common import claude_usage_limit, plugin_environment, process_tree
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")
_ENV_EMIT_SESSION_STATE_EVENTS = "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"
_DISCONNECT_TIMEOUT = 5.0
_EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]
# 軽量起動の組込ツール。探索の読み取り専用性はプロンプトで指示する。
_LAUNCH_TOOLS: dict[str, list[str]] = {
    "explore": ["Read", "Glob", "Grep", "Bash"],
    "shell": ["Bash", "Read"],
    "write": ["Read", "Write", "Edit", "Glob", "Grep"],
}
# 無確認で使えるMCPは組込ツールの利用可能性と分ける。
_LAUNCH_ALLOWED_TOOLS = {kind: list(tools) for kind, tools in _LAUNCH_TOOLS.items()}
_LAUNCH_ALLOWED_TOOLS["explore"].append("mcp__pyfltr__*")
_DeliveryResult = tuple[str, dict[str, Any] | None]
_Command = tuple[Literal["prompt", "interrupt"], str, asyncio.Future[_DeliveryResult]]
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


def _owned_runtime_processes(root: psutil.Process | None) -> list[psutil.Process]:
    """SDKが起動したCLIと子孫を、停止前の同一性を持つhandleとして確定する。"""
    if root is None:
        return []
    try:
        if not root.is_running():
            return []
        return [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


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


def _settings_from_cmdline(cmdline: bytes) -> str | None:
    """NUL区切りの起動引数から有効な`--settings`の値を返す。"""
    arguments = cmdline.split(b"\0")
    settings: str | None = None
    for index, argument in enumerate(arguments):
        if argument.startswith(b"--settings="):
            value = argument.removeprefix(b"--settings=")
            settings = os.fsdecode(value) if value else None
        elif argument == b"--settings":
            value = arguments[index + 1] if index + 1 < len(arguments) else b""
            settings = os.fsdecode(value) if value else None
    return settings


def _parent_settings() -> str | None:
    """親Claude Codeの起動引数からsettingsファイルを返す。"""
    try:
        cmdline = pathlib.Path(f"/proc/{os.getppid()}/cmdline").read_bytes()
    except OSError:
        return None
    return _settings_from_cmdline(cmdline)


class _CommandChannel:
    """1つのClaude sessionへの継続要求と中断要求を所有タスクへ渡す。

    受理した要求の応答futureは、所有タスクによる処理か`close`のいずれかで必ず解決する。
    所有タスクを終了させる処理が増えても、`close`の1箇所で受理済みの要求を解決できる。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[_Command] = asyncio.Queue()
        self._closed = False

    def send(self, kind: Literal["prompt", "interrupt"], prompt: str) -> asyncio.Future[_DeliveryResult]:
        """要求を受理して応答futureを返す。閉鎖後は受理せず例外を送出する。"""
        if self._closed:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        future: asyncio.Future[_DeliveryResult] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((kind, prompt, future))
        return future

    async def get(self) -> _Command:
        """所有タスクが次の要求を取り出す。"""
        return await self._queue.get()

    def close(self) -> None:
        """以降の受理を止め、未処理の要求を所有タスクの終了として解決する。"""
        self._closed = True
        while True:
            try:
                _kind, _prompt, future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))


class _ContextUsageObserver:
    """表示用のコンテキスト使用量の照会を受信ループと別のtaskで行い、照会中に新しい要求が届いた場合は古い応答を捨てて照会し直す。"""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._revision = 0
        self._task: asyncio.Task[None] | None = None

    def request(self, session: SessionState) -> None:
        self._revision += 1
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._observe(session))

    async def _observe(self, session: SessionState) -> None:
        while True:
            revision = self._revision
            turn_seq = session.turn_seq
            try:
                usage = await self._client.get_context_usage()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # 旧CLIの未対応や照会失敗で、起動とモデル出力の処理を失敗させない。
                _LOG.debug("context_usage_unavailable session_id=%s error=%s", session.session_id, exc)
                usage = {}
            if revision != self._revision:
                continue
            if turn_seq == session.turn_seq:
                usage = usage if isinstance(usage, dict) else {}
                context_usage.record_context_usage(session, usage.get("totalTokens"), usage.get("rawMaxTokens"))
            return

    async def close(self) -> None:
        if self._task is not None:
            if not self._task.done():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


# CLIのstdoutの1行にはモデルへ渡らないtool_use_result（EditのoriginalFile等）も含まれる。
# CLIが編集できる最大1GiBのファイルと差分が収まる4GiBを指定する。事前のメモリー確保ではない。
_MAX_BUFFER_SIZE = 4 * 1024**3


def _build_options(
    cwd: str,
    model: str | None,
    effort: str | None,
    session_id: str | None = None,
    launch_kind: LaunchKind = "delegate",
    stderr: Callable[[str], None] | None = None,
    debug_file: pathlib.Path | None = None,
    root_session_id: str | None = None,
    api_env_file: str | None = None,
    connection_kind: str = "inherit",
    isolation_env_file: str | None = None,
    handoff_prompt: str = "",
) -> Any:
    """起動区分ごとの自前システム指示と委譲先の印を持つSDKオプションを組む。

    `ClaudeAgentOptions.env`は継承環境へ後から重なるため、process-loopの印を継承したまま
    委譲先の印を追加する。
    共有状態を所有するManagerから受け取ったrootを委譲先へ渡す。
    親の`--settings`層へ同じenvを合成し、ユーザー・project設定のenvより優先させる。
    親を取得・解析できない場合はenvだけのsettingsを使う。
    親の設定がファイルの場合は内容をargvへ展開せず、起動ごとに作成した所有者だけが読めるファイルを渡す。
    軽量起動はSkillを明示除外し、基準の参照にはReadを使う。

    起動区分によらず権限モードをbypass系にしない。Claude Codeのセッション間メッセージの受信方針は、
    受信側がbypass系であり送信側が権限モードを申告していない場合にそのメッセージを保留する。
    委譲元は起動直後にメッセージを送るため、bypass系で起動したセッションは保留のまま
    初期化を完了できない。この保留動作は起動区分によらず同じである。
    """
    from claude_agent_sdk import ClaudeAgentOptions

    plugin_root = plugin_roots.SERVER_PLUGIN_ROOT
    cli_env = plugin_environment.cli_environment(plugin_root, os.environ)
    env = {
        _delegated_session.DELEGATED_SESSION_ENV: "1",
        # 委譲先のhookが軽量起動を判別して、スキルの取得を求めない判定に使う。
        _delegated_session.LAUNCH_KIND_ENV: launch_kind,
        "PATH": cli_env["PATH"],
        plugin_environment.RUNTIME_DATA_ENV: cli_env[plugin_environment.RUNTIME_DATA_ENV],
    }
    env.update(_delegated_session.handoff_environment(handoff_prompt))
    cli_instructions = cli_runtime_instructions(plugin_root)
    launch_prompt = CLAUDE_EXPLORE_SYSTEM_PROMPT if launch_kind == "explore" else LAUNCH_SYSTEM_PROMPTS[launch_kind]
    # CLIのturn状態の報告（`SystemMessage`のsubtype `session_state_changed`）を受け取る。
    # Claude Agent SDKはこの変数が無い場合だけ報告を自身の判定用に要求し、呼び出し側のストリームから除く。
    # 結果の公開の判定に`idle`を使うため、全起動区分で有効にする（`_run`の`ResultMessage`の分岐を参照）。
    env[_ENV_EMIT_SESSION_STATE_EVENTS] = "1"
    if root_session_id is not None:
        env[_delegated_session.OWNER_SESSION_ENV] = root_session_id
    # 委譲先のプロンプトキャッシュ保持期間を`mode`ごとに固定する。評価順序は`_common/wait_schedule.py`のdocstringが定める。
    # 軽量起動（探索委譲とシェル実行委譲）は連続する要求の間隔が短く、5分でも失効しないため、書き込み単価の低い側を選ぶ。
    # 通常起動は配下のサブエージェントへユーザー設定ファイルの指定が届かないため、1時間を明示する。
    # 前提が成立しなくなった場合は、軽量起動で連続する要求の間隔が5分を超える事象、または通常起動の配下サブエージェントが
    # 1時間で書き込む事象を、セッション記録の`message.usage.cache_creation`から観測できる。
    lightweight = launch_kind in LIGHTWEIGHT_LAUNCH_KINDS
    if lightweight:
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = "5m"
    else:
        env["CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"] = "1h"
    options: dict[str, Any] = {
        "cwd": cwd,
        "model": model,
        "effort": cast(_EffortLevel, effort),
        "resume": session_id,
        "permission_mode": "auto",
        # 起動直後の可用性待機をAPIの応答開始（`message_start`）で打ち切るため、部分出力のイベントを受け取る。
        "include_partial_messages": True,
        "env": env,
        "plugins": [{"type": "local", "path": str(plugin_root)}],
        "max_buffer_size": _MAX_BUFFER_SIZE,
        "setting_sources": ["user"] if lightweight else ["user", "project"],
        "system_prompt": (
            f"{launch_prompt}\n{AUTO_RESUME_NOTICE}\n{python_runtime_instructions()}\n{cli_instructions}"
            if lightweight
            else f"{SUBAGENT_BASE_INSTRUCTIONS}\n{CLAUDE_DELEGATE_SYSTEM_PROMPT}\n{AUTO_RESUME_NOTICE}\n{cli_instructions}"
        ),
    }
    if stderr is not None:
        options["stderr"] = stderr
    if debug_file is not None:
        options["extra_args"] = {"debug-file": str(debug_file)}
    if (cli_path := shutil.which("claude")) is not None:
        options["cli_path"] = cli_path
    # 起動時に継承環境から除いた接続値は、選択した接続へだけ明示的に戻す。
    connection = connection_settings.Connection(connection_kind, api_env_file, isolation_env_file)
    isolate_auth = connection.kind != "inherit"
    selected_environment = connection_settings.connection_environment(child_environment.inherited(), connection)
    env.update({key: value for key, value in selected_environment.items() if key not in os.environ})
    env.update(connection_settings.connection_overrides(connection))
    if lightweight:
        options.update(
            skills=[],
            disallowed_tools=["Skill"],
            tools={"type": "preset", "preset": "claude_code"},
            allowed_tools=_LAUNCH_ALLOWED_TOOLS[launch_kind],
        )
    if launch_kind == "explore":
        # 起動定義はpluginのSSOTを使い、検索を担うpyfltrを軽量起動へ渡す。
        # pyfltrの読取用途はプロンプトが定め、個別ツールの許可リストは設けない。
        mcp_config = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        pyfltr = mcp_config["mcpServers"]["pyfltr"]
        pyfltr["args"] = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)) for arg in pyfltr.get("args", [])]
        options["mcp_servers"] = {"pyfltr": pyfltr}
        options.setdefault("extra_args", {}).update({"permission-prompts": "none"})
    with contextlib.ExitStack() as settings_owner:
        options["settings"] = claude_settings.merge_settings(
            _parent_settings(),
            env,
            root_session_id,
            force_file=api_env_file is not None,
            isolate_auth=isolate_auth,
            isolation_keys=connection_settings.isolation_keys(connection),
            cwd=cwd,
            project_settings=not lightweight,
            drop_parent_env=frozenset({_delegated_session.HANDOFF_PATH_ENV}),
        )
        if isinstance(options["settings"], claude_settings.SettingsFile):
            settings_owner.enter_context(options["settings"])
        result = ClaudeAgentOptions(**options)
        settings_owner.pop_all()
        return result


def check_dependencies() -> None:
    """Claude Agent SDKの依存を読み込み、optionsを構築する。"""
    options = _build_options(str(pathlib.Path.cwd()), None, None)
    claude_settings.cleanup_settings(options)


def _message_name(message: Any) -> str:
    return message.__class__.__name__


def _assistant_text(message: Any) -> str:
    blocks = getattr(message, "content", ())
    return "\n".join(text for block in blocks if (text := getattr(block, "text", None)) is not None and isinstance(text, str))


def consume_api_retry(session: SessionState, data: dict[str, Any]) -> None:
    """CLIのturn内再試行を、活動時刻を保ったまま共通の失敗状態へ写す。"""
    delay = data.get("retry_delay_ms")
    retry_at = None
    if isinstance(delay, (int, float)) and not isinstance(delay, bool):
        retry_at = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(milliseconds=delay)).isoformat()
    status = data.get("error_status")
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    attempt = data.get("attempt")
    attempt = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None
    error = data.get("error")
    error_type = error.get("type") if isinstance(error, dict) else error
    limit = session.usage_limit
    waiting = status == 429 and limit is not None and limit.is_wait_target
    session.record_api_error(
        resume_waits.USAGE_LIMIT_ERROR_TYPE if waiting else str(error_type or "api_error"),
        status,
        retry_at=retry_at,
        attempt=attempt,
    )
    if waiting:
        assert limit is not None and session.api_error is not None
        session.api_error.update(limit_type=limit.limit_type, resets_at=limit.resets_at_iso() or retry_at)


def recover_api_retry(session: SessionState, data: dict[str, Any], *, record_notification: bool = True) -> None:
    """途中通知と終了失敗を同じ接続選択へ渡し、下位CLIの再試行終了への依存を解く。"""
    if record_notification:
        consume_api_retry(session, data)
    elif session.api_error is not None and session.usage_limit is not None and session.usage_limit.is_wait_target:
        session.api_error.update(
            type=resume_waits.USAGE_LIMIT_ERROR_TYPE,
            limit_type=session.usage_limit.limit_type,
            resets_at=session.usage_limit.resets_at_iso() or session.api_error.get("retry_at"),
        )
    if data.get("error_status") != 429 and session.usage_limit_resume_at is not None:
        resume_waits.clear_usage_limit_wait(session)
        session.awaiting_auto_resume = False
        session.pending_result = None
    if session.interrupt_requested or session.claude_api_resume_pending or session.usage_limit_resume_at is not None:
        return
    if session.usage_limit is None or not session.usage_limit.is_wait_target or data.get("error_status") != 429:
        return
    failure = {
        "status": "failed",
        "agent_message": session.agent_message,
        "error": {"message": session.usage_limit.describe(), "apiErrorStatus": 429},
    }
    resume_waits.begin_usage_limit_wait(session, failure)


def consume_assistant_message(session: SessionState, message: Any) -> None:
    """assistantメッセージをsessionの共有状態へ反映する。

    正常なメッセージではテキストの有無によらず活動時刻を進める。進めないと、ツール呼び出しだけを長時間続ける
    sessionへ停滞の印が付き、委譲元が不要な催促と巻き取りへ進む。
    ツール呼び出しの記録はテキストの反映後に行う。同じメッセージがテキストと
    ツール呼び出しの両方を持つ場合、後に発行したツール呼び出しを最後の行動とするためである。

    API失敗（429など）は`error`付きの合成メッセージとして`ResultMessage`より前に届くため、
    モデル出力と活動時刻の更新から除く。数えると、可用性失敗を確定する前に`start`の終了待ちを打ち切り、
    再試行が続く間も停滞を検出できなくなる。
    正常な起動では、完成したassistantメッセージより先に届くAPIの応答開始（`message_start`）で
    観測済みになる。完成したメッセージは思考と最初の内容ブロックの生成が終わるまで届かない。
    """
    error = getattr(message, "error", None)
    if error is not None:
        detail = _assistant_text(message)
        status_match = re.search(r"\bAPI Error:\s*(\d{3})\b", detail)
        status = int(status_match.group(1)) if status_match is not None else None
        if status is None and error == "rate_limit":
            status = 429
        error_type = "rate_limit_error" if error == "rate_limit" else str(error)
        session.record_api_error(error_type, status)
        return
    session.api_error = None
    if session.usage_limit_resume_at is not None:
        resume_waits.clear_usage_limit_wait(session)
        session.awaiting_auto_resume = False
        session.pending_result = None
    session.model_output_observed = True
    # 正常なモデル出力は利用上限で拒否されていない証拠であり、以前の拒否の報告を待機の根拠から外す。
    if session.usage_limit is not None and session.usage_limit.is_wait_target:
        session.usage_limit = None
    text = _assistant_text(message)
    if text.strip():
        session.agent_message = text
        session.set_progress(text)
    wait_output_tracking.consume_claude_agents_server_message(session, message)
    session.touch()


def _returns_to_subscription(session: SessionState) -> bool:
    """利用上限でAPIへ移した委譲先が、解除予定時刻を過ぎ、使用順で現在の種別より前にサブスクリプションを持つかを返す。"""
    retry_at = session.claude_subscription_retry_at
    if retry_at is None or session.claude_connection not in claude_connection.API_KINDS or session.claude_role != "delegate":
        return False
    if time.time() < retry_at:
        return False
    kinds = [item.kind for item in claude_connection.candidates("delegate", env=child_environment.inherited()).connections]
    if "subscription" not in kinds:
        return False
    return session.claude_connection not in kinds or kinds.index("subscription") < kinds.index(session.claude_connection)


def _connection_kind(connection: claude_connection.Connection) -> str:
    """残高不足の並べ替えへ渡す接続の種別を返す。"""
    return connection.kind


class ClaudeServerManager:
    """Claudeセッションの所有タスクと結果メタデータを管理する。"""

    START_FAILURE_EXCLUDES_CANDIDATE: typing.ClassVar[bool] = False
    """起動の例外は候補を変えても結果が変わらないため、次の候補へ進まずに委譲元へ返す。"""
    INTERRUPT_REQUIRES_TURN_ID: typing.ClassVar[bool] = False
    """中断の要求にturnの識別子を使わない。"""
    ORPHAN_TAKEOVER: typing.ClassVar[bool] = False
    """所有者のいない登録簿の記録を、委譲先CLIの記録から終了として引き継がない。"""

    async def __aenter__(self) -> ClaudeServerManager:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    @staticmethod
    def unavailable_reason(session: SessionState) -> str | None:
        """終了したsessionがengineの可用性を理由に失敗した場合、その除外理由を返す。

        429は利用枠の種類を付けて記録し、旧版が記録した`429`と区別する。
        """

        def api_status_reason(status: Any) -> str:
            if status != 429:
                return str(status)
            limit_type = session.usage_limit.limit_type if session.usage_limit is not None else None
            return f"429:{limit_type or 'rate_limit'}"

        return engine_availability.unavailable_reason(session, api_status_reason=api_status_reason)

    @staticmethod
    def excludes_with_recorded_reason(reason: str) -> bool:
        """記録済みの除外理由が、候補を除外する根拠になるかを返す。

        旧版が429へ記録した`429`はWeekly limitと5時間の利用上限による拒否も含むため、根拠にせず、
        その候補で起動して利用枠の報告から判定し直す。
        """
        return reason != engine_availability.LEGACY_CLAUDE_RATE_LIMIT_REASON

    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
        client_factory: Callable[[Any], Any] | None = None,
        expire_session: Callable[[str], None] | None = None,
        publish_registry: bool = False,
        *,
        root_session_id: str | None = None,
        validate_session_id: session_identity.IdentityValidator | None = None,
    ) -> None:
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
        self._client_factory = client_factory or self._default_client_factory
        self._expire_session = expire_session or self._expire_local_session
        self._publish_registry = publish_registry
        self._root_session_id = root_session_id
        self._validate_session_id = validate_session_id or session_identity.identity_validator(self.sessions, root_session_id)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._task_sessions: dict[asyncio.Task[Any], str] = {}
        self._channels: dict[str, _CommandChannel] = {}
        self._disconnects: set[asyncio.Task[None]] = set()
        self._residual_processes: dict[str, list[psutil.Process]] = {}

    def _expire_local_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    @staticmethod
    def _default_client_factory(options: Any) -> Any:
        from claude_agent_sdk import ClaudeSDKClient

        return ClaudeSDKClient(options)

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
    ) -> SessionState:
        role = "orchestrator" if model_type == "orchestrate" else "delegate"
        choices = claude_connection.candidates(role, env=child_environment.inherited())
        failures = list(choices.diagnostics)
        connections = choices.connections
        if role == "delegate":
            # 同じプロセスで直前に残高不足を観測した場合は、クレジットで失敗して切り替える往復を省く。
            connections = api_credits.order(connections, _connection_kind, engine="claude")
            if connections != choices.connections:
                _LOG.info("claude_connection_reordered reason=api_credits order=%s", ",".join(c.kind for c in connections))
        for connection in connections:
            _LOG.info("claude_connection_attempt connection=%s role=%s resumed=false", connection.kind, role)
            try:
                session = await self._start_owned_task(
                    prompt,
                    cwd,
                    model,
                    effort,
                    session_id=None,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=excluded_candidates,
                    turn_seq=1,
                    api_env_file=connection.api_env_file,
                    connection_kind=connection.kind,
                    isolation_env_file=connection.isolation_env_file,
                )
                session.claude_connection_diagnostics = choices.warnings
                _LOG.info("claude_connection_selected session_id=%s connection=%s", session.session_id, connection.kind)
                return session
            except (SessionInitializationTimeoutError, session_identity.DuplicateSessionIdentityError) as error:
                _LOG.info(
                    "claude_connection_failed connection=%s phase=initialization error_type=%s",
                    connection.kind,
                    type(error).__name__,
                )
                raise
            except (OSError, ValueError, RuntimeError) as error:
                _LOG.info(
                    "claude_connection_failed connection=%s phase=initialization error_type=%s",
                    connection.kind,
                    type(error).__name__,
                )
                if connection.kind == "inherit":
                    raise
                failures.append(f"{connection.kind}: {type(error).__name__}: {error}。認証と利用枠を確認する")
        raise RuntimeError("Claude接続候補が全て不成立: " + "; ".join(failures))

    async def resume(
        self,
        session_id: str,
        prompt: ResumePrompt,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
        turn_seq: int = 0,
        fast_mode: bool | None = None,
        api_env_file: str | None = None,
        connection_kind: str = "inherit",
        existing_session: SessionState | None = None,
        isolation_env_file: str | None = None,
        handoff_prompt: str = "",
    ) -> SessionState:
        """保存済みClaude sessionを新しい所有タスクで再開する。

        `fast_mode`はCodexの速度の指定であり、本backendは受け取って使わない。
        """
        del fast_mode
        selected_connection = claude_connection.apply_current_settings(
            connection_settings.Connection(
                existing_session.claude_connection or "inherit" if existing_session else connection_kind,
                api_env_file or (existing_session.claude_api_env_file if existing_session else None),
                isolation_env_file,
            ),
            role=(existing_session.claude_role if existing_session else None)
            or ("orchestrator" if model_type == "orchestrate" else "delegate"),
            env=child_environment.inherited(),
        )
        connection_kind = selected_connection.kind
        api_env_file = selected_connection.api_env_file
        isolation_env_file = selected_connection.isolation_env_file
        await self._stop_owned_task(session_id)
        if existing_session is not None:
            begin_reply(existing_session, preserve_waits=True)
        selected = connection_kind
        role = "orchestrator" if model_type == "orchestrate" else "delegate"
        _LOG.info("claude_connection_attempt connection=%s role=%s resumed=true", selected, role)
        try:
            session = await self._start_owned_task(
                prompt,
                cwd,
                model,
                effort,
                session_id=session_id,
                model_type=model_type,
                launch_kind=launch_kind,
                excluded_candidates=excluded_candidates,
                turn_seq=turn_seq + 1,
                api_env_file=api_env_file,
                connection_kind=selected,
                existing_session=existing_session,
                isolation_env_file=isolation_env_file,
                handoff_prompt=handoff_prompt,
            )
        except (OSError, ValueError, RuntimeError, SessionInitializationTimeoutError) as error:
            _LOG.info(
                "claude_connection_failed connection=%s phase=initialization error_type=%s",
                selected,
                type(error).__name__,
            )
            raise
        _LOG.info("claude_connection_selected session_id=%s connection=%s", session.session_id, selected)
        return session

    async def _stop_owned_task(self, session_id: str) -> None:
        """同じsession IDを所有する旧タスクを終了し、再開時のキュー競合を防ぐ。"""
        task = next((item for item in self._tasks if self._task_sessions.get(item) == session_id), None)
        if task is None:
            await self._reclaim_runtime(session_id)
            return
        if not task.done():
            task.cancel()
        outcomes = await asyncio.gather(task, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                raise outcome

    async def _reclaim_runtime(self, session_id: str) -> None:
        """未回収の所有資源を再試行し、終了できないhandleを次の停止要求へ保持する。"""
        targets = self._residual_processes.get(session_id, [])
        if not targets:
            return
        residual = await run_file_operation(process_tree.reclaim_processes, targets)
        if residual:
            self._residual_processes[session_id] = residual
            raise session_errors.DelegateBackendError(
                "Claude CLIの所有資源を終了できなかった。所有状態を確認して停止を再試行する。"
            )
        self._residual_processes.pop(session_id, None)

    async def release_session(self, session: SessionState) -> None:
        """sessionを所有するタスクを終了し、SDKクライアントの接続を閉じる。"""
        await self._stop_owned_task(session.session_id)
        await session.drain_registration()

    async def _start_owned_task(
        self,
        prompt: str | ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        session_id: str | None,
        model_type: str | None,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
        turn_seq: int,
        api_env_file: str | None = None,
        connection_kind: str = "inherit",
        existing_session: SessionState | None = None,
        isolation_env_file: str | None = None,
        handoff_prompt: str = "",
    ) -> SessionState:
        """新規または保存済みsessionを所有する長命タスクを開始する。"""
        diagnostic = _InitializationDiagnostic()
        if connection_kind == "subscription" and isolation_env_file is None:
            isolation_env_file = claude_connection.apply_current_settings(
                connection_settings.Connection(connection_kind),
                role="orchestrator" if model_type == "orchestrate" else "delegate",
                env=child_environment.inherited(),
            ).isolation_env_file
        debug_file = await run_file_operation(_prepare_debug_file, launch_kind)
        diagnostic.debug_file = str(debug_file)
        options = await run_file_operation(
            _build_options,
            cwd,
            model,
            effort,
            session_id,
            launch_kind=launch_kind,
            stderr=diagnostic.capture_stderr,
            debug_file=debug_file,
            root_session_id=self._root_session_id,
            api_env_file=api_env_file,
            connection_kind=connection_kind,
            isolation_env_file=isolation_env_file,
            handoff_prompt=existing_session.prompt
            if existing_session
            else prompt
            if isinstance(prompt, str)
            else handoff_prompt,
        )
        loop = asyncio.get_running_loop()
        initialized: asyncio.Future[SessionState] = loop.create_future()
        task: asyncio.Task[Any] = asyncio.create_task(
            self._run(
                prompt,
                cwd,
                model,
                effort,
                options,
                initialized,
                expected_session_id=session_id,
                model_type=model_type,
                launch_kind=launch_kind,
                excluded_candidates=excluded_candidates,
                turn_seq=turn_seq,
                diagnostic=diagnostic,
                api_env_file=api_env_file,
                connection_kind=connection_kind,
                existing_session=existing_session,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._forget_task)
        try:
            session = await asyncio.wait_for(initialized, timeout=shared_state.SESSION_INITIALIZATION_TIMEOUT)
            if diagnostic.mcp_recovery_task is not None:
                # 起動の全試行と可用性確認を予約し、残予算を回復待ちと応答確定へ半分ずつ割り当てる。
                recovery_budget = (
                    max(
                        0.0,
                        shared_state.HOST_BACKGROUND_THRESHOLD_SECONDS
                        - shared_state.SESSION_INITIALIZATION_TIMEOUT * shared_state.SESSION_INITIALIZATION_ATTEMPTS
                        - shared_state.START_AVAILABILITY_TIMEOUT,
                    )
                    / 2
                )
                completed, _ = await asyncio.wait({diagnostic.mcp_recovery_task}, timeout=recovery_budget)
                if completed:
                    await diagnostic.mcp_recovery_task
        except TimeoutError as exc:
            # SDKがinitを届けないまま接続を保つ場合、所有タスクが失敗してもこの待機は解消しない。
            await self._release_unstarted_task(task)
            diagnostic.record_exception(exc)
            details = await diagnostic.public_async()
            _LOG.error("Claude session初期化timeout: diagnostic=%s", details)
            raise SessionInitializationTimeoutError(
                f"Claude session did not reach init within {shared_state.SESSION_INITIALIZATION_TIMEOUT:.0f}s: "
                f"cwd={cwd}, launch_kind={launch_kind}, model={model}; diagnostic={details}"
            ) from exc
        except BaseException as exc:
            await self._release_unstarted_task(task)
            diagnostic.record_exception(exc)
            _LOG.error("Claude session初期化失敗: diagnostic=%s", await diagnostic.public_async())
            raise
        diagnostic.debug_file = str(
            await run_file_operation(rename_debug_file_for_session, debug_file, session.session_id, launch_kind)
        )
        await session.drain_registration()
        return session

    async def _release_unstarted_task(self, task: asyncio.Task[Any]) -> None:
        """初期化を完了していない所有タスクを終了し、SDKクライアントの接続を解放する。"""
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._forget_task(task)

    async def _disconnect_client(self, client: Any) -> None:
        """SDKクライアントの切断を、所有タスクの取り消しから切り離して完走させる。

        SDKが所有するCLIへの通常の終了要求は切断処理が担当する。
        取り消された実行では、SDKの切断処理が内部のcheckpointで例外を送出し、
        子プロセスの終了処理へ到達しないまま接続だけを閉じる。
        このため切断は別taskで実行し、待機の側が取り消されてもそのtaskの実行を継続させる。
        """
        task: asyncio.Task[None] = asyncio.create_task(self._close_client(client))
        self._disconnects.add(task)
        task.add_done_callback(self._disconnects.discard)
        with contextlib.suppress(asyncio.CancelledError):
            done, _ = await asyncio.wait({task}, timeout=_DISCONNECT_TIMEOUT)
            if not done:
                task.cancel()

    @staticmethod
    async def _close_client(client: Any) -> None:
        """SDKクライアントを切断し、切断処理自体の失敗は無視する。"""
        with contextlib.suppress(Exception):
            await client.disconnect()

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        channel = self._channels.get(session.session_id)
        if channel is None:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        # 通常の追送と、孫sessionの終了・利用上限・過負荷の後の自動再開は、いずれもここで次のturnを始める。
        # 進行中のturnと、CLIが自ら次のturnを始め得る待機（バックグラウンドタスクなど）では所有タスクを止めない。
        held_without_pending = session.awaiting_auto_resume and not resume_waits.has_pending_auto_resume_targets(session)
        role = session.claude_role or ("orchestrator" if session.model_type == "orchestrate" else "delegate")
        # 保持した接続の種別を使用順から外した会話は、次のturnで使用順の先頭の利用可能な候補へ移す。
        if (session.terminal or held_without_pending) and claude_connection.outside_order(session.claude_connection, role):
            previous = session.previous_result() if session.terminal and not session.awaiting_auto_resume else None
            await self._resume_next_turn(session, prompt)
            return {"delivery": "reply_started", "previous_result": previous}
        if (session.terminal or held_without_pending) and _returns_to_subscription(session):
            return await self._return_to_subscription(session, prompt)
        async with session.turn_control_lock:
            if not session.terminal and session.interrupt_requested:
                raise ActionableError(
                    "the active Claude turn is being interrupted", next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION
                )
            future = channel.send("prompt", prompt)
            actual_delivery, previous_result = await future
        result: dict[str, Any] = {"delivery": actual_delivery}
        if actual_delivery in {"reply_started", "reply_failed", "reply_ambiguous"}:
            result["previous_result"] = previous_result
        return result

    async def _return_to_subscription(self, session: SessionState, prompt: str) -> dict[str, Any]:
        """APIへ移した会話を、次のturnの指示とともにサブスクリプションの新しい所有タスクで再開する。

        接続は所有タスクの起動時に決まるため、上限でAPIへ移した場合と同じ再開の処理で同じsession IDを続ける。
        再開に失敗した場合はAPIの接続と戻す時刻を元へ戻し、呼び出し元へ失敗を伝える。
        """
        previous_result = session.previous_result() if session.terminal and not session.awaiting_auto_resume else None
        saved = (session.claude_connection, session.claude_api_env_file, session.claude_subscription_retry_at)
        session.claude_connection = "subscription"
        session.claude_api_env_file = None
        session.claude_subscription_retry_at = None
        session.claude_connection_failures.pop("subscription", None)
        _LOG.info("claude_subscription_return session_id=%s", session.session_id)
        try:
            await self._resume_next_turn(session, prompt)
        except BaseException:
            session.claude_connection, session.claude_api_env_file, session.claude_subscription_retry_at = saved
            raise
        return {"delivery": "reply_started", "previous_result": previous_result}

    async def _resume_next_turn(self, session: SessionState, prompt: str) -> None:
        """同じ会話IDの所有タスクへ、次のturnの入力を渡す。"""
        resume_prompt = ResumePrompt(prompt)
        try:
            await self.resume(
                session.session_id,
                resume_prompt,
                session.cwd,
                session.model,
                session.effort,
                model_type=session.model_type,
                launch_kind=session.launch_kind,
                excluded_candidates=session.excluded_candidates,
                turn_seq=session.turn_seq,
                existing_session=session,
            )
        finally:
            resume_prompt.close()

    async def interrupt(self, session: SessionState) -> None:
        """公開killから所有タスクへ中断要求を送り、受理を待つ。"""
        channel = self._channels.get(session.session_id)
        if channel is None and session.session_id not in self._residual_processes:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        if session.terminal:
            return
        session.interrupt_requested = True
        session.touch()
        try:
            if channel is None:
                await self._reclaim_runtime(session.session_id)
                delivery = "interrupt_accepted"
            else:
                future = channel.send("interrupt", "")
                delivery, _ = await future
        except Exception:
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            raise
        if delivery != "interrupt_accepted":
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            raise session_errors.DelegateBackendError(f"unexpected Claude interrupt delivery: {delivery}")
        # control要求の受理とCLIの終了は別の事象である。所有タスクの後始末でCLIと子孫を回収してから停止を公開する。
        await self._stop_owned_task(session.session_id)
        if session.pending_result is not None:
            resume_waits.finalize_pending_result(session, touch=False)
        resume_waits.clear_usage_limit_wait(session)
        session.claude_api_resume_pending = False
        session.api_error = None
        self._finalize_turn(session, {"status": "interrupted", "agent_message": session.agent_message, "error": session.error})
        await self._notify_waiters()

    def _forget_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        self._task_sessions.pop(task, None)

    async def _run(
        self,
        prompt: str | ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        options: Any,
        initialized: asyncio.Future[SessionState],
        *,
        expected_session_id: str | None,
        model_type: str | None,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
        turn_seq: int,
        diagnostic: _InitializationDiagnostic,
        api_env_file: str | None = None,
        connection_kind: str = "inherit",
        existing_session: SessionState | None = None,
    ) -> None:
        from claude_agent_sdk import TERMINAL_TASK_STATUSES

        client: Any = None
        usage_observer: _ContextUsageObserver | None = None
        session: SessionState | None = None
        channel = _CommandChannel()
        iterator: Any = None
        message_task: asyncio.Task[Any] | None = None
        command_task: asyncio.Task[Any] | None = None
        retrieved: _Command | None = None
        active_future: asyncio.Future[_DeliveryResult] | None = None
        # `SystemMessage(init)`より前に届いた利用枠の報告。sessionの作成時に引き継ぐ。
        early_usage_limit: claude_usage_limit.UsageLimitState | None = None
        early_api_retry: dict[str, Any] | None = None
        # 保留中の結果が完了通知による再開turnのものか。`idle`で確定するときに、`ResultMessage`の分岐で
        # 直接確定した場合と同じく`auto_resume_consumed`へ反映する。
        held_from_task_notification = False
        # `SystemMessage(init)`より前に届いたturn状態の報告。CLIはコマンドループの先頭で最初のturnの`running`を
        # 発行するため、initより先に届き得る。捨てると最初のturnの判定が追跡集合だけの方法へ戻るため、
        # sessionの作成時に引き継ぐ。
        early_cli_turn_state: str | None = None
        runtime_started_at = shared_state.utc_now()

        async def apply_api_retry(data: dict[str, Any]) -> None:
            """利用枠がSDK通知に無い場合だけ、会話・試行番号・待機時間が一致する元記録から利用枠を読む。"""
            assert session is not None
            if session.usage_limit is None or not session.usage_limit.is_wait_target:
                limit = await run_file_operation(
                    claude_retry_record.retry_usage_limit, session.session_id, data, started_at=runtime_started_at
                )
                if limit is not None:
                    session.usage_limit = limit
            recover_api_retry(session, data)

        async def initialize_session(session_id: str, data: dict[str, Any]) -> None:
            """initから同じ所有タスクのsessionを登録し、途中失敗時もその状態を保持する。"""
            nonlocal session
            await session_identity.validate_identity(self._validate_session_id, session_id, expected_session_id)
            diagnostic.stage = "initialized"
            _LOG.info("Claude session初期化完了: diagnostic=%s", await diagnostic.public_async())
            session = existing_session or SessionState(
                session_id=session_id,
                cwd=cwd,
                model_type=model_type,
                launch_kind=launch_kind,
                excluded_candidates=excluded_candidates,
                model=model,
                effort=effort,
                engine="claude",
                turn_seq=turn_seq,
                publish_registry=self._publish_registry,
                claude_api_env_file=api_env_file or None,
                claude_connection=connection_kind,
                claude_role="orchestrator" if model_type == "orchestrate" else "delegate",
            )
            session.claude_connection = connection_kind
            session.claude_api_env_file = api_env_file
            # 使用順にサブスクリプションが無い会話は、上限解除後もサブスクリプションへ戻さない。
            if not claude_connection.order_includes("subscription", session.claude_role or "delegate"):
                session.claude_subscription_retry_at = None
            self.sessions[session_id] = session
            session.claude_mcp_diagnostics = ()
            diagnostic.mcp_recovery_task = asyncio.create_task(self._recover_mcp_connections(client, data, session))
            await session.attach_executor_async(diagnostic.child_pid)
            session.usage_limit = early_usage_limit
            session.cli_turn_state = early_cli_turn_state
            session.status = "starting" if expected_session_id is None else "running"
            if early_api_retry is not None:
                await apply_api_retry(early_api_retry)
            session.touch()
            self._channels[session_id] = channel
            current_task = asyncio.current_task()
            if current_task is not None:
                self._task_sessions[current_task] = session_id
            if not initialized.done():
                initialized.set_result(session)

        try:
            diagnostic.stage = "creating_client"
            client = self._client_factory(options)
            usage_observer = _ContextUsageObserver(client)
            diagnostic.stage = "connecting"
            await client.connect()
            diagnostic.capture_client(client)
            diagnostic.stage = "connected"
            if isinstance(prompt, ResumePrompt):
                await prompt.deliver(client.query)
            else:
                await client.query(prompt)
            diagnostic.stage = "query_sent"
            iterator = aiter(client.receive_messages())
            diagnostic.stage = "receiving_messages"
            while True:
                loop = asyncio.get_running_loop()
                now = loop.time()
                deadline = resume_waits.auto_resume_wait_deadline(session) if session is not None else None
                if session is not None and deadline is not None and deadline <= now:
                    (await self._finalize_pending_result(session))
                    await self._notify_waiters()
                    continue
                timeout: float | None = None
                if session is not None and session.retention_deadline is not None:
                    timeout = session.retention_deadline - now
                    if timeout <= 0:
                        self._expire_session(session.session_id)
                        break
                if deadline is not None:
                    auto_resume_timeout = deadline - now
                    timeout = auto_resume_timeout if timeout is None else min(timeout, auto_resume_timeout)

                if iterator is not None and message_task is None:
                    message_task = asyncio.create_task(anext(iterator))
                if command_task is None and retrieved is None:
                    command_task = asyncio.create_task(channel.get())
                if retrieved is not None:
                    # 新queryより前に既に届いたイベントを処理する。読取taskが新しい
                    # イベントを待つ状態になったところで、保持していたコマンドを配送する。
                    await asyncio.sleep(0)
                    if message_task is None or not message_task.done():
                        command = retrieved
                        retrieved = None
                        active_future = command[2]
                        if (
                            command[0] == "interrupt"
                            and session is not None
                            and session.awaiting_auto_resume
                            and message_task is not None
                        ):
                            message_task.cancel()
                            await asyncio.gather(message_task, return_exceptions=True)
                            message_task = None
                        iterator = await self._handle_command(client, session, command, iterator)
                        active_future = None
                        continue
                pending = {task for task in (message_task, command_task) if task is not None}
                if not pending:
                    raise RuntimeError("Claude session has no message stream or command queue")
                done, _ = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if session is not None and resume_waits.auto_resume_wait_deadline(session) is not None:
                        (await self._finalize_pending_result(session))
                        await self._notify_waiters()
                        continue
                    if session is not None:
                        self._expire_session(session.session_id)
                    break

                if command_task is not None and command_task in done:
                    retrieved = command_task.result()
                    command_task = None

                if message_task is not None and message_task in done:
                    completed_message_task = message_task
                    message_task = None
                    try:
                        message = completed_message_task.result()
                    except StopAsyncIteration:
                        if (
                            session is not None
                            and session.awaiting_auto_resume
                            and session.usage_limit_resume_at is None
                            and not session.claude_api_resume_pending
                            and not resume_waits.has_pending_auto_resume_targets(session)
                        ):
                            (await self._finalize_pending_result(session))
                            iterator = None
                            await self._notify_waiters()
                        elif (
                            session is not None
                            and session.awaiting_auto_resume
                            or session is not None
                            and session.result_available
                        ):
                            iterator = None
                        else:
                            raise session_errors.DelegateBackendError(
                                "Claude Agent SDK message stream ended before ResultMessage"
                            ) from None
                    else:
                        diagnostic.record_message(message)
                        name = _message_name(message)
                        # 公開済みのturnの後始末も同じ受信列から回収する。新queryを送るまで
                        # assistant/resultを公開済みの状態へ適用せず、旧結果の二重公開を防ぐ。
                        if (
                            session is not None
                            and session.terminal
                            and name
                            in {
                                "AssistantMessage",
                                "StreamEvent",
                                "ResultMessage",
                            }
                        ):
                            continue
                        if name == "SystemMessage" and getattr(message, "subtype", None) == "init":
                            data = getattr(message, "data", {})
                            session_id = data.get("session_id") if isinstance(data, dict) else None
                            if not isinstance(session_id, str) or not session_id:
                                raise session_errors.DelegateBackendError("Claude init message did not contain session_id")
                            if expected_session_id is not None and session_id != expected_session_id:
                                raise session_errors.DelegateBackendError("Claude resume returned an unexpected session_id")
                            # `_CommandChannel`と`SessionState`は所有タスクの生存期間で1つだけ保持する。
                            # キューは永続session IDを要しないため所有タスクの開始時に生成する。
                            # 状態は同IDをinitからしか取得できないため、最初の有効なinitで生成し、
                            # turnごとにinitが再送されても再生成しない。
                            if session is None:
                                await initialize_session(session_id, data)
                            elif session_id != session.session_id:
                                raise session_errors.DelegateBackendError("Claude init message reported a different session_id")
                            assert session is not None
                            usage_observer.request(session)
                        elif (
                            name == "SystemMessage"
                            and getattr(message, "subtype", None) == "compact_boundary"
                            and session is not None
                        ):
                            usage_observer.request(session)
                        elif name == "SystemMessage" and getattr(message, "subtype", None) == "session_state_changed":
                            # CLIのturn状態の報告。`idle`は次のturnが発生しないことを示すため、保留中の結果を
                            # 確定してよい時点になる。ただしシェルのバックグラウンドタスクの稼働中も`idle`が届くため、
                            # 追跡集合が残る間は確定せず、そのタスクの完了通知による再開turnの結果を待つ。
                            data = getattr(message, "data", {})
                            reported = data.get("state") if isinstance(data, dict) else None
                            if not isinstance(reported, str) or not reported:
                                reported = None
                            if session is None:
                                if reported is not None:
                                    early_cli_turn_state = reported
                            elif reported is not None:
                                session.cli_turn_state = reported
                            # 完了通知で次のturnが始まった後は、前のturnの待機期限で古い待機表明を公開させない。
                            if session is not None and reported == "running" and resume_waits.release_auto_resume_hold(session):
                                await self._notify_waiters()
                            if (
                                session is not None
                                and reported == "idle"
                                and session.awaiting_auto_resume
                                and session.usage_limit_resume_at is None
                                and not session.claude_api_resume_pending
                                and not resume_waits.has_pending_auto_resume_targets(session)
                            ):
                                if held_from_task_notification:
                                    session.auto_resume_consumed = True
                                (await self._finalize_pending_result(session))
                                await self._notify_waiters()
                        elif name == "SystemMessage" and getattr(message, "subtype", None) == "api_retry":
                            data = getattr(message, "data", {})
                            if isinstance(data, dict):
                                if session is None:
                                    early_api_retry = data
                                else:
                                    await apply_api_retry(data)
                                    await self._notify_waiters()
                        elif name == "RateLimitEvent":
                            # 利用枠の報告はモデル活動ではないため活動時刻を進めない。
                            usage_limit = claude_usage_limit.from_event(message)
                            if usage_limit is not None:
                                if session is None:
                                    early_usage_limit = usage_limit
                                else:
                                    session.usage_limit = usage_limit
                                    if session.api_error is not None and session.api_error.get("http_status") == 429:
                                        recover_api_retry(session, {"error_status": 429}, record_notification=False)
                                        await self._notify_waiters()
                        elif name == "AssistantMessage" and session is not None:
                            # 保留後のモデル出力は次のturnに属する。Stopの処理中に完了通知が先に届いた場合は、
                            # 保留の前に`running`が報告済みで、次のturnの開始が出力でしか観測できない。
                            resume_waits.release_auto_resume_hold(session)
                            consume_assistant_message(session, message)
                            if getattr(message, "error", None) is None:
                                usage_observer.request(session)
                            await self._notify_waiters()
                        elif name == "StreamEvent" and session is not None:
                            # 失敗した要求はストリーミング応答を始めないため、応答開始を可用性の確定点とする。
                            # それ以外の部分出力は完成したメッセージで反映されるため読み捨てる。
                            event = getattr(message, "event", None)
                            if isinstance(event, dict) and event.get("type") == "message_start":
                                resume_waits.release_auto_resume_hold(session)
                                session.api_error = None
                                session.usage_limit = None
                                if session.usage_limit_resume_at is not None:
                                    resume_waits.clear_usage_limit_wait(session)
                                    session.awaiting_auto_resume = False
                                    session.pending_result = None
                                session.model_output_observed = True
                                await self._notify_waiters()
                            elif isinstance(event, dict) and event.get("type") == "message_stop":
                                usage_observer.request(session)
                        elif name == "UserMessage" and session is not None:
                            await wait_output_tracking.consume_claude_agents_server_message_async(session, message)
                            session.touch()
                            await self._notify_waiters()
                        elif name == "TaskStartedMessage" and session is not None:
                            session.live_tasks[message.task_id] = shared_state.LiveTask(
                                task_type=str(getattr(message, "task_type", None) or ""),
                                description=str(getattr(message, "description", None) or ""),
                                started_at=datetime.datetime.now(datetime.UTC).isoformat(),
                            )
                            session.touch()
                            await self._notify_waiters()
                        elif name in {"TaskUpdatedMessage", "TaskNotificationMessage"} and session is not None:
                            if getattr(message, "status", None) in TERMINAL_TASK_STATUSES:
                                session.live_tasks.pop(message.task_id, None)
                                session.touch()
                                await self._notify_waiters()
                        elif name == "ResultMessage" and session is not None:
                            usage_observer.request(session)
                            held_from_task_notification = await self._consume_result_message(
                                session, message, held_from_task_notification
                            )
                            await self._notify_waiters()

        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            diagnostic.record_exception(exc)
            if session is None:
                if not initialized.done():
                    initialized.set_exception(exc)
            else:
                self._record_failure(session, exc, diagnostic=diagnostic)
                await self._notify_waiters()
        finally:

            async def cleanup_runtime() -> None:
                nonlocal retrieved
                if diagnostic.mcp_recovery_task is not None:
                    if not diagnostic.mcp_recovery_task.done():
                        diagnostic.mcp_recovery_task.cancel()
                    await asyncio.gather(diagnostic.mcp_recovery_task, return_exceptions=True)
                for task in (message_task, command_task):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (message_task, command_task) if task is not None), return_exceptions=True
                )
                if command_task is not None and not command_task.cancelled() and command_task.exception() is None:
                    retrieved = command_task.result()
                if retrieved is not None and not retrieved[2].done():
                    retrieved[2].set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))
                if active_future is not None and not active_future.done():
                    active_future.set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))
                if client is not None:
                    try:
                        if usage_observer is not None:
                            await usage_observer.close()
                    finally:
                        # 切断より先に子孫を列挙し、親の終了後も所有資源を回収できるようにする。
                        descendants = []
                        try:
                            descendants = await run_file_operation(_owned_runtime_processes, diagnostic.child_process)
                            if session is not None and descendants:
                                self._residual_processes[session.session_id] = descendants
                        finally:
                            try:
                                await self._disconnect_client(client)
                            finally:
                                if session is not None:
                                    await self._reclaim_runtime(session.session_id)
                                else:
                                    residual = await run_file_operation(process_tree.reclaim_descendants, descendants)
                                    process_tree.log_residual(residual, context="claude initialization")

            async def cleanup() -> None:
                async with contextlib.AsyncExitStack() as resources:
                    resources.push_async_callback(run_file_operation, claude_settings.cleanup_settings, options)
                    resources.callback(channel.close)
                    if session is not None:
                        resources.callback(self._channels.pop, session.session_id, None)
                        resources.push_async_callback(session.drain_registration)
                    await cleanup_runtime()

            await finish_task(asyncio.create_task(cleanup()))

    @staticmethod
    async def _recover_mcp_connections(client: Any, initialization: dict[str, Any], session: SessionState) -> None:
        def report_pending(warnings: tuple[str, ...]) -> None:
            session.claude_mcp_diagnostics = warnings

        session.claude_mcp_diagnostics = await claude_mcp.recover_connections(
            client, initialization, session.session_id, report_pending=report_pending
        )

    async def _consume_result_message(self, session: SessionState, message: Any, held_from_task_notification: bool) -> bool:
        """turnの結果を保留または確定し、保留結果がtask完了通知のturnから届いたかを引き継ぐ。"""
        await wait_output_tracking.consume_agents_wait_background_outputs_async(session)
        result = self._result_values(session, message)
        if session.interrupt_requested:
            # killの所有者がCLIの終了を確認してから公開する。後着の失敗結果を回復の契機へ戻さない。
            session.pending_result = {**result, "status": "interrupted"}
            session.awaiting_auto_resume = True
            session.auto_resume_deadline = None
            return held_from_task_notification
        if session.claude_api_resume_pending:
            if result["status"] == "completed":
                session.api_error = None
            return held_from_task_notification
        if session.usage_limit_resume_at is not None:
            if result["status"] != "completed":
                return held_from_task_notification
            resume_waits.clear_usage_limit_wait(session)
            session.api_error = None
            session.usage_limit = None
        # 対象の利用上限では結果を保留し、MCP層の常駐監視がAPI移行または解除後の継続を進める。
        if result_contract.hold_invalid_result(session, result):
            return held_from_task_notification
        if resume_waits.begin_usage_limit_wait(session, result):
            return held_from_task_notification
        # 自動再開したturnもバックグラウンドタスクを残して待機を表明し得るため、`origin`によらず保留を判定する。
        # 完了通知は`ResultMessage`より前にも届く。Stop hookの実行中にバックグラウンドタスクが終わると、
        # その通知で追跡集合は空になるが、CLIは`ResultMessage`の後に通知の再開turnを開始する。
        # 次のturnの有無はCLIの`idle`の報告だけが確定できるため、報告が`idle`になるまで保留し、
        # `session_state_changed`の分岐で確定する。この判定はMCP層の再開済みsessionにも適用する。
        # 根拠とした版と順序は`docs/development/audit-records.md`の次の節にある。
        # 「agent-toolkit/agent_toolkit/_agents_server/claude.py：結果の保留とturn状態の報告：2026年10月4日」
        if resume_waits.has_pending_auto_resume_targets(session) and (
            not session.auto_resume_consumed or resume_waits.cli_turn_may_continue(session)
        ):
            self._log_turn_state_fallback(session)
            held_from_task_notification = getattr(message, "origin", None) == {"kind": "task-notification"}
            resume_waits.begin_auto_resume_wait(session, result)
            session.touch()
        else:
            self._log_turn_state_fallback(session)
            if getattr(message, "origin", None) == {"kind": "task-notification"}:
                session.auto_resume_consumed = True
            self._finalize_turn(session, result)
        return held_from_task_notification

    async def _handle_command(
        self,
        client: Any,
        session: SessionState | None,
        command: _Command,
        iterator: Any,
    ) -> Any:
        command_kind, prompt, future = command
        if session is None:
            if not future.done():
                future.set_exception(ValueError("Claude session initialization is incomplete"))
            return iterator
        if command_kind == "interrupt":
            session.auto_resume_consumed = True
            if session.terminal:
                if not future.done():
                    future.set_result(("interrupt_accepted", None))
                return iterator
            try:
                await client.interrupt()
            except Exception as exc:
                session.interrupt_requested = False
                session.touch()
                if not future.done():
                    future.set_exception(exc)
            else:
                if session.awaiting_auto_resume:
                    # モデルのturnは終わっており、Claude Code CLIはturnの外で受けた中断要求へ`ResultMessage`を返さない。
                    # 以後の再開turnは読まず、killの所有者が資源を回収してから停止結果を確定する。
                    iterator = None
                if not future.done():
                    future.set_result(("interrupt_accepted", None))
            await self._notify_waiters()
            return iterator
        if future.cancelled():
            return iterator
        if session.awaiting_auto_resume and not resume_waits.is_background_completion_wait(session):
            (await self._finalize_pending_result(session))
        held = resume_waits.HeldTurn.capture(session)
        kind = "reply" if session.terminal or held is not None else "steer"
        previous_result = session.previous_result() if kind == "reply" and held is None else None
        try:
            if kind == "reply":
                begin_reply(session, preserve_waits=held is not None)
            await client.query(prompt)
        except Exception as exc:
            if held is not None:
                held.restore(session)
                if iterator is None:
                    iterator = aiter(client.receive_messages())
                if not future.done():
                    future.set_result(("reply_failed", None))
            elif kind == "reply":
                self._record_failure(session, exc)
                if not future.done():
                    future.set_result(("reply_failed", previous_result))
            elif not future.done():
                future.set_exception(exc)
            await self._notify_waiters()
            return iterator
        if kind == "reply":
            if not future.done():
                future.set_result(("reply_started", previous_result))
        elif not future.done():
            future.set_result(("steered", None))
        session.retention_deadline = None
        session.touch()
        await self._notify_waiters()
        # 保留中は同じ受信を続ける。追送中に到着した完了通知を読取taskの取消で失わない。
        return aiter(client.receive_messages()) if kind == "reply" and iterator is None else iterator

    @staticmethod
    def _result_values(session: SessionState, message: Any) -> dict[str, Any]:
        terminal_reason = getattr(message, "terminal_reason", None)
        if terminal_reason in {"aborted_streaming", "aborted_tools"}:
            status = "interrupted"
        else:
            status = "failed" if bool(getattr(message, "is_error", False)) else "completed"
        result = getattr(message, "result", None)
        agent_message = result if isinstance(result, str) and result.strip() else session.agent_message
        error: dict[str, Any] | None = None
        errors = getattr(message, "errors", None)
        if status == "completed" and not agent_message.strip():
            status = "failed"
            error = {"message": "Claude Agent SDK returned no assistant output"}
        if status == "failed":
            if error is not None:
                pass
            elif isinstance(errors, list) and errors:
                error = {"message": "; ".join(str(item) for item in errors)}
            elif isinstance(result, str) and result:
                error = {"message": result}
            else:
                error = {"message": "Claude Agent SDK returned an error"}
            # `api_error_status`は失敗したAPI呼び出しのHTTPステータスを示す。
            # MCP層がengineの可用性を判定するために保持する。
            api_error_status = getattr(message, "api_error_status", None)
            if isinstance(api_error_status, int):
                error["apiErrorStatus"] = api_error_status
        return {"status": status, "agent_message": agent_message, "error": error}

    @staticmethod
    def _finalize_turn(session: SessionState, result: dict[str, Any]) -> None:
        session.awaiting_auto_resume = False
        session.auto_resume_deadline = None
        session.pending_result = None
        session.status = result["status"]
        session.agent_message = result["agent_message"]
        session.error = result["error"]
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.touch()

    @staticmethod
    def _log_turn_state_fallback(session: SessionState) -> None:
        """CLIのturn状態の報告を受けないまま結果を判定したことを、sessionごとに1回だけ警告する。

        報告が無い場合は追跡集合だけで判定する従来の方法へ戻り、`ResultMessage`より前に終わったバックグラウンドタスクの
        再開turnを判別できない。CLIが報告をやめて判定の精度が下がったことを検出できるようにログへ残す。
        """
        if session.cli_turn_state is not None or session.cli_turn_state_fallback_logged:
            return
        session.cli_turn_state_fallback_logged = True
        _LOG.warning(
            "claude_turn_state_unreported session_id=%s: CLIのturn状態の報告が無いため、結果の保留を追跡集合だけで判定する",
            session.session_id,
        )

    @classmethod
    async def _finalize_pending_result(cls, session: SessionState) -> None:
        """保留した終了時の結果を確定する。

        Claude backendはタスク完了通知と孫sessionの終了によって同一sessionを再開できるため終了時の結果を保留する。
        Codex backendは孫sessionの終了による再開だけを持ち、同じ保留をMCP層の監視が確定する。
        確定の時点で残るバックグラウンドタスクと孫sessionの`error`への記録は、確定の契機によらず共通処理が行う。
        """
        await wait_output_tracking.consume_agents_wait_background_outputs_async(session)
        resume_waits.finalize_pending_result(session)

    @classmethod
    def _record_failure(
        cls,
        session: SessionState,
        error: BaseException,
        *,
        diagnostic: _InitializationDiagnostic | None = None,
    ) -> None:
        """失敗を終了時の結果へ確定し、初期化の完了後に生じた失敗へは原因の特定に要する診断を添える。"""
        failure: dict[str, Any] = {"message": str(error) or error.__class__.__name__}
        if diagnostic is not None:
            stderr = diagnostic.stderr.strip()
            failure.update(
                engine=session.engine or "claude",
                model=session.model,
                stderr=stderr,
                childPid=diagnostic.child_pid,
                debugFile=diagnostic.debug_file,
            )
            _LOG.error(
                "claude_session_failure session_id=%s engine=%s model=%s child_pid=%s debug_file=%s stderr=%s",
                session.session_id,
                session.engine or "claude",
                session.model,
                diagnostic.child_pid,
                diagnostic.debug_file,
                stderr,
            )
        cls._finalize_turn(
            session,
            {"status": "failed", "agent_message": session.agent_message, "error": failure},
        )

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await lifetime.wait_for_shutdown(tasks)
        # 切断taskは所有タスクの終了処理が登録するため、待機の対象は所有タスクの終了後に集める。
        disconnects = tuple(self._disconnects)
        if disconnects:
            await lifetime.wait_for_shutdown(disconnects, timeout=_DISCONNECT_TIMEOUT)
        for session_id in tuple(self._residual_processes):
            await self._reclaim_runtime(session_id)
        for session in tuple(self.sessions.values()):
            if session.engine == "claude":
                await session.drain_registration()
