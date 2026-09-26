"""CodexとClaudeの委譲先を非同期MCPとして公開する。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import inspect
import json
import logging
import os
import pathlib
import re
import subprocess
import typing
import warnings
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Annotated, Any, cast
from uuid import UUID

from anyio.abc import ObjectReceiveStream, ObjectSendStream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import Icon, ToolAnnotations
from pydantic import Field

from agent_toolkit._agents_server import antigravity as antigravity_backend
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import logging_config, session_registry, state, status_file
from agent_toolkit._agents_server.state import (
    TERMINAL_STATUSES,
    LaunchKind,
    ModelCandidate,
    ResumePrompt,
    SessionInitializationTimeoutError,
    SessionOwnerGoneError,
    SessionResumeState,
    SessionState,
    _validate_cwd,
    _validate_model_effort,
    _validate_prompt,
    _validate_shell_request,
    add_terminal_listener,
    add_touch_listener,
    finalize_pending_result,
    has_pending_auto_resume_targets,
    has_uncollected_result,
    record_unobserved_sessions,
    remove_terminal_listener,
    remove_touch_listener,
    selected_candidate,
)
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import codex_models
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._common.markdown_headings import top_level_atx_headings
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message

try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
except ImportError:  # pragma: no cover - mcpの依存版が警告型を公開しない場合
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")
DEFAULT_KILL_TIMEOUT = 270.0
DEFAULT_SEND_MESSAGE_TIMEOUT = 270.0
SUPPORTED_ENGINES = frozenset({"claude", "codex", "agy"})
REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})
# 起動直後の可用性失敗を確定するために`start`が終端を待つ上限秒数。
# Codex CLI 0.152.0で利用上限に達した状態のturnは、backendの起動応答から3.84〜4.27秒後に
# `turn/completed`で失敗した（2026-09-02、`explore_fast`候補`gpt-5.6-terra/medium`で3回測定）。
# 再検証は同じ失敗状態で`AppServerManager.start`を呼び、応答から終端までの経過を測る。
# 可用性失敗は最初のモデル出力より前に生じるため、正常起動ではモデル出力の観測で上限を待たずに打ち切る。
START_AVAILABILITY_TIMEOUT = 15.0
# engineの可用性に起因し、別候補なら結果が変わり得る失敗の識別子。
# `codex app-server generate-json-schema`が出力する`CodexErrorInfo`列挙のうち、
# 利用枠超過、流量制限及びサーバー側過負荷に該当する区分へ限定する。
ENGINE_UNAVAILABLE_ERROR_INFO = frozenset({"usageLimitExceeded", "rateLimitExceeded", "serverOverloaded"})
# engineの可用性に起因し、別候補なら結果が変わり得るClaude APIのHTTPステータス。
# 429（rate_limit_error）と529（overloaded_error）は公式なエラーコード表が再試行可能とする。
# 401（authentication_error）と403（permission_error）は、認証情報の不備と権限の不足という
# 別々の原因を持つが、いずれも同じ候補での再試行では解消せず、別候補なら結果が変わり得る点で
# 前2者と一致するため同じ集合の要素とする。
# 500（api_error）はサービス内部の失敗であり、候補の変更で解決するとは限らないため含めない。
ENGINE_UNAVAILABLE_API_ERROR_STATUS = frozenset({401, 403, 429, 529})
_REQUIRED_INPUT_PREFIX = "必須入力名: "
_REQUIRED_INPUT_NAME_PATTERN = re.compile(r"^[^`\s:，、](?:[^`\s，、]*[^`\s:，、])?$")
_SHARE_DIRECTORY = pathlib.Path(__file__).resolve().parent.parent / "share"
_TASK_MODEL_TYPES = state.TASK_MODEL_TYPES
_TASK_DOCUMENT_SUFFIX = ".subagent.md"
# 委譲先のClaude Code・Codexがプラグインを起動するコマンド。MCP設定（`.mcp.json`・`.mcp.codex.json`・`mcp.json`）の
# `command`と`hooks/hooks.json`のhookの先頭語、及びCodexのhook起動器が内部で呼ぶ`uv run`を覆う。
# 委譲先は同じPATHと作業ディレクトリからこれらを解決するため、作業ディレクトリの設定（未trustのmise設定など）で
# 失敗する状態を子の起動前に検出する。起動後に失敗すると、Claude Codeはプラグインの接続失敗をホスト共通の記録へ残し、
# 同じ設定のサーバーへの接続を15分間試みない。
PREFLIGHT_COMMANDS: tuple[tuple[str, ...], ...] = (("uv", "--version"), ("uvx", "--version"))
# 事前確認の1コマンドあたりの上限秒数。成功時の実測は2コマンドの連続実行で約0.14秒である。
PREFLIGHT_TIMEOUT = 20.0
# start系の起動ツールが共通して受け取る引数の説明はサーバーの`instructions`へ1か所だけ置き、
# 各ツールの引数説明にはツール固有の既定値と、この参照文だけを書く。
_COMMON_ARGUMENT_REFERENCE = "意味と書式はサーバーの`instructions`の共通引数`{name}`の説明に従う。"

# 検査が受理する行の書式。拒否応答の本文へ添え、呼び出し元が同じ応答だけで書式を確定できる状態にする。
_REQUIRED_INPUT_LINE_FORMAT = (
    "受理する書式: 必須入力の行は`<項目名>:`で始める。"
    "項目名へ別の語を連結した行は当該項目として解決しないため、補足する語は別の行へ書く。"
)


# タスク文書の本文がプラグインルートを参照するときの変数名。
_PLUGIN_ROOT_VARIABLE = "${CLAUDE_PLUGIN_ROOT}"


def _is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginのshare直下にあるタスク文書だけを受理する。"""
    if path.parent.name != "share":
        return False
    try:
        manifest = json.loads((path.parent.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "agent-toolkit"


@dataclasses.dataclass
class _PendingResume:
    """timeout後も同じsessionから観測・共有する再開操作。"""

    state: SessionResumeState
    task: asyncio.Task[SessionState]
    prompt: ResumePrompt
    previous_result: dict[str, Any] | None = None

    def discard_previous_result(self) -> None:
        """配送又は明示的な破棄の後に退避済み結果本文を除く。"""
        self.previous_result = None

    def take_previous_result(self) -> dict[str, Any] | None:
        """退避済み結果を返し、進行中再開から本文を取り除く。"""
        result = self.previous_result
        self.discard_previous_result()
        return result


def _resolve_display_label(label: str | None, fallback: str) -> str:
    """呼び出し元が指定した識別名を正規化し、空になる指定では代替の本文から導く。

    未指定と、空白だけで構成された指定を含む空になる指定を同じ扱いとする。
    識別名の列が空のまま表示されると、当該sessionを名前で見分けられないためである。
    """
    normalized = status_file.normalize_label(label) if label else ""
    return normalized or status_file.normalize_label(fallback)


def _listed_public_session(session: dict[str, Any]) -> dict[str, Any]:
    """一覧の公開応答へ返す項目だけを取り出す。

    停滞の判定は`seconds_since_activity`と閾値の比較で呼び出し元が行うため、判定済みの印を返さない。
    """
    public = {"session_id": session["session_id"], "status": session["status"]}
    if "seconds_since_activity" in session:
        public["seconds_since_activity"] = session["seconds_since_activity"]
    return public


def _public_start_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """起動の応答のうち呼び出し元へ公開する項目を返す。

    候補を切り替えて成立した起動だけが、除外した候補と採用した候補を加える。
    切り替えが起きない起動は`session_id`と`status`を返す。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    """
    public: dict[str, Any] = {key: response[key] for key in ("session_id", "status")}
    if "root_session_id" in response:
        public["root_session_id"] = response["root_session_id"]
    if response.get("excluded_candidates"):
        public["excluded_candidates"] = response["excluded_candidates"]
        public["engine"] = response["engine"]
        public["model"] = response["model"]
        public["effort"] = response["effort"]
    return public


def _engine_unavailable_reason(session: SessionState) -> str | None:
    """Claude/Codexの可用性失敗とagyの失敗について、除外理由を返す。"""
    if session.status != "failed" or not isinstance(session.error, dict):
        return None
    if session.engine == "agy":
        return str(session.error.get("stderr") or session.error.get("message") or "Antigravity CLI failed")
    error_info = session.error.get("codexErrorInfo")
    if error_info in ENGINE_UNAVAILABLE_ERROR_INFO:
        return str(error_info)
    api_error_status = session.error.get("apiErrorStatus")
    if api_error_status in ENGINE_UNAVAILABLE_API_ERROR_STATUS:
        return str(api_error_status)
    return None


def _engine_unavailable(session: SessionState) -> bool:
    """終端したsessionが、engineの可用性を理由に失敗したかを返す。"""
    return _engine_unavailable_reason(session) is not None


def _excluded_candidate_payload(excluded: Mapping[ModelCandidate, str]) -> list[dict[str, Any]]:
    """除外した候補と除外の根拠を、呼び出し元が読む形へそろえる。"""
    return [
        {"engine": engine, "model": model, "effort": effort, "reason": reason}
        for (engine, model, effort), reason in sorted(excluded.items())
    ]


def _elapsed_seconds(started_at_value: str) -> int | None:
    """開始時刻から現在までの経過秒を返す。"""
    try:
        started_at = datetime.datetime.fromisoformat(started_at_value)
    except (TypeError, ValueError):
        return None
    return max(0, int(datetime.datetime.now(tz=started_at.tzinfo).timestamp() - started_at.timestamp()))


# 配送本文を組み立てた主体。呼び出し元のエージェントと本サーバーを区別する。
COMPOSED_BY_CALLER = "caller"
COMPOSED_BY_AGENTS_SERVER = "agents-server"

# MCPのスキーマとして実行ホストのsystem promptへ入る本文の境界。
_NORMATIVE_ELEMENT = AUTO_INSERTED_ELEMENT
_NORMATIVE_SOURCE = "agent-toolkit/agents-server"
_KIND_MCP_INSTRUCTIONS = "mcp-instructions"
_KIND_MCP_TOOL = "mcp-tool"
_KIND_MCP_PARAMETER = "mcp-parameter"


def _schema_text(body: str, *, kind: str) -> str:
    """MCPのスキーマへ載る本文へ、生成主体と種別を示す境界を付ける。

    サーバーの説明文とツールの説明文は、呼び出し側のホストがsystem promptへ自動的に載せる。
    受信したエージェントが本リポジトリの生成物と判別できるよう、他の自動注入経路と同じ形式で囲む。
    """
    return auto_message(body, source=_NORMATIVE_SOURCE, kind=kind)


def _parameter_description(body: str) -> str:
    """ツールの引数の説明文へ境界を付ける。"""
    return _schema_text(body, kind=_KIND_MCP_PARAMETER)


def _shell_prompt(command: str, summary_policy: str) -> str:
    """コマンドと要約方針を、シェル実行委譲先への指示本文へ組み立てる。"""
    return f"次のコマンドを実行し、結果を報告せよ。\n\n実行するコマンド:\n{command}\n\n要約方針:\n{summary_policy}"


def _common_argument_description(name: str, tool_specific: str) -> str:
    """共通引数の説明を、ツール固有の既定値と`instructions`への参照から組み立てる。"""
    return _parameter_description(f"{tool_specific}{_COMMON_ARGUMENT_REFERENCE.format(name=name)}")


def _label_description(tool_specific: str) -> str:
    """label引数の説明を、ツール固有の形式・既定値と`instructions`への参照から組み立てる。"""
    return _common_argument_description("label", tool_specific)


def _model_type_description(tool_specific: str) -> str:
    """model_type引数の説明を、ツール固有の既定値と`instructions`への参照から組み立てる。"""
    return _common_argument_description("model_type", tool_specific)


def _task_document_label(subagent_md_path: str, extra_params: Mapping[str, str]) -> str:
    """`start`のlabel省略時の識別名をタスク文書名とレーン識別子から組み立てる。"""
    name = pathlib.PurePath(subagent_md_path).name.removesuffix(_TASK_DOCUMENT_SUFFIX)
    lane = extra_params.get("レーン識別子", "").strip()
    return f"{lane}-{name}" if lane else name


def _shell_default_label(command: str) -> str:
    """`start_shell`のlabel省略時の識別名を、コマンドの最初の語のbasenameから組み立てる。"""
    words = command.split()
    return f"shell-{pathlib.PurePath(words[0]).name}" if words else "shell"


def _run_preflight_command(command: tuple[str, ...], cwd: str) -> str | None:
    """1件の事前確認コマンドを実行し、失敗時は失敗内容の説明を返す。"""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return f"command={' '.join(command)} error=実行ファイルが見つからない"
    except subprocess.TimeoutExpired:
        return f"command={' '.join(command)} error={PREFLIGHT_TIMEOUT:g}秒以内に終了しない"
    if completed.returncode != 0:
        return f"command={' '.join(command)} exit_code={completed.returncode} stderr={completed.stderr.strip()}"
    return None


def _check_plugin_commands_sync(cwd: str) -> None:
    """委譲先の作業ディレクトリでプラグインの起動コマンドが動くことを確かめる。"""
    for command in PREFLIGHT_COMMANDS:
        failure = _run_preflight_command(command, cwd)
        if failure is not None:
            raise ValueError(
                "委譲先の作業ディレクトリでプラグインの起動コマンドが失敗したため委譲先を起動しない: "
                f"cwd={cwd} {failure}。"
                "未trustのmise設定が原因の場合は、呼び出し元で`mise trust`の要否を判断してから再実行する。"
            )


async def _check_plugin_commands(cwd: str) -> None:
    """事前確認をイベントループの外で実行する。"""
    await asyncio.to_thread(_check_plugin_commands_sync, cwd)


def _delivery_sender_label() -> str:
    """配送本文の`from`属性が示す呼び出し元の種別とsession識別子を返す。"""
    identity = status_file.resolve_status_file_identity(os.environ)
    if identity is None:
        return "unresolved"
    if identity.host_session_id is None:
        return f"main:{identity.root_session_id}"
    return f"delegate:{identity.host_session_id}"


def _wrap_delivery_body(body: str, *, composed_by: str = COMPOSED_BY_CALLER) -> str:
    """委譲先へ配送する本文を、配送元と本文の作成主体を示す標識で囲む。

    `from`は配送を発行したsessionを示し、`composed-by`は本文を組み立てた主体を示す。
    タスク文書の読み込み指示、シェル実行の依頼文、自動再開の継続指示は、呼び出し元ではなく
    本サーバーが組み立てるため、受信側が両者を取り違えないよう作成主体を分けて示す。

    `from`が示すsession識別子はXML属性値へエスケープして置く。
    配送境界は最初の開始タグと最後の同名終了タグで確定する。
    受信側の解釈は`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」が定める。
    """
    sender = _delivery_sender_label()
    return auto_message(
        body,
        source="agent-toolkit/agents-server",
        kind="agent-delivery",
        attributes={"from": sender, "composed-by": composed_by},
    )


def _validate_required_prompt_inputs(
    task_document: pathlib.Path,
    extra_params: Mapping[str, str],
    document_text: str | None = None,
) -> str | None:
    """タスク文書の必須入力名を名前付き追加入力と照合する。"""
    if not _is_agent_toolkit_task_document(task_document):
        return f"必須入力検査を実施できません: タスク文書がshare配下ではありません: {task_document}"
    if document_text is None:
        try:
            document_text = task_document.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return f"必須入力検査を実施できません: タスク文書をUTF-8で読めません: {task_document}: {error}"
    document_lines = document_text.splitlines()
    headings = top_level_atx_headings("\n".join(document_lines), 2)
    inputs = [index for index, (_, title) in enumerate(headings) if title == "入力"]
    if not inputs:
        return f"必須入力検査を実施できません: タスク文書に## 入力がありません: {task_document}"
    position = inputs[0]
    token = headings[position][0]
    assert token.map is not None
    following = headings[position + 1][0] if position + 1 < len(headings) else None
    end = following.map[0] if following is not None and following.map is not None else len(document_lines)
    section = document_lines[token.map[1] : end]
    try:
        fence = section.index("```text")
        marker = section[fence + 1]
    except (ValueError, IndexError):
        return f"必須入力検査を実施できません: ## 入力にtextコードブロックがありません: {task_document}"
    if not marker.startswith(_REQUIRED_INPUT_PREFIX):
        return f"必須入力検査を実施できません: 必須入力名を取得できません: {task_document}"
    required_names = marker.removeprefix(_REQUIRED_INPUT_PREFIX).split(",")
    if not required_names or any(not _REQUIRED_INPUT_NAME_PATTERN.fullmatch(name) for name in required_names):
        return f"必須入力検査を実施できません: 必須入力名の書式が不正です: {task_document}"
    missing = [name for name in required_names if name not in extra_params]
    if missing:
        raise ValueError(
            f"必須入力が欠けています: {', '.join(missing)}; タスク文書: {task_document}; {_REQUIRED_INPUT_LINE_FORMAT}"
        )
    return None


def _task_document_request(
    subagent_md_path: str,
    extra_params: Mapping[str, str],
) -> tuple[str, str]:
    """専用タスク文書と名前付き追加入力からmodel種別と起動文を返す。"""
    task_document = pathlib.Path(subagent_md_path)
    if not task_document.is_absolute():
        raise ValueError("subagent_md_path must be an absolute path")
    task_document = task_document.resolve()
    if not task_document.is_file() or not task_document.name.endswith(".subagent.md"):
        raise ValueError(f"subagent_md_path is not an existing .subagent.md file: {task_document}")
    if not _is_agent_toolkit_task_document(task_document):
        raise ValueError(f"subagent_md_path is not an agent-toolkit task document: {task_document}")
    model_type = _TASK_MODEL_TYPES.get(task_document.name)
    if model_type is None:
        raise ValueError(f"subagent task has no model_type mapping: {task_document.name}")
    if any(not isinstance(name, str) or not _REQUIRED_INPUT_NAME_PATTERN.fullmatch(name) for name in extra_params):
        raise ValueError("extra_params contains an invalid input name")
    if any(not isinstance(value, str) for value in extra_params.values()):
        raise ValueError("extra_params values must be strings")
    try:
        document_text = task_document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"タスク文書をUTF-8で読めません: {task_document}: {error}") from error
    # 委譲先は配送された本文だけを読むため、プラグインルートの変数を配送側で解決する。
    # 未展開のまま渡すと、委譲先は変数の値を推測して参照先を探す。
    document_text = document_text.replace(_PLUGIN_ROOT_VARIABLE, str(task_document.parent.parent))
    prompt_lines = [f"次のタスク文書の手順を実行せよ（出所: {task_document}）。", document_text, "追加指示:"]
    prompt_lines.extend(f"{name}: {value}" for name, value in extra_params.items())
    prompt = "\n".join(prompt_lines)
    warning = _validate_required_prompt_inputs(task_document, extra_params, document_text)
    if warning is not None:
        _LOG.warning("%s", warning)
    return model_type, prompt


_DEFAULT_STATUS_WRITER = object()


class AgentsServerManager:
    """エンジン別バックエンドと共有session状態を管理する。"""

    def __init__(
        self,
        status_writer: status_file.StatusFileWriter | None | object = _DEFAULT_STATUS_WRITER,
    ) -> None:
        self.sessions: dict[str, SessionState] = (
            status_writer.sessions if isinstance(status_writer, status_file.StatusFileWriter) else {}
        )
        self.expired_sessions: dict[str, SessionResumeState] = {}
        self.stopped_sessions: dict[str, SessionResumeState] = {}
        self._pending_resumes: dict[str, _PendingResume] = {}
        self._condition = asyncio.Condition()
        self._resume_lock = asyncio.Lock()
        self._codex: Any = None
        self._claude: Any = None
        self._agy: Any = None
        self._wait_timeouts: dict[str, float] = {}
        self._pending_unobserved_child_sessions: dict[str, tuple[int, set[str]]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._auto_resume_task: asyncio.Task[None] | None = None
        self._status_writer: status_file.StatusFileWriter | None
        if status_writer is _DEFAULT_STATUS_WRITER:
            identity = status_file.resolve_status_file_identity(os.environ)
            if identity is None:
                identity = status_file.create_process_root_identity()
            self._status_writer = status_file.StatusFileWriter(self.sessions, identity)
        else:
            assert status_writer is None or isinstance(status_writer, status_file.StatusFileWriter)
            self._status_writer = status_writer
        if self._status_writer is not None:
            add_touch_listener(self._status_writer.schedule)
        add_terminal_listener(self._carry_over_unavailable_candidate)
        add_terminal_listener(self._record_pending_unobserved_child_sessions)

    def activate(self) -> None:
        """状態ファイル出力を有効化する。"""
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())
        if self._status_writer is not None:
            self._status_writer.activate()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())

    async def _refresh_heartbeat(self) -> None:
        """MCPサーバーの生存中に状態ファイルの生存の印を更新する。"""
        assert self._status_writer is not None
        while True:
            await asyncio.sleep(status_file.HEARTBEAT_INTERVAL_SECONDS)
            self._status_writer.flush()

    async def _monitor_auto_resume(self) -> None:
        """公開待機操作に依存せず、孫session終端後の自動再開を進める。"""
        while True:
            awaiting = [session for session in self.sessions.values() if session.awaiting_auto_resume]
            for session in awaiting:
                await self._advance_child_session_wait(session)
            await asyncio.sleep(0.1 if awaiting else 1.0)

    def _backend(self, engine: str) -> Any:
        if engine == "codex":
            if self._codex is None:
                self._codex = codex_backend.AppServerManager(
                    self.sessions,
                    self._condition,
                    publish_registry=True,
                )
            return self._codex
        if engine == "claude":
            if self._claude is None:
                self._claude = claude_backend.ClaudeServerManager(
                    self.sessions,
                    self._condition,
                    expire_session=self._expire_session,
                    publish_registry=True,
                )
            return self._claude
        if engine == "agy":
            if self._agy is None:
                self._agy = antigravity_backend.AntigravityManager(
                    self.sessions,
                    self._condition,
                    publish_registry=True,
                    log_directory=self._status_writer.path.parent / "logs" if self._status_writer is not None else None,
                )
            return self._agy
        raise ValueError(f"unsupported engine: {engine}")

    def _get_session(self, session_id: str) -> SessionState:
        """保持中のsessionを返し、未解決値は識別子体系と喪失に分けて診断する。"""
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        try:
            session = self.sessions[session_id]
        except KeyError as exc:
            if session_id in self.expired_sessions:
                raise ValueError(f"session retention expired: {session_id}") from exc
            raise self._unresolved_session_error(session_id, label="session") from exc
        if session.retention_deadline is not None and asyncio.get_running_loop().time() >= session.retention_deadline:
            self._expire_session(session_id)
            raise ValueError(f"session retention expired: {session_id}")
        return session

    def _expire_session(self, session_id: str) -> None:
        """期限に到達したsession本体を解放し、再開状態と未回収結果を保持する。"""
        self._pending_unobserved_child_sessions.pop(session_id, None)
        session = self.sessions.pop(session_id, None)
        if session is not None:
            if session.publish_registry:
                session_registry.remove(session_id)
            resume_state = SessionResumeState.from_session(session)
            self.expired_sessions[session_id] = resume_state
            if self._status_writer is not None:
                self._status_writer.retain_result(resume_state)
                self._status_writer.schedule()

    def _resolve_expired_session(self, session_id: str) -> SessionResumeState | None:
        """保持期限を反映し、期限切れsessionの再開状態を返す。"""
        if not isinstance(session_id, str) or not session_id:
            return None
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
        return self.expired_sessions.get(session_id)

    def _expired_result_response(self, session_id: str, *, collector: str) -> dict[str, Any] | None:
        """期限切れsessionの未回収結果を1回だけ返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        if self._status_writer is not None and self._status_writer.result_state(session_id) == "consumed":
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.expired_sessions[session_id] = resume_state
        response = self._stopped_result_response(resume_state)
        if response is None:
            return {"status": "expired"}
        self.expired_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id, collector=collector)
            self._status_writer.schedule()
        return response

    def _resolve_stopped_session(self, session_id: str) -> SessionResumeState | None:
        """破棄済みsessionを返し、外部経路による結果回収を反映する。"""
        resume_state = self.stopped_sessions.get(session_id)
        if resume_state is None:
            return None
        if (
            not resume_state.result_delivered
            and resume_state.finalized_at is not None
            and self._status_writer is not None
            and self._status_writer.result_state(session_id) == "consumed"
        ):
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.stopped_sessions[session_id] = resume_state
        return resume_state

    def _restore_registry_session(
        self,
        session_id: str,
        *,
        resolution: session_registry.SessionResolution | None = None,
    ) -> SessionResumeState | None:
        """再起動前の終端sessionを登録簿から最小の再開状態へ復元する。

        `resolution`は、呼び出し元が同じ識別子を既に解決している場合に渡す。
        登録簿のファイル読取を1回の照会へ収めるための入力であり、省略時は自ら解決する。
        """
        if session_id in self.sessions or session_id in self.stopped_sessions or session_id in self.expired_sessions:
            return None
        if resolution is None:
            resolution = session_registry.resolve(session_id)
        if resolution.state is session_registry.Resolution.RUNNING:
            raise ValueError(f"error.recovery=turn_unobserved: {session_id}")
        if resolution.state is session_registry.Resolution.MISSING:
            return None
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ValueError(f"error.recovery=unreadable: {session_id}")
        info = resolution.resume_info
        if info is None:
            raise ValueError(f"error.recovery=no_resume_info: {session_id}")
        persisted_result = self._status_writer.read_result(session_id) if self._status_writer is not None else None
        resume_state = SessionResumeState(
            session_id=session_id,
            cwd=info.cwd,
            model=info.model,
            effort=info.effort,
            engine=info.engine,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            created_at=info.created_at,
            turn_seq=persisted_result["turn_seq"] if persisted_result is not None else info.turn_seq,
            status=persisted_result["status"] if persisted_result is not None else info.status,
            agent_message=persisted_result["agent_message"] if persisted_result is not None else "",
            error=persisted_result.get("error") if persisted_result is not None else None,
            finalized_at=persisted_result["finalized_at"] if persisted_result is not None else None,
            result_delivered=persisted_result is None,
        )
        self.stopped_sessions[session_id] = resume_state
        return resume_state

    @staticmethod
    def _stopped_result_response(resume_state: SessionResumeState) -> dict[str, Any] | None:
        """破棄済みsessionの未回収結果を返す。"""
        if resume_state.result_delivered or resume_state.status not in TERMINAL_STATUSES or resume_state.finalized_at is None:
            return None
        response: dict[str, Any] = {
            "status": resume_state.status,
            "agent_message": resume_state.agent_message,
        }
        if resume_state.error is not None and resume_state.error != "" and resume_state.error != {}:
            response["error"] = resume_state.error
        return response

    def _take_stopped_result(
        self,
        session_id: str,
        resume_state: SessionResumeState,
        *,
        collector: str,
    ) -> dict[str, Any] | None:
        """破棄済みsessionの未回収結果を返し、配送済みとして記録する。"""
        response = self._stopped_result_response(resume_state)
        if response is None:
            return None
        self.stopped_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id, collector=collector)
            self._status_writer.schedule()
        return response

    @staticmethod
    def _recovered_result_response(resume_state: SessionResumeState) -> dict[str, Any]:
        """再起動前の終端種別と結果本文を回収できない事実を返す。"""
        return {"status": resume_state.status, "recovery": "result_unavailable"}

    def _expired_kill_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionなら中断対象が無いことを示す成功応答を返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        return {"status": "expired", "kill_requested": False}

    @staticmethod
    def _listed_session(
        session: SessionState | SessionResumeState,
        *,
        status: str,
        progress: str,
        result_available: bool,
    ) -> dict[str, Any]:
        """sessionを一覧向けの公開項目へ射影する。

        最終活動時刻からの経過秒数を返し、停滞かどうかの判定は呼び出し元へ委ねる。
        判定済みの印は同じ応答の値と閾値から再現できるため、公開項目へ加えない。
        """
        label = session.label
        if len(label) > 100:
            label = f"{label[:100]}…"
        listed: dict[str, Any] = {
            "session_id": session.session_id,
            "status": status,
            "progress": progress,
            "model_type": session.model_type,
            "launch_kind": session.launch_kind,
            "label": label,
            "result_available": result_available,
        }
        listed.update(
            state.activity_projection(
                updated_at=session.updated_at,
                output_updated_at=session.output_updated_at,
                started_at=session.started_at,
            )
        )
        return listed

    def list_sessions(self, *, include_terminated: bool = False) -> dict[str, Any]:
        """保持中のsessionを開始時刻順の公開項目へ射影する。

        未回収結果を持つsessionは、終端済み又は期限切れでも既定の一覧へ残す。
        """
        loop_time = asyncio.get_running_loop().time()
        for session_id, session in tuple(self.sessions.items()):
            if session.retention_deadline is not None and loop_time >= session.retention_deadline:
                self._expire_session(session_id)

        listed: dict[str, tuple[str, dict[str, Any]]] = {}
        for session in self.sessions.values():
            listed[session.session_id] = (
                session.started_at,
                self._listed_session(
                    session,
                    status=session.status,
                    progress=session.progress,
                    result_available=has_uncollected_result(
                        session,
                        None
                        if self._status_writer is None
                        else self._status_writer.result_state(session.session_id) == "consumed",
                    ),
                ),
            )
        for pending in self._pending_resumes.values():
            session = pending.state
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(session, status="running", progress="", result_available=False),
                ),
            )
        for session in self.expired_sessions.values():
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(
                        session,
                        status="expired",
                        progress="",
                        result_available=has_uncollected_result(
                            session,
                            None
                            if self._status_writer is None
                            else self._status_writer.result_state(session.session_id) == "consumed",
                        ),
                    ),
                ),
            )
        sessions = [entry for _, entry in sorted(listed.values(), key=lambda item: item[0])]
        if include_terminated:
            response: dict[str, Any] = {
                "sessions": [_listed_public_session(session) for session in sessions],
                "omitted": 0,
            }
        else:
            visible = [
                session
                for session in sessions
                if session["result_available"] or session["status"] not in TERMINAL_STATUSES | {"expired"}
            ]
            response = {
                "sessions": [_listed_public_session(session) for session in visible],
                "omitted": len(sessions) - len(visible),
            }
        if self._status_writer is not None:
            response["root_session_id"] = self._status_writer.root_session_id
        return response

    def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
        """保持中又は再開可能なsessionの復旧用詳細を返す。

        自プロセスの保持状態に無い識別子は、共有の登録簿を正本として在否を判定する。
        当該sessionを別のMCPサーバープロセスが実行中である場合と、登録簿にレコードが無い場合を
        区別せずに喪失として案内すると、照会した主体が新しいsessionの起動へ進む。
        """
        session: SessionState | SessionResumeState | None = self.sessions.get(session_id)
        if session is None and session_id in self._pending_resumes:
            session = self._pending_resumes[session_id].state
        if session is None:
            session = self.expired_sessions.get(session_id) or self.stopped_sessions.get(session_id)
        if session is None:
            resolution = session_registry.resolve(session_id)
            if resolution.state is session_registry.Resolution.RUNNING:
                raise ValueError(
                    f"session {session_id} belongs to another writer's agents_server process and is still running; "
                    "receive its result with `atk agents wait` on the owning root session"
                )
            session = self._restore_registry_session(session_id, resolution=resolution)
        if session is None:
            raise self._unresolved_session_error(session_id, label="session")
        status = "running" if session_id in self._pending_resumes else session.status
        result_available = bool(
            isinstance(session, SessionState) and session.result_available and not session.result_delivered
        ) or bool(
            isinstance(session, SessionResumeState) and session.status in TERMINAL_STATUSES and not session.result_delivered
        )
        response: dict[str, Any] = {
            "session_id": session.session_id,
            "status": status,
            "launch_kind": session.launch_kind,
            "model_type": session.model_type,
            "label": session.label,
            "prompt": session.prompt,
            "cwd": session.cwd,
            "result_available": result_available,
        }
        activity = state.activity_projection(
            updated_at=session.updated_at,
            output_updated_at=session.output_updated_at,
            started_at=session.started_at,
        )
        response.update(activity)
        if status == "running" and isinstance(session, SessionState):
            active_tool_uses = session.active_tool_uses()
            if active_tool_uses:
                response["active_tool_uses"] = active_tool_uses
        if status == "running" and isinstance(session, SessionState) and session.live_child_session_ids:
            # 呼び出し元が当該識別子へ`send_message`と`kill`を発行できるよう、許可判定の入力となる`cwd`を併記する。
            # `cwd`を解決できない識別子は、対を持たない側の項目として区別できる形で返す。
            resolved: list[dict[str, str]] = []
            unresolved: list[str] = []
            for child_session_id in sorted(session.live_child_session_ids):
                try:
                    resume_info = session_registry.resolve(child_session_id).resume_info
                except Exception:
                    unresolved.append(child_session_id)
                    continue
                child_cwd = resume_info.cwd if resume_info is not None else ""
                if child_cwd:
                    resolved.append({"session_id": child_session_id, "cwd": child_cwd})
                else:
                    unresolved.append(child_session_id)
            response["live_child_sessions"] = resolved
            if unresolved:
                response["live_child_session_ids_without_cwd"] = unresolved
        if verbose:
            response.update(
                engine=session.engine,
                model=session.model,
                effort=session.effort,
                started_at=session.started_at,
                turn_seq=session.turn_seq,
            )
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
        return response

    async def stop(self, session_id: str, *, retain_result: bool = False) -> dict[str, Any]:
        """終端済みsessionを破棄し、会話再開用の最小状態だけを保持する。

        `stopped_sessions`への在籍は、このプロセスが解放すべきbackend資源を
        持たないことを表す。解放後の同期失敗では再発行が同期だけを再試行する。
        """
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if session_id in self._pending_resumes:
            raise ValueError(f"session is running: {session_id}; issue kill before stop if interruption is required")
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is None:
            stopped_state = self._restore_registry_session(session_id)
        already_stopped = session_id in self.stopped_sessions
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
            session = None
        if session is not None:
            if not session.terminal:
                raise ValueError(f"session is running: {session_id}; issue kill before stop if interruption is required")
            resume_state = SessionResumeState.from_session(session)
        else:
            resume_state = stopped_state or self.expired_sessions.get(session_id)
            if resume_state is None:
                raise self._unresolved_session_error(session_id, label="session")
        result_state = None if self._status_writer is None else self._status_writer.result_state(session_id)
        keep_result = retain_result and resume_state.finalized_at is not None and result_state != "consumed"
        resume_state = dataclasses.replace(resume_state, result_delivered=not keep_result)
        if session is not None:
            session.result_delivered = not keep_result
        if not already_stopped:
            await self._backend(resume_state.engine).release_session(session_id)
        self._pending_unobserved_child_sessions.pop(session_id, None)
        self.sessions.pop(session_id, None)
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions[session_id] = resume_state
        session_registry.remove(session_id)
        if self._status_writer is not None:
            try:
                if not keep_result:
                    self._status_writer.delete_wait_target(session_id)
                if keep_result and result_state == "unpublished" and not self._status_writer.result_exists(session_id):
                    self._status_writer.retain_result(resume_state)
                elif not keep_result and (result_state == "published" or self._status_writer.result_exists(session_id)):
                    self._status_writer.delete_result(session_id, collector="stop")
                self._status_writer.schedule()
            except Exception as exc:
                raise RuntimeError(
                    f"backend resources released; state synchronization incomplete for session {session_id}: {exc}"
                ) from exc
        return {}

    async def _stop_after_terminal_response(
        self,
        session_id: str,
        response: dict[str, Any],
        stop: bool,
    ) -> dict[str, Any]:
        """要求された終端応答に限りsessionを破棄し、元の応答を維持する。"""
        if stop and response.get("status") in {*TERMINAL_STATUSES, "expired"} and session_id not in self.stopped_sessions:
            await self.stop(session_id, retain_result=True)
        return response

    @staticmethod
    def _unresolved_session_error(session_id: str, *, label: str) -> ValueError:
        """未解決の識別子を体系相違または失われたsessionとして診断する。

        保持状態の照会後だけ呼び、登録済みの非UUID識別子は拒否しない。
        体系相違の本文には、継続不能の判定に使う`unknown session`を含めない。
        """
        try:
            parsed = UUID(session_id)
        except ValueError:
            parsed = None
        if parsed is None or str(parsed) != session_id.lower():
            return ValueError(f"{label} identifier scheme mismatch: {session_id}; expected UUID")
        return ValueError(
            f"unknown {label}: {session_id}; agents_server may have restarted and lost this session; "
            "start a new session with the verified state"
        )

    async def _resolve_start_candidates(
        self,
        model_type: str,
        *,
        launch_kind: LaunchKind,
    ) -> tuple[list[ModelCandidate], dict[ModelCandidate, str]]:
        """起動条件を検証し、除外後の候補列を設定順で返す。

        除外中の候補は状態ディレクトリの記録を正本として読む。
        除外後に候補が残らない場合は、記録を無視して全候補を設定順で返す。
        """
        candidates = _atk_config.parse_unresolved_model_candidates(model_type)
        if codex_models.needs_catalog(candidates):
            catalog = await self._backend("codex").list_models()
            candidates = codex_models.resolve_candidates(candidates, catalog)
        if not candidates:
            raise ValueError(f"no model candidates remain for model_type: {model_type}")
        recorded = status_file.load_unavailable_candidates(
            model_type,
            launch_kind,
            now=datetime.datetime.now(datetime.UTC),
        )
        excluded = {candidate: recorded[candidate] for candidate in candidates if candidate in recorded}
        remaining = [item for item in candidates if item not in excluded]
        if not remaining:
            return candidates, {}
        return remaining, excluded

    def _carry_over_unavailable_candidate(self, session: SessionState) -> None:
        """可用性を理由に終端した候補を、同じ起動条件の次回へ引き継ぐ。

        終端結果が確定した時点の通知として`SessionState.touch`から呼ぶ。
        結果本文の受領、`stop`による破棄、保持期限切れのいずれを経ても記録が漏れないよう、
        記録の契機を終端の確定点だけに置く。共有の通知先は全managerへ届くため、
        自身が保持するsessionだけを記録の対象とする。
        """
        if self.sessions.get(session.session_id) is not session:
            return
        candidate = selected_candidate(session)
        reason = _engine_unavailable_reason(session)
        if reason is None or candidate is None or session.model_type is None:
            return
        status_file.record_unavailable_candidate(
            session.model_type,
            session.launch_kind,
            candidate,
            reason,
            now=datetime.datetime.now(datetime.UTC),
        )

    def _record_pending_unobserved_child_sessions(self, session: SessionState) -> None:
        """自動再開をまたいで保持した未観測の孫sessionを終端結果へ併合する。"""
        if self.sessions.get(session.session_id) is not session:
            return
        pending = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending is None or pending[0] != session.turn_seq:
            return
        _, unobserved = self._pending_unobserved_child_sessions.pop(session.session_id)
        if unobserved:
            record_unobserved_sessions(session, unobserved)

    async def start(
        self,
        model_type: str,
        prompt: str,
        cwd: str,
        *,
        launch_kind: LaunchKind = "delegate",
        label: str | None = None,
        composed_by: str = COMPOSED_BY_CALLER,
    ) -> dict[str, Any]:
        """工程別モデル設定の候補を先頭から試し、起動できたturnを返す。

        起動直後にengineの可用性を理由として終端した候補とagyの失敗候補を
        除外集合へ加えて次候補へ進む。agy以外のbackend起動例外では候補を進めない。
        Claude/Codexで候補を変えても結果が変わらない失敗は、そのまま呼び出し元へ返す。
        候補を除外して後続の候補で成立した場合は、除外した候補と除外の根拠を応答へ加える。
        """
        _validate_prompt(prompt)
        _validate_cwd(cwd)
        await _check_plugin_commands(cwd)
        candidates, excluded = await self._resolve_start_candidates(
            model_type,
            launch_kind=launch_kind,
        )
        unavailable_response: dict[str, Any] | None = None
        unavailable_session: SessionState | None = None
        display_label = _resolve_display_label(label, prompt)
        delivery_body = _wrap_delivery_body(prompt, composed_by=composed_by)
        for candidate_index, candidate in enumerate(candidates):
            engine, model, effort = candidate
            if engine not in SUPPORTED_ENGINES:
                raise ValueError(f"unsupported engine: {engine}")
            _validate_model_effort(model, effort)
            try:
                session = await self._start_until_initialized(
                    engine,
                    delivery_body,
                    cwd,
                    model,
                    effort,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=frozenset(excluded),
                )
            except Exception as exc:
                if engine != "agy":
                    raise
                reason = str(exc) or type(exc).__name__
                excluded[candidate] = reason
                unavailable_response = None
                unavailable_session = None
                _LOG.warning("agy_start_failed model_type=%s model=%s reason=%s", model_type, model, reason)
                continue
            session.engine = engine
            if session.status == "starting":
                session.status = "running"
                session.touch()
                if self._status_writer is not None:
                    self._status_writer.flush()
            await self._await_start_outcome(session)
            response: dict[str, Any] = {
                "session_id": session.session_id,
                "status": session.status,
                "engine": engine,
                "model_type": model_type,
                "model": model,
                "effort": effort,
            }
            if excluded:
                response["excluded_candidates"] = _excluded_candidate_payload(excluded)
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
            unavailable_reason = _engine_unavailable_reason(session)
            if unavailable_reason is None:
                status_file.clear_unavailable_candidate(
                    model_type,
                    launch_kind,
                    candidate,
                    now=datetime.datetime.now(datetime.UTC),
                )
                if excluded:
                    _LOG.info(
                        "engine_switch session_id=%s model_type=%s launch_kind=%s excluded=%s selected=%s",
                        session.session_id,
                        model_type,
                        launch_kind,
                        json.dumps(_excluded_candidate_payload(excluded), ensure_ascii=False),
                        json.dumps({"engine": engine, "model": model, "effort": effort}, ensure_ascii=False),
                    )
                session.label = display_label
                session.prompt = prompt
                session.announced = True
                session.touch()
                if self._status_writer is not None:
                    self._status_writer.flush()
                _LOG.info(
                    "session_transition event=start session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                    session.session_id,
                    session.status,
                    session.turn_seq,
                )
                return response
            unavailable_response, unavailable_session = response, session
            excluded[candidate] = unavailable_reason
            if candidate_index + 1 < len(candidates):
                await self._abandon_unavailable_session(session)
        if unavailable_response is not None:
            assert unavailable_session is not None
            unavailable_response["excluded_candidates"] = _excluded_candidate_payload(excluded)
            unavailable_session.label = display_label
            unavailable_session.prompt = prompt
            unavailable_session.announced = True
            unavailable_session.touch()
            if self._status_writer is not None:
                self._status_writer.flush()
            _LOG.info(
                "session_transition event=start session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                unavailable_session.session_id,
                unavailable_session.status,
                unavailable_session.turn_seq,
            )
            return unavailable_response
        raise RuntimeError(f"no available model candidates: {model_type}; excluded={_excluded_candidate_payload(excluded)}")

    async def _start_until_initialized(
        self,
        engine: str,
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        model_type: str,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
    ) -> SessionState:
        """初期化の上限超過だけを同じ候補で再試行し、全試行の超過を例外で確定する。

        上限超過はbackendが当該sessionの資源を解放してから返るため、再試行は新しい起動として成立する。
        上限超過が続けば例外を上位へ返し、engineごとの候補切替条件を適用する。
        """
        last_timeout: SessionInitializationTimeoutError | None = None
        timeout_diagnostics: list[str] = []
        for attempt in range(1, state.SESSION_INITIALIZATION_ATTEMPTS + 1):
            _LOG.info(
                "session初期化を開始します: engine=%s attempt=%d/%d model_type=%s launch_kind=%s",
                engine,
                attempt,
                state.SESSION_INITIALIZATION_ATTEMPTS,
                model_type,
                launch_kind,
            )
            try:
                session = await self._backend(engine).start(
                    prompt,
                    cwd,
                    model,
                    effort,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=excluded_candidates,
                )
                _LOG.info(
                    "session初期化が完了しました: engine=%s attempt=%d/%d session_id=%s",
                    engine,
                    attempt,
                    state.SESSION_INITIALIZATION_ATTEMPTS,
                    session.session_id,
                )
                return session
            except SessionInitializationTimeoutError as exc:
                last_timeout = exc
                timeout_diagnostics.append(f"attempt={attempt}: {exc}")
                _LOG.warning(
                    "session初期化timeout: engine=%s attempt=%d/%d exception_type=%s exception=%s",
                    engine,
                    attempt,
                    state.SESSION_INITIALIZATION_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                if attempt < state.SESSION_INITIALIZATION_ATTEMPTS:
                    _LOG.warning("session初期化が上限へ達したため同じ候補で再試行します: engine=%s, 試行=%d", engine, attempt)
        assert last_timeout is not None
        raise SessionInitializationTimeoutError(
            f"{engine} session initialization timed out on every attempt: "
            f"attempts={state.SESSION_INITIALIZATION_ATTEMPTS}, cwd={cwd}, "
            f"model_type={model_type}, launch_kind={launch_kind}, "
            f"diagnostics=[{'; '.join(timeout_diagnostics)}]"
        ) from last_timeout

    async def _abandon_unavailable_session(self, session: SessionState) -> None:
        """次候補へ進む前に可用性失敗sessionの全資源を解放する。"""
        await self._backend(session.engine).release_session(session.session_id)
        self.sessions.pop(session.session_id, None)
        if self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="start-unavailable")
            self._status_writer.schedule()

    async def _await_start_outcome(self, session: SessionState) -> None:
        """起動直後の可用性失敗を確定するため、上限付きで終端を待つ。

        engineの可用性失敗は最初のモデル出力より前に生じるため、モデル出力を観測した時点で待機を打ち切る。
        上限内に終端もモデル出力もしないsessionと、打ち切ったsessionは通常の実行中として扱い、
        以降は`atk agents wait`が観測する。
        """
        if session.result_available or session.model_output_observed:
            return
        with contextlib.suppress(TimeoutError):
            async with self._condition:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: session.result_available or session.model_output_observed),
                    timeout=START_AVAILABILITY_TIMEOUT,
                )

    async def start_explore(
        self,
        fast: bool,
        prompt: str,
        cwd: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """探索専用の軽量な起動条件でturnを開始する。`model_type`の指定時は`fast`を参照しない。"""
        if model_type is None:
            model_type = "explore_fast" if fast else "explore"
        return await self.start(
            model_type,
            prompt,
            cwd,
            launch_kind="explore",
            label=_resolve_display_label(label, "explore"),
        )

    async def start_shell(
        self,
        command: str,
        cwd: str,
        summary_policy: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """コマンド実行専用の軽量な起動条件でturnを開始する。"""
        _validate_shell_request(command, summary_policy)
        return await self.start(
            model_type or "explore_fast",
            _shell_prompt(command, summary_policy),
            cwd,
            launch_kind="shell",
            label=_resolve_display_label(label, _shell_default_label(command)),
            composed_by=COMPOSED_BY_AGENTS_SERVER,
        )

    async def start_write(
        self,
        prompt: str,
        cwd: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """対象、読者、事実と根拠が確定済みの文章起草・書込turnを開始する。"""
        return await self.start(
            model_type or "write",
            prompt,
            cwd,
            launch_kind="write",
            label=_resolve_display_label(label, "write"),
        )

    async def _resolve_wait_timeout(self, request_bucket: str) -> float:
        """bucket別の既定待機上限を導出し、同じbucketの以降の呼び出しへ再利用する。

        導出は`claude auth status`の実行を伴い得るため、イベントループ上で直接実行しない。
        """
        cached = self._wait_timeouts.get(request_bucket)
        if cached is not None:
            return cached
        resolved = await asyncio.to_thread(_wait_schedule.get_wait_timeout, request_bucket)
        self._wait_timeouts[request_bucket] = resolved
        return resolved

    def _wait_target_ids(self) -> list[str]:
        """待機の対象となる保持中sessionを識別子順に返す。

        未回収の終端結果を持つ破棄済み又は期限切れsessionも対象へ含め、
        呼び出し元が結果本文を回収できないまま失う経路を残さない。
        """
        targets = set(self.sessions) | set(self._pending_resumes)
        for session_id, resume_state in (*self.expired_sessions.items(), *self.stopped_sessions.items()):
            if self._stopped_result_response(resume_state) is not None:
                targets.add(session_id)
        return sorted(targets)

    def _retained_result_response(self, session_id: str, *, collector: str) -> dict[str, Any] | None:
        """破棄済み又は期限切れsessionの未回収の終端結果だけを返す。"""
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is not None:
            response = self._take_stopped_result(session_id, stopped_state, collector=collector)
            if response is None:
                return None
            return self._response_with_notices(response, self._take_notices(session_id))
        if self._resolve_expired_session(session_id) is None:
            return None
        response = self._expired_result_response(session_id, collector=collector)
        if response is None or "agent_message" not in response:
            return None
        return self._response_with_notices(response, self._take_notices(session_id))

    async def wait(self) -> dict[str, Any]:
        """委譲先の終端を待ち、終端時だけ結果本文を返す。

        引数を受け取らない。対象は当該MCPサーバープロセスが保持する起動中のsession全体とし、最初に終端した1件の結果を返す。
        残るsessionの終端結果は次の呼び出しまで保持する。
        待機上限はプロンプトキャッシュの保持期間から導出した値とし、委譲先として起動されたセッションでは240秒を上限とする。
        当該上限へ達した応答は`status`と`elapsed_seconds`を返す。
        保持中のsessionの最終活動時刻と停滞の印は`show`が返す。待機せずに現状態を確認する場合は`show`を発行する。
        以下の`/goal`の条件に該当しない場合は、本ツールを前景で発行する。
        呼び出し元のセッションに`/goal`が設定され、未完了の背景タスクが本ツールの背景移行だけになる場合は、
        公開MCP toolではなく、`atk agents wait`を実行ホストの前景又は背景ジョブとして起動する。
        委譲先が背景作業を残してturnを終えた場合は、同じsessionを一度だけ自動的に再開し、再開したturnの終端まで待つ。
        呼び出し元は背景作業の完了後に`send_message`で再開を指示しない。
        終端前に`status: running`が返った場合は、本ツールを再発行して待機を継続する。
        終端結果は呼び出し元が最初の呼び出しで受領するまで保持し、経過時間では解放しない。
        受領した終端結果のsessionを破棄する場合は`stop`を発行する。
        終端結果を残さずにsessionが失われた場合だけ、`status`が`expired`の応答を返す。
        委譲先が実行中に`atk agents-notify`で送った通知が未回収である場合は、終端前でも当該通知を`notices`へ載せて復帰する。
        再待機の要否は`notices`の有無ではなく`status`で判定する。
        `status`が`completed`、`failed`、`interrupted`のいずれかである応答は終端であり、`notices`を含む場合も結果本文とともに受領して本ツールを再発行しない。
        応答へ載せた通知は回収済みとして再び返さない。
        """
        timeout = await self._resolve_wait_timeout("main")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        ordered_ids = self._wait_target_ids()
        # 保留中の結果を進める判定は待機の刻みごとに1回だけ行う。
        # backendは背景作業の完了通知で受け取った再開turnの結果へ保留中の結果を差し替えるため、
        # 通知のたびに判定すると当該差し替えの前に保留中の結果を確定してしまう。
        advance_pending = True

        while True:
            for session_id in ordered_ids:
                retained_response = self._retained_result_response(session_id, collector="mcp-wait")
                if retained_response is not None:
                    return {"session_id": session_id, **retained_response}
                session = self.sessions.get(session_id)
                if session is not None and advance_pending:
                    await self._advance_child_session_wait(session)
            advance_pending = False
            async with self._condition:
                terminal: list[SessionState] = []
                for session_id in ordered_ids:
                    candidate = self.sessions.get(session_id)
                    if candidate is not None and candidate.result_available and not candidate.result_delivered:
                        if self._status_writer is not None and self._status_writer.result_state(session_id) == "consumed":
                            candidate.result_delivered = True
                            continue
                        terminal.append(candidate)
                terminal.sort(key=lambda candidate: (candidate.finalized_at or "", candidate.session_id))
                if terminal:
                    session = terminal[0]
                    if self._status_writer is not None and self._status_writer.result_state(session.session_id) == "published":
                        claimed, claim_error = self._status_writer.take_result(session.session_id, collector="mcp-wait")
                        if claim_error is not None:
                            raise RuntimeError(f"終端結果を回収できません: {session.session_id}: {claim_error}")
                        if claimed is None:
                            session.result_delivered = True
                            continue
                    _LOG.info("result_collected session_id=%s collector=mcp-wait", session.session_id)
                    response = self._response_with_notices(
                        self._result_response(session), self._take_notices(session.session_id)
                    )
                    return {"session_id": session.session_id, **response}

                for session_id in ordered_ids:
                    session = self.sessions.get(session_id)
                    if session is None:
                        continue
                    notices = self._take_notices(session_id)
                    if notices:
                        response = self._response_with_notices(self._result_response(session), notices)
                        return {"session_id": session_id, **response}

                retained = [self.sessions[session_id] for session_id in ordered_ids if session_id in self.sessions]
                pending_ids = [session_id for session_id in ordered_ids if session_id in self._pending_resumes]
                if not retained and not pending_ids:
                    if not ordered_ids:
                        return {"status": "expired"}
                    session_id = ordered_ids[0]
                    return {"session_id": session_id, "status": "expired"}

                remaining = deadline - loop.time()
                if remaining <= 0:
                    if not retained:
                        session_id = pending_ids[0]
                        response = self._pending_resume_status(self._pending_resumes[session_id])
                        return {"session_id": session_id, **response}
                    undelivered = next((candidate for candidate in retained if not candidate.result_delivered), None)
                    if undelivered is not None:
                        return {"session_id": undelivered.session_id, **self._result_response(undelivered)}
                    session = retained[0]
                    timeout_response: dict[str, Any] = {"status": "running", "progress": ""}
                    elapsed_seconds = _elapsed_seconds(session.started_at)
                    if elapsed_seconds is not None:
                        timeout_response["elapsed_seconds"] = elapsed_seconds
                    return {"session_id": session.session_id, **timeout_response}
                interval = 0.1 if any(candidate.awaiting_auto_resume for candidate in retained) else 1.0
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=min(interval, remaining))
                except TimeoutError:
                    advance_pending = True

    def _child_result_is_terminal(self, session_id: str) -> bool:
        """孫sessionの共有された終端結果ファイルが終端を示すかを返す。

        当該ファイルは同じルートsessionの`results`配下を全ての書込主体が共有するため、
        別プロセスが起動した孫sessionの終端も同じ経路で判定できる。
        """
        if self._status_writer is None or not status_file.valid_session_id(session_id):
            return False
        return self._status_writer.read_result(session_id) is not None

    async def _advance_child_session_wait(self, session: SessionState) -> None:
        """保留中の結果を、孫sessionの終端又は保持期限に応じて進める。"""
        if not session.awaiting_auto_resume or session.pending_result is None:
            return
        resolutions = {session_id: session_registry.resolve(session_id) for session_id in session.live_child_session_ids}
        terminal = {
            session_id
            for session_id, resolution in resolutions.items()
            if resolution.state is session_registry.Resolution.TERMINAL
        }
        unobserved: set[str] = set()
        for session_id, resolution in resolutions.items():
            if resolution.state not in {session_registry.Resolution.MISSING, session_registry.Resolution.UNREADABLE}:
                continue
            # レコードの不在は削除と保持期限の経過からも生じるため、終端結果ファイルを終端の第2の根拠とする。
            if self._child_result_is_terminal(session_id):
                terminal.add(session_id)
                continue
            unobserved.add(session_id)
        pending_unobserved = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending_unobserved is not None:
            unobserved.update(pending_unobserved[1])
        for session_id in terminal:
            session.live_child_session_ids.discard(session_id)
            session.terminal_child_session_ids.add(session_id)
        session.live_child_session_ids.difference_update(unobserved)

        if not has_pending_auto_resume_targets(session) and session.terminal_child_session_ids:
            identifiers = sorted(session.terminal_child_session_ids)
            prompt = _wrap_delivery_body(
                "あなたが`agents_server`で起動した次のsessionは終端した。\n"
                f"終端したsession: {', '.join(identifiers)}\n"
                "各sessionの結果を確認し、所定の返却形式を返せ。",
                composed_by=COMPOSED_BY_AGENTS_SERVER,
            )
            session.auto_resume_consumed = True
            pending_result = session.pending_result
            assert pending_result is not None
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = pending_result["error"]
            if unobserved:
                self._pending_unobserved_child_sessions[session.session_id] = (session.turn_seq + 1, unobserved)
            try:
                await self._backend(session.engine).send_message(session, prompt)
            except Exception as exc:
                self._pending_unobserved_child_sessions.pop(session.session_id, None)
                session.awaiting_auto_resume = False
                session.auto_resume_deadline = None
                session.pending_result = None
                session.live_child_session_ids.clear()
                session.status = "failed"
                session.agent_message = pending_result["agent_message"]
                session.error = {
                    "message": f"{type(exc).__name__}: {exc}",
                    "unobservedSessions": sorted(set(identifiers) | unobserved),
                }
                session.turn_completed = True
                session.turn_start_ambiguous = False
                session.touch()
                return
            if session.pending_result is not None:
                finalize_pending_result(session, touch=False)
            if self._status_writer is not None:
                self._status_writer.delete_result(session.session_id, collector="auto-resume")
            return

        # 保留対象が背景taskの終端だけで消えた場合は確定しない。backendは当該終端に続く再開turnの結果で
        # 保留中の結果を差し替えるため、ここで確定すると再開turnの結果より先に待機表明の結果を公開してしまう。
        # この場合の確定は再開turnの結果か保留期限の経過に委ねる。
        if not has_pending_auto_resume_targets(session) and unobserved:
            finalize_pending_result(session)
            record_unobserved_sessions(session, unobserved)
            return

        deadline = session.auto_resume_deadline
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            unobserved = set(session.live_child_session_ids)
            finalize_pending_result(session)
            if unobserved:
                record_unobserved_sessions(session, unobserved)

    def _take_notices(self, session_id: str) -> list[dict[str, str]]:
        """待機対象sessionの正常な通知を回収し、送信時刻順に返す。"""
        if self._status_writer is None:
            return []
        return self._status_writer.take_notices(session_id)

    @staticmethod
    def _response_with_notices(response: dict[str, Any], notices: list[dict[str, str]]) -> dict[str, Any]:
        """回収した通知がある場合だけ応答へ追加する。"""
        if notices:
            response["notices"] = notices
        return response

    @staticmethod
    def _result_response(session: SessionState, *, include_progress: bool = True) -> dict[str, Any]:
        """wait又はkillの応答を組み立て、返した終端結果を回収済みにする。

        `elapsed_seconds`はturnの`started_at`起点である。
        最終活動時刻と停滞の印は`list`が返すため、本応答へは載せない。
        """
        response = session.public_status(include_result=session.result_available)
        if response.get("status") == "running":
            elapsed_seconds = _elapsed_seconds(session.started_at)
            if elapsed_seconds is not None:
                response["elapsed_seconds"] = elapsed_seconds
        if not include_progress:
            response.pop("progress", None)
            response.pop("elapsed_seconds", None)
        if "agent_message" in response:
            session.result_delivered = True
            session.touch()
        return response

    def _kill_result_response(self, session: SessionState, *, kill_requested: bool) -> dict[str, Any]:
        """killの応答を組み立て、回収した通知がある場合だけ付ける。"""
        response = self._result_response(session, include_progress=False)
        if "agent_message" in response and self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="kill")
        response["kill_requested"] = kill_requested
        return self._response_with_notices(response, self._take_notices(session.session_id))

    def _pending_resume_status(self, pending: _PendingResume) -> dict[str, Any]:
        """進行中の再開操作を通常のrunning状態として射影する。"""
        session = self.sessions.get(pending.state.session_id)
        if session is not None:
            return self._result_response(session)
        response: dict[str, Any] = {"status": "running", "progress": ""}
        elapsed_seconds = _elapsed_seconds(pending.state.started_at)
        if elapsed_seconds is not None:
            response["elapsed_seconds"] = elapsed_seconds
        return response

    async def _run_resume(self, resume_state: SessionResumeState, prompt: ResumePrompt) -> SessionState:
        """backendの再開を完了し、失敗時だけ再試行用状態を復元する。"""
        session_id = resume_state.session_id
        backend = self._backend(resume_state.engine)
        try:
            await _check_plugin_commands(resume_state.cwd)
            session = await backend.resume(
                session_id,
                prompt,
                resume_state.cwd,
                resume_state.model,
                resume_state.effort,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
                turn_seq=resume_state.turn_seq,
            )
            if resume_state.created_at is not None:
                session.created_at = resume_state.created_at
            if self._status_writer is not None:
                self._status_writer.delete_result(session_id, collector="send-message")
            if session.status == "starting":
                session.status = "running"
            session.announced = True
            session.touch()
            _LOG.info(
                "session_transition event=resume session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                session.session_id,
                session.status,
                session.turn_seq,
            )
            return session
        except BaseException:
            if session_id not in self.sessions:
                self.expired_sessions[session_id] = resume_state
            raise
        finally:
            prompt.close()
            pending = self._pending_resumes.get(session_id)
            if pending is not None and pending.task is asyncio.current_task():
                self._pending_resumes.pop(session_id, None)

    @staticmethod
    def _consume_resume_exception(task: asyncio.Task[SessionState]) -> None:
        """timeout後に完了した管理対象taskの例外を回収する。"""
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    def _start_resume(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None,
    ) -> tuple[_PendingResume, int]:
        """期限切れ状態を一意な進行中再開操作へ原子的に移す。"""
        session_id = resume_state.session_id
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions.pop(session_id, None)
        resume_prompt = ResumePrompt(prompt)
        task = asyncio.create_task(self._run_resume(resume_state, resume_prompt))
        pending = _PendingResume(
            state=resume_state,
            task=task,
            prompt=resume_prompt,
            previous_result=previous_result,
        )
        self._pending_resumes[session_id] = pending
        task.add_done_callback(self._consume_resume_exception)
        return pending, resume_prompt.initial_ticket

    async def _resume_response(self, pending: _PendingResume, ticket: int) -> dict[str, Any]:
        """同じ再開taskの配送結果を返し、呼び出し取消時は対応promptだけを外す。"""
        try:
            session = await asyncio.shield(pending.task)
        except asyncio.CancelledError:
            pending.prompt.cancel(ticket)
            raise
        delivery = "reply_failed" if session.result_available else "reply_started"
        response: dict[str, Any] = {"delivery": delivery}
        previous_result = pending.take_previous_result()
        if previous_result:
            response["previous_result"] = previous_result
        return response

    async def _cancel_pending_resume(
        self,
        pending: _PendingResume,
        timeout: float,
    ) -> tuple[SessionState, bool]:
        """保留中の再開と配下作業を回収し、中断要求の受理有無を返す。"""
        pending.discard_previous_result()
        pending.prompt.close()
        cancellation_requested = not pending.task.done()
        if cancellation_requested:
            pending.task.cancel()
        outcomes = await asyncio.wait_for(
            asyncio.gather(pending.task, return_exceptions=True),
            timeout=timeout,
        )
        outcome = outcomes[0]
        if not isinstance(outcome, asyncio.CancelledError):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome, outcome.interrupt_requested

        resume_state = pending.state
        session = self.sessions.get(resume_state.session_id)
        if session is None:
            session = SessionState(
                session_id=resume_state.session_id,
                cwd=resume_state.cwd,
                model=resume_state.model,
                effort=resume_state.effort,
                engine=resume_state.engine,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
                announced=True,
                turn_seq=resume_state.turn_seq + 1,
            )
            if resume_state.created_at is not None:
                session.created_at = resume_state.created_at
            self.sessions[session.session_id] = session
        self.expired_sessions.pop(session.session_id, None)
        if self._pending_resumes.get(session.session_id) is pending:
            self._pending_resumes.pop(session.session_id, None)
        session.status = "interrupted"
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.interrupt_requested = False
        session.touch()
        await self._notify_waiters()
        return session, False

    async def _resume_and_reply(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """保存済みの最小状態から同じ会話を再開し、公開契約の応答を返す。

        再開は結果保持期限の経過と所有主体の終了の双方を契機とする。
        結果本文を保持したまま所有主体だけが終了した場合は、直前結果を応答へ含める。
        """
        pending, ticket = self._start_resume(
            resume_state,
            prompt,
            previous_result,
        )
        return await self._resume_response(pending, ticket)

    async def send_message(
        self,
        session_id: str,
        prompt: str,
        timeout: float = DEFAULT_SEND_MESSAGE_TIMEOUT,
    ) -> dict[str, Any]:
        """実行中turnを継続し、終端済みなら同じsessionでreplyを開始する。"""
        _validate_prompt(prompt)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        prompt = _wrap_delivery_body(prompt)
        try:
            async with asyncio.timeout(float(timeout)):
                while True:
                    async with self._resume_lock:
                        pending = self._pending_resumes.get(session_id)
                        if pending is not None:
                            ticket, changed = pending.prompt.submit_or_observe(prompt)
                            if ticket is not None:
                                return await self._resume_response(pending, ticket)
                            if changed is not None:
                                await changed.wait()
                            else:
                                await asyncio.shield(pending.task)
                            continue
                        retained = self.sessions.get(session_id)
                        if (
                            retained is not None
                            and retained.retention_deadline is not None
                            and asyncio.get_running_loop().time() >= retained.retention_deadline
                        ):
                            self._expire_session(session_id)
                        stopped_state = self._resolve_stopped_session(session_id)
                        if stopped_state is None:
                            stopped_state = self._restore_registry_session(session_id)
                        resume_state = stopped_state or self.expired_sessions.get(session_id)
                        if resume_state is not None:
                            previous_result = None
                            if stopped_state is not None:
                                previous_result = self._take_stopped_result(
                                    session_id,
                                    stopped_state,
                                    collector="send-message",
                                )
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                        session = self._get_session(session_id)
                    backend = self._backend(session.engine)
                    async with session.turn_control_lock:
                        if session.interrupt_requested and not session.terminal:
                            raise ValueError(f"session is being interrupted: {session_id}")
                    try:
                        result = await backend.send_message(session, prompt)
                    except SessionOwnerGoneError:
                        async with self._resume_lock:
                            if self.sessions.get(session_id) is not session:
                                continue
                            previous_result = session.previous_result() if session.result_available else None
                            self._expire_session(session_id)
                            resume_state = self.expired_sessions[session_id]
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                    if self._status_writer is not None:
                        self._status_writer.delete_result(session_id, collector="send-message")
                    delivery = result["delivery"]
                    if delivery in {"reply_started", "reply_ambiguous"}:
                        session.reset_progress()
                    response: dict[str, Any] = {"delivery": delivery}
                    if delivery in REPLY_DELIVERIES:
                        previous_result = result["previous_result"]
                        if previous_result:
                            response["previous_result"] = previous_result
                    return response
        except TimeoutError as exc:
            raise TimeoutError(
                f"send_message timed out: {session_id}; delivery is undetermined, observe with atk agents wait"
            ) from exc

    async def kill(
        self,
        session_id: str,
        timeout: float = DEFAULT_KILL_TIMEOUT,
        stop: bool = False,
    ) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終端を待つ。"""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ValueError("timeout must be non-negative")
        stopped_state = self._resolve_stopped_session(session_id)
        recovered_state: SessionResumeState | None = None
        if stopped_state is None:
            recovered_state = self._restore_registry_session(session_id)
            stopped_state = recovered_state
        if recovered_state is not None:
            response = self._recovered_result_response(recovered_state)
            response["kill_requested"] = False
            return response
        if stopped_state is not None:
            stopped_response = self._take_stopped_result(session_id, stopped_state, collector="kill")
            if stopped_response is not None:
                stopped_response["kill_requested"] = False
                notices = self._take_notices(session_id)
                return self._response_with_notices(stopped_response, notices)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout) if timeout > 0 else None
        delivery_deadline = deadline if deadline is not None else loop.time() + DEFAULT_SEND_MESSAGE_TIMEOUT
        pending = self._pending_resumes.get(session_id)
        if pending is not None:
            try:
                session, interrupt_requested = await self._cancel_pending_resume(
                    pending,
                    max(0.0, delivery_deadline - loop.time()),
                )
            except TimeoutError as exc:
                raise TimeoutError(f"kill timed out: {session_id}; the interrupt request was not delivered") from exc
            if interrupt_requested:
                response = self._kill_result_response(session, kill_requested=True)
                return await self._stop_after_terminal_response(session_id, response, stop)
        else:
            expired_response = self._expired_kill_response(session_id)
            if expired_response is not None:
                return await self._stop_after_terminal_response(session_id, expired_response, stop)
            session = self._get_session(session_id)
        started_terminal = session.terminal
        requested_before_call = session.interrupt_requested
        if started_terminal:
            response = self._kill_result_response(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)

        requested = requested_before_call
        backend = self._backend(session.engine)
        try:
            await asyncio.wait_for(
                session.turn_control_lock.acquire(),
                timeout=max(0.0, delivery_deadline - loop.time()),
            )
        except TimeoutError as exc:
            raise TimeoutError(f"kill timed out: {session_id}; the interrupt request was not delivered") from exc
        try:
            if session.terminal:
                requested = requested or requested_before_call or session.interrupt_requested
            elif session.interrupt_requested:
                requested = True
            else:
                if session.engine == "codex" and not session.turn_id:
                    if timeout == 0:
                        raise ValueError("the active Codex turn has no turn_id")
                    assert deadline is not None
                    try:
                        async with self._condition:
                            await asyncio.wait_for(
                                self._condition.wait_for(lambda: bool(session.turn_id) or session.terminal),
                                timeout=max(0.0, deadline - loop.time()),
                            )
                    except TimeoutError as exc:
                        raise TimeoutError(f"kill timed out: {session_id}; the interrupt request was not delivered") from exc
                    if session.terminal:
                        response = self._kill_result_response(session, kill_requested=False)
                        return await self._stop_after_terminal_response(session_id, response, stop)
                session.interrupt_requested = True
                session.touch()
                try:
                    await asyncio.wait_for(
                        backend.interrupt(session),
                        timeout=max(0.0, delivery_deadline - loop.time()),
                    )
                except TimeoutError:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise TimeoutError(
                        f"kill timed out: {session_id}; interrupt delivery is undetermined, observe with atk agents wait"
                    ) from None
                except Exception:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise
                requested = True
        finally:
            session.turn_control_lock.release()

        if not requested:
            response = self._kill_result_response(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)
        if timeout > 0:
            assert deadline is not None
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.result_available),
                        timeout=max(0.0, deadline - loop.time()),
                    )
            except TimeoutError as exc:
                raise TimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was delivered but the turn did not terminate"
                ) from exc
        response = self._kill_result_response(session, kill_requested=True)
        return await self._stop_after_terminal_response(session_id, response, stop)

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
        if self._auto_resume_task is not None:
            self._auto_resume_task.cancel()
            await asyncio.gather(self._auto_resume_task, return_exceptions=True)
            self._auto_resume_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        pending_resumes = tuple(self._pending_resumes.values())
        for pending in pending_resumes:
            pending.discard_previous_result()
        resume_tasks = tuple(pending.task for pending in pending_resumes)
        for task in resume_tasks:
            if not task.done():
                task.cancel()
        if resume_tasks:
            await asyncio.gather(*resume_tasks, return_exceptions=True)
        backends = tuple(backend for backend in (self._codex, self._claude, self._agy) if backend is not None)
        for backend in backends:
            await backend.close()
        remove_terminal_listener(self._carry_over_unavailable_candidate)
        remove_terminal_listener(self._record_pending_unobserved_child_sessions)
        if self._status_writer is not None:
            remove_touch_listener(self._status_writer.schedule)
            self._status_writer.deactivate()


_MANAGER = AgentsServerManager()


class _InitializationLogTracker:
    """initialize要求と対応応答の節目だけを永続ログへ記録する。"""

    def __init__(self) -> None:
        self._pending_request_ids: set[str | int] = set()

    def receive(self, message: SessionMessage | Exception) -> None:
        root = getattr(getattr(message, "message", None), "root", None)
        if getattr(root, "method", None) != "initialize":
            return
        request_id = getattr(root, "id", None)
        if not isinstance(request_id, (str, int)):
            return
        self._pending_request_ids.add(request_id)
        _LOG.info("FastMCP initializeを受信しました: request_id=%s", request_id)

    def sent(self, message: SessionMessage) -> None:
        root = getattr(message.message, "root", None)
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        error = getattr(root, "error", None)
        if error is not None:
            _LOG.error(
                "FastMCP initialize応答が失敗しました: request_id=%s exception_type=%s exception=%s",
                request_id,
                type(error).__name__,
                getattr(error, "message", error),
            )
            return
        _LOG.info("FastMCP initialize応答が完了しました: request_id=%s", request_id)

    def send_failed(self, message: SessionMessage, exc: BaseException) -> None:
        root = getattr(message.message, "root", None)
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        _LOG.error(
            "FastMCP initialize応答の送信に失敗しました: request_id=%s exception_type=%s exception=%s",
            request_id,
            type(exc).__name__,
            exc,
        )

    def transport_closed(self) -> None:
        for request_id in sorted(self._pending_request_ids, key=str):
            _LOG.error(
                "FastMCP initializeが未完了のままtransportが終了しました: "
                "request_id=%s exception_type=RuntimeError exception=transport closed before initialize response",
                request_id,
            )
        self._pending_request_ids.clear()


class _InitializationLoggingReceiveStream(ObjectReceiveStream[SessionMessage | Exception]):
    def __init__(
        self,
        stream: ObjectReceiveStream[SessionMessage | Exception],
        tracker: _InitializationLogTracker,
    ) -> None:
        self._stream = stream
        self._tracker = tracker

    async def receive(self) -> SessionMessage | Exception:
        message = await self._stream.receive()
        self._tracker.receive(message)
        return message

    async def aclose(self) -> None:
        await self._stream.aclose()


class _InitializationLoggingSendStream(ObjectSendStream[SessionMessage]):
    def __init__(self, stream: ObjectSendStream[SessionMessage], tracker: _InitializationLogTracker) -> None:
        self._stream = stream
        self._tracker = tracker

    async def send(self, item: SessionMessage) -> None:
        try:
            await self._stream.send(item)
        except BaseException as exc:
            self._tracker.send_failed(item, exc)
            raise
        self._tracker.sent(item)

    async def aclose(self) -> None:
        await self._stream.aclose()


class _AgentsServerFastMCP(FastMCP[Any]):
    """stdio上のinitialize節目を診断ログへ残し、ツールの説明文へ境界を付けるFastMCP。"""

    @typing.override
    def add_tool(  # noqa: PLR0913 -- 上位の署名をそのまま受け取る
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> None:
        """ツールの説明文へ境界を付けて登録する。

        説明文は実行ホストがスキーマとしてsystem promptへ載せる。登録の1箇所で囲むことで、
        ツールごとの書き分けを増やさずに全てのツールへ同じ境界を付ける。
        """
        resolved = description if description is not None else inspect.getdoc(fn) or ""
        super().add_tool(
            fn,
            name,
            title,
            _schema_text(resolved, kind=_KIND_MCP_TOOL),
            annotations,
            icons,
            meta,
            structured_output,
        )

    async def run_stdio_async(self) -> None:
        tracker = _InitializationLogTracker()
        async with stdio_server() as (read_stream, write_stream):
            try:
                await self._mcp_server.run(
                    cast(
                        MemoryObjectReceiveStream[SessionMessage | Exception],
                        _InitializationLoggingReceiveStream(read_stream, tracker),
                    ),
                    cast(
                        MemoryObjectSendStream[SessionMessage],
                        _InitializationLoggingSendStream(write_stream, tracker),
                    ),
                    self._mcp_server.create_initialization_options(),
                )
            finally:
                tracker.transport_closed()


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    _LOG.info("manager activateを開始します")
    try:
        _MANAGER.activate()
    except BaseException:
        _LOG.exception("manager activateに失敗しました: stage=manager_activate")
        raise
    _LOG.info("manager activateが完了しました")
    try:
        yield
    finally:
        _LOG.info("manager closeを開始します")
        try:
            await _MANAGER.close()
        except BaseException:
            _LOG.exception("manager closeに失敗しました: stage=manager_close")
            raise
        _LOG.info("manager closeが完了しました")


with warnings.catch_warnings():
    if IncompleteFieldDefinitionWarning is not None:
        warnings.simplefilter("ignore", IncompleteFieldDefinitionWarning)
    mcp = _AgentsServerFastMCP(
        "agents_server",
        instructions=_schema_text(
            "Codex、ClaudeまたはAntigravityへの非同期委譲。承認操作は公開しない。\n"
            "`start`は専用タスク文書、`start_custom`は自由本文からsessionを開始する。"
            "`start_explore`は読み取り専用探索、`start_shell`はコマンド実行、`start_write`は確定済みの軽量書込を委譲する。"
            "終端と結果本文は`atk agents wait --output-file <絶対パス>`で受け取る。"
            "`wait`はsession_idの位置引数を取らず、登録済みsessionの終端を待つ。"
            "`list`は最小状態、`show`は個別の診断情報を返す。"
            "継続は`send_message`、実行中turnの中断は`kill`、終端済みsessionの明示的な破棄は`stop`で行う。\n"
            "`start`・`start_custom`・`start_explore`・`start_write`・`start_shell`が返した`session_id`と、"
            "`send_message`で新しい指示を配送したsessionは、"
            "実行ホストで`atk agents wait`を発行して観測するか、結果が不要なら`kill`で破棄する。"
            "観測を試みていない作業を残したままターンを終えると、当該作業を観測する主体が残らない。\n"
            "start系の起動ツール（`start`・`start_custom`・`start_explore`・`start_shell`・`start_write`）は、"
            "次の共通引数を同じ意味で受け取る。各ツールの引数説明にはツール固有の既定値だけを書く。\n"
            "共通引数`model_type`: 工程別モデル設定の種別（例: `execute`）、又はASCIIカンマ区切りの"
            "`<claude|codex|agy>:<model>[/<effort>]`候補列（例: `agy:gemini-3.8-flash/medium,claude:opus[1m]/medium`）。"
            "候補は先頭から試し、起動可能な候補へ切り替える。"
            "`start_custom`では必須とする。他の起動ツールでは省略可能で、省略時は各ツールの工程別設定を使い、"
            "指定時はその値で一時的に上書きする。恒常的な変更は`atk config set`で行う。\n"
            "共通引数`label`: 当該sessionを人が識別する短い名前とし、`show`・`atk agents list`・statuslineへ現れる。"
            "全起動ツールで次の凡例に従う。`<…1〜2語>`は英小文字・数字・日本語の語をハイフンで連結した1〜2語とし、"
            "依頼本文や文章をそのまま使わない。\n"
            "| 起動 | 形式 | 例 |\n"
            "| --- | --- | --- |\n"
            "| `start`（省略時はサーバーが生成） | `extra_params`に`レーン識別子`があれば`<レーン識別子>-<タスク文書名>`、"
            "無ければ`<タスク文書名>`。タスク文書名はファイル名から`.subagent.md`を除いた名前 | "
            "`lane-01-exec`、`lane-01-exec-review`、`pick-wi` |\n"
            "| レビューを目的とする`start_explore`又は`start_custom` | `<レビュー対象を表す語>-review` | `pr-body-review` |\n"
            "| `start_explore`（レビュー以外） | `explore-<調査対象を示す1〜2語>` | `explore-pyfltr` |\n"
            "| `start_shell` | `shell-<コマンド名など1〜2語>` | `shell-make-test` |\n"
            "| `start_write` | `write-<起草対象を示す1〜2語>` | `write-awi` |\n"
            "| `start_custom`（レビュー以外） | 役割を表す短い語 | `audit` |\n"
            "`start_explore`・`start_shell`・`start_write`はlabelを明示して起動する。"
            "省略時の既定値（`explore`、`shell-<コマンド名>`、`write`）だけでは同じ種別のsessionを区別できないためである。",
            kind=_KIND_MCP_INSTRUCTIONS,
        ),
        lifespan=_mcp_lifespan,
    )


@mcp.tool(name="start", structured_output=True)
async def start(
    subagent_md_path: Annotated[
        str,
        Field(
            description=_parameter_description(
                "受信側の起動・返却契約を保持するagent-toolkitの`.subagent.md`絶対パス。"
                "自由な本文を渡す場合は`start_custom`を使う。"
            )
        ),
    ],
    extra_params: Annotated[
        dict[str, str],
        Field(
            description=_parameter_description(
                "タスク文書の必須入力名をキーとする追加パラメータ。固有の補足は`追加指示`へ渡す。"
            )
        ),
    ],
    cwd: str,
    label: Annotated[
        str | None,
        Field(
            description=_label_description(
                "省略時は`extra_params`の`レーン識別子`とタスク文書名から`<レーン識別子>-<タスク文書名>`"
                "（`レーン識別子`が無ければ`<タスク文書名>`）を生成する。"
            )
        ),
    ] = None,
    model_type: Annotated[
        str | None,
        Field(description=_model_type_description("省略時はタスク文書に対応する工程別設定を使う。")),
    ] = None,
) -> dict[str, Any]:
    """専用タスク文書と名前付き追加入力から委譲先turnを開始する。

    タスク文書を読み、同文書の必須入力名と`extra_params`を照合し、文書本文と出所を起動文へ含めてから起動する。
    engine、model、effortはタスク文書に対応する工程別モデル設定から決め、`model_type`を指定した場合はその値から決める。
    engineの利用上限などで起動できない候補はサーバーが自動的に除外し、残る候補で起動する。
    返した`session_id`は同じ応答の中で実行ホストの`atk agents wait --output-file <絶対パス>`を開始して観測するか、
    結果が不要なら`kill`で破棄する。
    `atk agents wait`は`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。
    応答は`session_id`と`status`を含む。候補を切り替えて起動した場合だけ、除外した候補と
    除外の根拠、および採用した`engine`・`model`・`effort`を加える。起動条件の詳細は`show`で取得する。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    `label`は当該sessionの識別名として`show`・`atk agents list`・statuslineへ現れる。
    全候補が可用性またはagyのturn失敗で終端した場合は、最後の候補の終端応答を返す。
    最後のagy候補がbackend開始例外で失敗した場合は、除外理由を含む例外を送出する。
    """
    task_model_type, prompt = _task_document_request(subagent_md_path, extra_params)
    response = await _MANAGER.start(
        model_type or task_model_type,
        prompt,
        cwd,
        label=_resolve_display_label(label, _task_document_label(subagent_md_path, extra_params)),
        composed_by=COMPOSED_BY_AGENTS_SERVER,
    )
    return _public_start_response(response)


@mcp.tool(name="start_custom", structured_output=True)
async def start_custom(
    prompt: str,
    model_type: Annotated[
        str,
        Field(description=_model_type_description("必須。専用タスク文書がある場合は`start`を使う。")),
    ],
    cwd: str,
    label: Annotated[
        str | None,
        Field(description=_label_description("省略時は依頼本文の先頭にある空でない1行を正規化した値を用いる。")),
    ] = None,
) -> dict[str, Any]:
    """専用タスク文書がない自由な指示本文から委譲先turnを開始する。

    既存の`.subagent.md`で表現できる作業には使わない。engine、model及びeffortは
    `model_type`の設定種別または直接指定の候補列から解決する。
    候補は先頭から試し、通常応答は後続の観測に必要な`session_id`と`status`を返す。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    候補を切り替えて起動した場合だけ、除外した候補と採用した候補を加える。
    返した`session_id`は同じ応答の中で実行ホストの`atk agents wait --output-file <絶対パス>`を開始して観測するか、
    結果が不要なら`kill`で破棄する。
    `atk agents wait`は`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。
    engineの利用上限などで起動できない候補はサーバーが自動的に除外し、残る候補で起動する。
    """
    response = await _MANAGER.start(model_type, prompt, cwd, label=label)
    return _public_start_response(response)


@mcp.tool(name="start_explore", structured_output=True)
async def start_explore(
    prompt: str,
    cwd: str,
    fast: Annotated[
        bool,
        Field(
            description=_parameter_description(
                "`false`は`explore_model`、`true`は`explore_fast_model`の設定を候補列として使う。"
                "既定の`true`のまま使い、軽量側の候補では判断材料が不足する調査だけ`false`を指定する。"
            )
        ),
    ] = True,
    label: Annotated[
        str | None,
        Field(
            description=_label_description(
                "形式は`explore-<調査対象を示す1〜2語>`、レビューでは`<レビュー対象を表す語>-review`。省略時は`explore`。"
            )
        ),
    ] = None,
    model_type: Annotated[
        str | None,
        Field(
            description=_model_type_description(
                "省略時は`fast`に応じて`explore_fast`又は`explore`の設定を使う。指定時は`fast`を参照しない。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """探索専用の軽量な起動条件で委譲先turnを開始する。

    `fast=true`では`explore_fast_model`、`fast=false`では`explore_model`の候補列を使う。
    `model_type`を指定した場合は`fast`を参照せず、その値から候補列を決める。
    engineの利用上限などで起動できない候補はサーバーが自動的に除外し、残る候補で起動する。
    返した`session_id`は同じ応答の中で実行ホストの`atk agents wait --output-file <絶対パス>`を開始して観測するか、
    結果が不要なら`kill`で破棄する。
    `atk agents wait`は`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。
    プロジェクト指示の読込を減らした軽量な起動条件で開始する。
    起動時のシステム指示でファイルを作成、変更及び削除しない契約を委譲先へ課すため、成果ファイルの出力を依頼しない。
    読み取りが数回で確定する調査は自ら実行し、多数のファイルを横断する調査や大量の本文を読む調査を本ツールへ委譲する。
    委譲すると、呼び出し元の文脈へは結果の要約だけが入る。
    応答と、候補が尽きた場合の扱いは`start`と同じである。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    """
    response = await _MANAGER.start_explore(fast, prompt, cwd, label=label, model_type=model_type)
    return _public_start_response(response)


@mcp.tool(name="start_shell", structured_output=True)
async def start_shell(
    command: Annotated[str, Field(description=_parameter_description("実行するコマンド。委譲先がシェルで実行する。"))],
    cwd: Annotated[
        str,
        Field(description=_parameter_description("実行時の作業ディレクトリ。既存ディレクトリの絶対パスとする。")),
    ],
    summary_policy: Annotated[
        str,
        Field(description=_parameter_description("結果の要約方針。報告へ含める値と粒度を書く。")),
    ],
    label: Annotated[
        str | None,
        Field(
            description=_label_description(
                "形式は`shell-<コマンド名など1〜2語>`。省略時は`shell-<コマンドの最初の語のbasename>`。"
            )
        ),
    ] = None,
    model_type: Annotated[
        str | None,
        Field(description=_model_type_description("省略時は`explore_fast`の設定を使う。")),
    ] = None,
) -> dict[str, Any]:
    """コマンドを実行して結果を要約する委譲先turnを開始する。

    `explore_fast_model`の候補列で軽量な起動条件を使い、呼び出し元へは終了状態と要約だけを返す。
    `model_type`を指定した場合はその値から候補列を決める。
    読み取り専用の制約は課さないため、検査コマンドなど対象を変更する実行を渡せる。
    返した`session_id`は同じ応答の中で実行ホストの`atk agents wait --output-file <絶対パス>`を開始して観測するか、
    結果が不要なら`kill`で破棄する。
    `atk agents wait`は`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。
    委譲と直接実行の採算は、コマンドの出力量で判定する。
    出力が4,000トークン（英数字主体で約16,000バイト、300行程度）を超える見込みのコマンドは本ツールへ委譲し、
    1,000トークン未満に収まる見込みのコマンドは自ら実行する。
    委譲すると、呼び出し元の文脈へは終了状態と要約だけが入る。
    応答と、候補が尽きた場合の扱いは`start`と同じである。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    """
    response = await _MANAGER.start_shell(command, cwd, summary_policy, label=label, model_type=model_type)
    return _public_start_response(response)


@mcp.tool(name="start_write", structured_output=True)
async def start_write(
    prompt: str,
    cwd: str,
    label: Annotated[
        str | None,
        Field(description=_label_description("形式は`write-<起草対象を示す1〜2語>`。省略時は`write`。")),
    ] = None,
    model_type: Annotated[
        str | None,
        Field(description=_model_type_description("省略時は`write`の設定を使う。")),
    ] = None,
) -> dict[str, Any]:
    """確定済みの文章起草と小規模な定型書込を委譲する。

    設計、調査、レビュー及び公開操作を依頼せず、成果物種別、読者、事実、根拠、反映先と完成形を`prompt`へ明記する。
    読者が異なる文章は別の依頼にする。プロジェクト指示の読込を省いた`write_model`の候補列を使い、ファイルの読取・検索・作成・編集だけを許可する。
    `model_type`を指定した場合はその値から候補列を決める。
    終端と結果本文は、返した`session_id`を保持して実行ホストの`atk agents wait --output-file <絶対パス>`で受け取る。
    `atk agents wait`は`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。
    結果が不要なら`kill`で破棄する。
    応答は`start`と同じ項目を含む。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    """
    response = await _MANAGER.start_write(prompt, cwd, label=label, model_type=model_type)
    return _public_start_response(response)


@mcp.tool(name="send_message", structured_output=True)
async def send_message(
    session_id: str,
    prompt: str,
    timeout: Annotated[
        float,
        Field(
            description=_parameter_description(
                "継続要求の配送結果が確定するまでの待機上限秒数。"
                "固有のtimeout要件がなければ引数を省略して通常既定を使う。"
                "委譲先の応答生成の完了は待たない。0以下は受理しない。"
            )
        ),
    ] = DEFAULT_SEND_MESSAGE_TIMEOUT,
) -> dict[str, Any]:
    """実行中turnへ追加指示を送り、終端済みなら同じsessionでreplyを開始する。

    通常の既定は270秒である。固有のtimeout要件がなければ引数を省略して通常既定を使う。
    待つのは継続要求の配送結果が確定するまでであり、委譲先の応答生成の完了ではない。
    上限に達した場合は配送の成否が確定しないため、`atk agents wait`で状態を確認する。
    実行中turnにはsteerし、終端済みturnでは結果回収を前提にせず同じsessionのreplyを開始する。
    保持期限を過ぎた場合と、sessionを所有する実行主体が終了している場合も、保持済みの最小状態から会話を暗黙に再開する。
    応答は`delivery`を含む。終端済みsessionの未回収の終端結果を消費して新しいturnを開始した場合は、
    その結果を`previous_result`（`status`・`agent_message`と、ある場合は`error`）で返す。
    消費した結果は`atk agents wait`で受領できないため、呼び出し元は同じ応答から受け取る。
    回収済みの結果は含めない。
    `delivery`の値は次のとおりである。
    `steered`は実行中turnの配送キューへ指示を投入したことだけを示し、委譲先が読んだことは示さない。
    委譲先が単一の長時間コマンドを実行している間はturnの区切りに達しないため、指示はキューに残る。
    到達は、`show`が返す`seconds_since_activity`と`active_tool_uses`の変化で判定する。
    `reply_started`は終端済みsessionで新しいturnを開始したことを示す。
    `reply_failed`は新しいturnを開始できなかったこと、`reply_ambiguous`は開始の成否を確定できなかったことを示し、
    いずれも`atk agents wait`で状態を確認してから次の操作を選ぶ。
    sessionの起動後に工程別モデル設定の候補列が変わっても、起動時に確定したengine・model・effortで継続する。
    採用済みのengineが実際に利用不能で継続できない場合は、backendが返す理由に従って回復手段を選ぶ。
    保持済みsessionを失って継続できない場合は`unknown session: <session_id>`を返す。
    """
    response = await _MANAGER.send_message(session_id, prompt, timeout)
    public: dict[str, Any] = {"delivery": response["delivery"]}
    previous_result = response.get("previous_result")
    if previous_result:
        public["previous_result"] = previous_result
    return public


@mcp.tool(name="kill", structured_output=True)
async def kill(
    session_id: str,
    timeout: Annotated[
        float,
        Field(
            description=_parameter_description(
                "中断要求後に終端を待つ上限秒数。"
                "固有のtimeout要件がなければ引数を省略して通常既定を使う。"
                "0は中断要求配送後の現状態を返す。"
            )
        ),
    ] = DEFAULT_KILL_TIMEOUT,
    stop: Annotated[
        bool,
        Field(description=_parameter_description("終端結果を返した応答に限り、同じsessionを応答後に破棄する。")),
    ] = False,
) -> dict[str, Any]:
    """実行中turnへ中断を要求し、指定時間まで終端を待つ。

    停止は最終手段とする。実行中の委譲先には`send_message`で訂正を配送できるため、
    そちらで意図を満たせる場合は、停止によって失われる作業と再起動の費用の方が大きい。
    本ツールを選ぶ前に、`send_message`による訂正では足りないことと、当該作業の継続自体が不要であることを確認する。
    通常の既定は270秒である。固有のtimeout要件がなければ引数を省略して通常既定を使う。
    `timeout=0`は中断要求配送後の現状態を返す。
    timeoutに達した場合もsessionとbackend processは破棄しないため、`atk agents wait`で状態を確認してから次の操作を選ぶ。
    終端結果の保持期限を過ぎたsessionでは中断する実行中turnが無いため、`status`へ`expired`、`kill_requested`へ`false`を設定した応答を返す。
    """
    return await _MANAGER.kill(session_id, timeout, stop)


@mcp.tool(name="stop", structured_output=True)
async def stop_session(session_id: str) -> dict[str, Any]:
    """再開する予定の無い終端済みsessionを明示的に破棄する。

    statusLineの表示対象と`list`の応答から除き、backendがsession専用に保持する資源を解放する。
    実行中turnを持つsessionは破棄しない。中断が必要な場合は先に`kill`を発行する。
    破棄後も同じ`session_id`への`send_message`で会話を暗黙再開できる。
    成功時は空のオブジェクトを返し、失敗は例外で示す。
    """
    return await _MANAGER.stop(session_id)


@mcp.tool(name="list", structured_output=True)
async def list_sessions(include_terminated: bool = False) -> dict[str, Any]:
    """保持中のsessionの状態を開始順に返す。

    所有する`root_session_id`を常に返す。PostToolUseはこの値をCLI会話の別名索引へ記録する。
    各sessionの`session_id`と`status`を返し、稼働中のsessionへ最終活動時刻からの経過秒数`seconds_since_activity`を加える。
    起動条件は`show`で取得する。
    結果本文は返さないため、終端の観測と結果の受領には`atk agents wait`を使う。
    既定では未回収結果を持たない終端済み又は`expired`のsessionを除き、除いた件数を`omitted`へ返す。
    全件が必要な場合は`include_terminated`へ真を渡す。このとき`omitted`は0となる。
    保持していた`session_id`を失った場合の回復と、並行する委譲先の残作業の把握へ用いる。
    """
    return _MANAGER.list_sessions(include_terminated=include_terminated)


@mcp.tool(name="show", structured_output=True)
async def show_session(session_id: str, verbose: bool = False) -> dict[str, Any]:
    """1件のsessionについて、文脈復旧又はトラブルシューティング用の詳細を返す。

    既定では識別名、起動prompt、cwd、種別、model_type、status、結果の有無及び進行中の停滞診断を返す。
    `model_type`は工程別設定の種別名、または起動ツールの`model_type`へ渡した候補列である。
    停滞診断の`seconds_since_activity`はツール呼び出しを含む最後の活動からの経過秒数であり、停滞の疑いはこの値で判定する。
    `seconds_since_output`は最新のテキスト出力からの経過秒数である。
    テキスト出力だけが止まり活動が続いている状態は長時間のコマンドの実行中であり、停滞ではない。
    `status`が`running`で未完了のツール呼び出しがある場合は、`active_tool_uses`へ各呼び出しの種別、開始時刻及び入力の要約を返す。
    前回の照会と同じ呼び出しが同じ開始時刻で続いている場合も、長時間のコマンドの実行中として扱う。
    `status`が`running`で、このsessionが`start`系ツールで起動し終端をまだ観測していない子sessionがある場合は、
    安定した順序の`live_child_sessions`（`session_id`と`cwd`の対）を返す。
    この一覧は子の終端を観測するまで残るため、子が稼働中である根拠にしない。
    子の状態は、子の`session_id`を渡した`show`の`status`と`seconds_since_activity`で判定する。
    `cwd`を解決できない識別子は`live_child_session_ids_without_cwd`へ分けて返し、当該識別子へは追送と打ち切りを発行できない。
    `verbose=True`はengine、model、effort、開始・更新時刻、turn番号及び解決可能なroot sessionも加える。
    終端結果本文は返さないため、受領には`atk agents wait`を使う。
    """
    return _MANAGER.show_session(session_id, verbose=verbose)


def _prepare_child_environment() -> None:
    """起動元ツールのエフェメラル仮想環境を、以降に起動する委譲先から取り除く。

    Claude backendが渡す`ClaudeAgentOptions.env`は継承環境へ重なる仕様であり、
    キーの削除を表現できない。Codex backendのApp Server子プロセスも本プロセスの環境を継承する。
    このため両経路の起点である本プロセスの環境を、起動時に1回だけ整える。
    """
    _inherited_venv.strip_inherited_venv(os.environ)


def _configure_logging() -> pathlib.Path:
    """標準エラーと永続ファイルへagents_serverの診断ログを出力する。"""
    return logging_config.configure_logging()


def main(argv: Sequence[str] | None = None) -> int:
    """引数に応じて依存検査またはMCP stdio transportを起動する。"""
    _prepare_child_environment()
    parser = argparse.ArgumentParser(description="CodexとClaudeの委譲先を非同期MCPとして公開する。")
    parser.add_argument(
        "--check-dependencies",
        action="store_true",
        help="Claude Agent SDKの依存を読み込み、options構築まで検査する。",
    )
    args = parser.parse_args(argv)
    log_path = _configure_logging()
    mode = "check-dependencies" if args.check_dependencies else "stdio"
    _LOG.info("agents_serverを起動します: mode=%s log=%s", mode, log_path)
    try:
        if args.check_dependencies:
            claude_backend.check_dependencies()
        else:
            mcp.run(transport="stdio")
    except BaseException:
        _LOG.exception("agents_serverが異常終了しました: mode=%s", mode)
        raise
    _LOG.info("agents_serverが正常終了しました: mode=%s", mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
