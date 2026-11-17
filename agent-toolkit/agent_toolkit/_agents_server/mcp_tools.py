"""agents_serverのMCPツールの定義と、MCPサーバーのlifespanおよび起動。"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import pathlib
import subprocess
import sys
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolRequestParams, ToolAnnotations
from pydantic import Field

from agent_toolkit._agents_server import (
    child_environment,
    launch_requests,
    lifetime,
    logging_config,
    shared_roots,
    state,
    status_file,
)
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_requests import task_document_label, task_document_request, validate_start_inputs
from agent_toolkit._agents_server.manager import DEFAULT_KILL_TIMEOUT, DEFAULT_SEND_MESSAGE_TIMEOUT, AgentsServerManager
from agent_toolkit._agents_server.manager_start import model_override_warnings
from agent_toolkit._agents_server.mcp_transport import AgentsServerMCP
from agent_toolkit._agents_server.responses import public_send_message_response, public_start_response, resolve_display_label
from agent_toolkit._agents_server.tool_descriptions import (
    COMMAND_DESCRIPTION,
    CWD_DESCRIPTION,
    DELEGATION_INPUT_GUIDANCE,
    EXTRA_PARAMS_DESCRIPTION,
    HANDOFF_FROM_DESCRIPTION,
    KIND_MCP_INSTRUCTIONS,
    LABEL_DESCRIPTION,
    LIGHTWEIGHT_MODE_SELECTION,
    MODE_DESCRIPTION,
    MODEL_TYPE_DESCRIPTION,
    PROMPT_DESCRIPTION,
    SESSION_ID_DESCRIPTION,
    START_DESCRIPTION,
    SUBAGENT_MD_PATH_DESCRIPTION,
    SUMMARY_POLICY_DESCRIPTION,
    WHOLE_CONTRACT_PATH_DESCRIPTION,
    StartMode,
    parameter_description,
    schema_text,
)
from agent_toolkit._common import (
    delegated_session,
    function_hook_requests,
    mcp_environment,
    response_language_check,
    runtime_environment,
    runtime_inserted,
    session_registry,
)
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._common.next_action import ActionableError
lazy from agent_toolkit._atk.wi import operation_permissions
lazy from agent_toolkit._common.operation_approvals import request_display

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")

if TYPE_CHECKING:
    from agent_toolkit._agents_server.service_context import ServiceManagers


_MANAGER = AgentsServerManager()
_SERVICE_MANAGERS: ServiceManagers | None = None
_UI_DELEGATED = delegated_session.is_delegated(os.environ)
_SELECTED_MANAGER: contextvars.ContextVar[AgentsServerManager | None] = contextvars.ContextVar(
    "agents_server_request_manager", default=None
)


def _manager() -> AgentsServerManager:
    return _SELECTED_MANAGER.get() or _MANAGER


async def _ui_request(operation: str, inputs: dict[str, Any]) -> dict[str, Any]:
    caller = runtime_environment.current()
    if _UI_DELEGATED if caller is None else delegated_session.is_delegated(caller.environment):
        raise ValueError("メイン専用ツール。結果本文は委譲元へ最終応答で返せ。")
    identifier = _manager().launcher_session_id()
    if identifier is None:
        raise ValueError("呼出元会話を解決できない。Claude CodeでMCPへ再接続する。")
    return await function_hook_requests.request(identifier, operation, inputs)


@contextlib.asynccontextmanager
async def _manager_lifespan(
    manager: AgentsServerManager, *, ready_for_requests: bool = False, shared_monitor: bool = False
) -> AsyncGenerator[None]:
    _LOG.info("manager activateを開始します")
    try:
        if shared_monitor:
            await manager.activate_async(shared_monitor=True)
        else:
            await manager.activate_async()
        await manager.prepare_delegation_recovery()
        if ready_for_requests:
            await manager.initialize_delegation_recovery()
            identifier = manager.launcher_session_id()
            root, _ = manager.status_owner()
            assert identifier is not None and root is not None
            await run_file_operation(shared_roots.write_root_alias, identifier, root)
    except BaseException:
        _LOG.exception("manager activateに失敗しました: stage=manager_activate")
        await manager.close()
        await run_file_operation(logging_config.flush_logging)
        raise
    _LOG.info("manager activateが完了しました")
    root_session_id, owner_status_file = manager.status_owner()
    logging_config.record_instance_event("bound", root_session_id=root_session_id, owner_status_file=owner_status_file)
    recovery = asyncio.create_task(manager.monitor_delegation_recovery())
    try:
        yield
    finally:
        _LOG.info("manager closeを開始します")
        try:
            try:
                recovery.cancel()
                await lifetime.wait_for_shutdown((recovery,))
            finally:
                await manager.close()
        except BaseException:
            _LOG.exception("manager closeに失敗しました: stage=manager_close")
            await run_file_operation(logging_config.flush_logging)
            raise
        _LOG.info("manager closeが完了しました")
        await run_file_operation(logging_config.flush_logging)


class _RequestManagers:
    """同じMCP接続から受けたCodexの会話を、所有者と終了処理ごとに分離する。"""

    def __init__(self, stack: contextlib.AsyncExitStack) -> None:
        self._stack = stack
        self._managers: dict[str, AgentsServerManager] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, identifier: str) -> AgentsServerManager:
        async with self._locks.setdefault(identifier, asyncio.Lock()):
            if identifier not in self._managers:
                # Codexの通常MCP起動には会話IDの環境値が無い。要求のthreadIdを元に既存のrootを確定する。
                sessions: dict[str, state.SessionState] = {}
                writer = status_file.StatusFileWriter(sessions, shared_roots.StatusFileIdentity(identifier, "root.json", None))
                manager = AgentsServerManager(writer, launcher_environment={"CODEX_THREAD_ID": identifier})
                await self._stack.enter_async_context(_manager_lifespan(manager, ready_for_requests=True))
                self._managers[identifier] = manager
            return self._managers[identifier]


_REQUEST_MANAGERS: contextvars.ContextVar[_RequestManagers | None] = contextvars.ContextVar(
    "agents_server_request_managers", default=None
)
_TOOL_USE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("agents_server_tool_use_id", default=None)
"""Claude CodeがMCP要求の`_meta`へ渡す呼出しのtool_use_id。送達表示を呼出し単位で区別するために使う。"""


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: MCPServer[None]) -> AsyncGenerator[None]:
    if _SERVICE_MANAGERS is not None:
        yield
        return
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(_manager_lifespan(_MANAGER))
        token = _REQUEST_MANAGERS.set(_RequestManagers(stack))
        try:
            yield
        finally:
            _REQUEST_MANAGERS.reset(token)


@contextlib.asynccontextmanager
async def _request_scope(params: CallToolRequestParams) -> AsyncGenerator[None]:
    tool_use_id = (params.meta or {}).get("claudecode/toolUseId")
    token = _TOOL_USE_ID.set(tool_use_id if isinstance(tool_use_id, str) and tool_use_id else None)
    try:
        async with _manager_scope(params):
            yield
    finally:
        _TOOL_USE_ID.reset(token)


@contextlib.asynccontextmanager
async def _manager_scope(params: CallToolRequestParams) -> AsyncGenerator[None]:
    if _SELECTED_MANAGER.get() is not None:
        yield
        return
    if _SERVICE_MANAGERS is not None:
        async with _SERVICE_MANAGERS.scope(params) as manager:
            token = _SELECTED_MANAGER.set(manager)
            try:
                yield
            finally:
                _SELECTED_MANAGER.reset(token)
        return
    metadata = params.meta
    identifier = metadata.get("threadId") if metadata else None
    if identifier is None:
        yield
        return
    if not isinstance(identifier, str) or not shared_roots.valid_session_id(identifier):
        raise ActionableError("MCP要求のthreadIdが不正", next_action="CodexのバージョンとMCP要求の会話IDを確認して再接続する")
    if identifier == _MANAGER.launcher_session_id():
        yield
        return
    pool = _REQUEST_MANAGERS.get()
    if pool is None:
        raise ActionableError("MCP接続が未初期化", next_action="CodexからMCPサーバーへ再接続する")
    manager = await pool.get(identifier)
    token = _SELECTED_MANAGER.set(manager)
    try:
        yield
    finally:
        _SELECTED_MANAGER.reset(token)


mcp = AgentsServerMCP(
    "agents_server",
    instructions=schema_text(
        "Codex、Claude、AntigravityまたはOpenCodeへの非同期委譲。承認操作は公開しない。\n"
        "Claude Code・Codexからの委譲は本サーバーを標準とする。"
        "組み込みの委譲機能（`Agent`・`spawn_agent`等）を使える例外は、"
        "`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」に従う。\n"
        "`start`がsessionを開始し、`mode`で`<役割名>.subagent.md`の定型作業、自由本文の委譲、読み取り専用の探索、"
        "確定済みの書込、コマンド実行を選ぶ。入力とmodeごとの条件は`start`と各引数の説明が定める。\n"
        f"{LIGHTWEIGHT_MODE_SELECTION}\n"
        "task・delegate: 独立した文脈での評価、モデルの使い分けまたは並列化の利益がある作業を委譲する。"
        "要否の判定は`agent-toolkit:delegation`の`references/routing.md`「要否判定」に従う。\n"
        "終了と結果本文は引数なしの単独コマンド`atk agents wait`で受け取る。"
        "`wait`はsession_idの位置引数を取らず、登録済みsessionの終了を待ち、応答の時点で終了したsessionの結果を返す。"
        "返った結果はその場で処理し、残りのsessionは同じコマンドを再発行して待つ。"
        "全件の終了まで戻らないループやスクリプトで待機を包まない。"
        "`list`は最小状態、`show`は個別の診断情報を返す。"
        "継続は`send_message`、実行中turnの中断は`kill`、終了済みsessionの明示的な破棄は`stop`で行う。\n"
        "`start`が返した`session_id`と、`send_message`で新しい指示を配送したsessionは、"
        "実行ホストで`atk agents wait`を発行して観測するか、結果が不要なら`kill`で破棄する。"
        "観測を試みていない作業を残したままターンを終えると、その作業を観測する主体が残らない。",
        kind=KIND_MCP_INSTRUCTIONS,
    ),
    lifespan=_mcp_lifespan,
    request_scope=_request_scope,
)


@mcp.tool(
    name="start",
    title="委譲sessionの開始",
    description=START_DESCRIPTION,
    # 新しいsessionを起動するだけで、既存のsessionと結果を変えない。
    annotations=ToolAnnotations(destructive_hint=False),
    structured_output=True,
)
async def start(  # noqa: PLR0913 -- 公開入力をmodeごとの平坦な引数として受け取る
    cwd: Annotated[str | None, Field(description=CWD_DESCRIPTION)] = None,
    mode: Annotated[StartMode | None, Field(description=MODE_DESCRIPTION)] = None,
    subagent_md_path: Annotated[str | None, Field(description=SUBAGENT_MD_PATH_DESCRIPTION)] = None,
    extra_params: Annotated[dict[str, str] | None, Field(description=EXTRA_PARAMS_DESCRIPTION)] = None,
    prompt: Annotated[str | None, Field(description=PROMPT_DESCRIPTION)] = None,
    command: Annotated[str | None, Field(description=COMMAND_DESCRIPTION)] = None,
    summary_policy: Annotated[str | None, Field(description=SUMMARY_POLICY_DESCRIPTION)] = None,
    label: Annotated[str | None, Field(description=LABEL_DESCRIPTION)] = None,
    model_type: Annotated[str | None, Field(description=MODEL_TYPE_DESCRIPTION)] = None,
    handoff_from: Annotated[str | None, Field(description=HANDOFF_FROM_DESCRIPTION)] = None,
    whole_contract_path: Annotated[str | None, Field(description=WHOLE_CONTRACT_PATH_DESCRIPTION)] = None,
) -> dict[str, Any]:
    """`mode`ごとの入力を確かめ、同じ起動処理へ渡して委譲先turnを開始する。公開説明は`START_DESCRIPTION`が持つ。"""
    if handoff_from is not None:
        launch_requests.validate_handoff_inputs(
            handoff_from,
            cwd=cwd,
            mode=mode,
            subagent_md_path=subagent_md_path,
            extra_params=extra_params,
            prompt=prompt,
            command=command,
            summary_policy=summary_policy,
            label=label,
            model_type=model_type,
            whole_contract_path=whole_contract_path,
        )
        response = await _manager().start_handoff(handoff_from, model_type=model_type)
        public = public_start_response(response)
        public["cwd"] = response["cwd"]
        public["model_type"] = response["model_type"]
        return public
    mode = mode or "task"
    if cwd is None:
        launch_requests.require_start_cwd()
    assert cwd is not None
    warnings = model_override_warnings(model_type)
    validate_start_inputs(
        mode,
        subagent_md_path=subagent_md_path,
        extra_params=extra_params,
        prompt=prompt,
        command=command,
        summary_policy=summary_policy,
        model_type=model_type,
        whole_contract_path=whole_contract_path,
    )
    if mode == "task":
        assert subagent_md_path is not None
        params = extra_params or {}
        launcher = await run_file_operation(_manager().launcher_session_id)
        task_model_type, task_prompt, launch_kind, handoff_path = await run_file_operation(
            task_document_request,
            subagent_md_path,
            params,
            whole_contract_path=whole_contract_path,
            launcher_session_id=launcher,
            include_startup=False,
        )
        context = await run_file_operation(_manager().launcher_record_context)
        task_prompt = launch_requests.with_launcher_record(task_prompt, context)
        warnings = model_override_warnings(model_type, default_type=task_model_type)
        response = await _manager().start(
            model_type or task_model_type,
            task_prompt,
            cwd,
            launch_kind=launch_kind,
            label=resolve_display_label(label, task_document_label(subagent_md_path, params)),
            startup_document=launch_requests.resolve_task_document_path(subagent_md_path).resolve(),
            startup_inputs={**params, **({"引き継ぎ記録先": f"{handoff_path}（新規）"} if handoff_path else {})},
        )
        if warnings:
            response.setdefault("warnings", []).extend(warnings)
        if handoff_path is not None:
            public = public_start_response(response)
            public["handoff_record_path"] = str(handoff_path)
            return public
    elif mode == "delegate":
        assert prompt is not None and model_type is not None
        launcher = await run_file_operation(_manager().launcher_session_id)
        delegate_prompt, handoff_path = await run_file_operation(
            launch_requests.delegate_request,
            prompt,
            whole_contract_path=whole_contract_path,
            launcher_session_id=launcher,
        )
        response = await _manager().start(model_type, delegate_prompt, cwd, label=label)
        if warnings:
            response.setdefault("warnings", []).extend(warnings)
        public = public_start_response(response)
        public["handoff_record_path"] = str(handoff_path)
        return public
    elif mode == "explore":
        assert prompt is not None
        response = await _manager().start_explore(prompt, cwd, label=label, model_type=model_type)
    elif mode == "write":
        assert prompt is not None
        response = await _manager().start_write(prompt, cwd, label=label, model_type=model_type)
    else:
        assert command is not None and summary_policy is not None
        response = await _manager().start_shell(command, cwd, summary_policy, label=label, model_type=model_type)
    if mode != "task" and warnings:
        response.setdefault("warnings", []).extend(warnings)
    return public_start_response(response)


@mcp.tool(
    name="send_message",
    title="委譲sessionへの指示の追送",
    # 指示を追加で配送して新しいturnを始めうるが、既存の結果や会話は消さない。
    annotations=ToolAnnotations(destructive_hint=False),
    structured_output=True,
)
async def send_message(
    session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)],
    prompt: Annotated[
        str | None,
        Field(
            description=parameter_description(
                "委譲先へ渡す追加指示または次のturnの依頼本文。実行中turnへは訂正として、終了済みsessionへは新しい依頼として届く。"
                + DELEGATION_INPUT_GUIDANCE
            )
        ),
    ] = None,
    timeout: Annotated[
        float,
        Field(
            description=parameter_description(
                "継続要求の配送結果が確定するまでの待機上限秒数。"
                "固有のtimeout要件がなければ引数を省略して待機上限を270秒とする。"
                "委譲先の応答生成の完了は待たない。0以下は受理しない。"
            )
        ),
    ] = DEFAULT_SEND_MESSAGE_TIMEOUT,
    instruction: Annotated[
        str | None,
        Field(
            description=parameter_description(
                "役割文書が宣言する定型継続指示。extra_paramsと組み合わせ、promptと排他的に指定する。"
            )
        ),
    ] = None,
    extra_params: Annotated[
        dict[str, str] | None,
        Field(
            description=parameter_description(
                'instructionの宣言済み入力だけを渡す。例: instruction=再レビューせよ、extra_params={"round":"2"}。'
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """実行中turnへ追加指示を送り、終了済みなら同じsessionでreplyを開始する。

    入力は委譲先が他から得られない確定済み要件・事実と実行時の値に限定し、参照可能な記録は絶対パスと対象IDで示す。
    必須入力とユーザー指定の手段を保つ。手段の指定はユーザー指定か要件達成に必要と確定した根拠を伴う場合に限る。
    詳細はdelegation/references/base-contract.md「入力の適格性と追送」に従う。
    自由文はprompt、宣言された定型指示はinstructionと必要なextra_paramsで渡す。
    入力形式が不成立の場合は配送せず、next_actionに同じsessionへの再実行形と、確認できた役割宣言の候補を返す。

    引数を省略すると270秒を待機上限とする。固有のtimeout要件がなければ引数を省略する。
    待つのは継続要求の配送結果が確定するまでとする。委譲先の応答生成の完了は待たない。
    上限に達した場合は配送の成否が確定しないため、`atk agents wait`で状態を確認する。
    Claude・Codexの実行中turnにはsteerする。Antigravity・OpenCodeの実行中turnは受理しない。
    後者は`atk agents wait`で終了を観測して同じsessionへ`send_message`を再送する。
    終了済みturnでは結果回収を前提にせず同じsessionのreplyを開始する。
    子session・背景taskの完了待ちの保留中には待機対象を引き継いで新しいturnを開始し、古い待機表明を結果として返さない。
    保持期限を過ぎた場合と、sessionを所有する実行主体が終了している場合も、保持済みの最小状態から会話を暗黙に再開する。
    応答は`delivery`と、指示を配送したsessionが保持する担当名の`label`を含み、
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    `label`は起動時に確定した値であり、送信前に意図した担当へ配送したかを確かめる比較に使う。
    担当名を記録していない旧形式の登録簿から再開した場合は空文字列を返す。
    終了済みsessionの未回収の終了時の結果を消費して新しいturnを開始した場合は、
    その結果を`previous_result`（`status`・`agent_message`と、ある場合は`error`）で返す。
    消費した結果は`atk agents wait`で受領できないため、委譲元は同じ応答から受け取る。
    回収済みの結果は含めない。
    `delivery`の値は次のとおりである。
    `steered`は実行中turnの配送キューへ指示を投入したことだけを示し、委譲先が読んだことは示さない。
    委譲先が単一の長時間コマンドを実行している間はturnの区切りに達しないため、指示はキューに残る。
    到達は、`show`が返す`seconds_since_activity`と`active_tool_uses`の変化で判定する。
    `reply_started`は終了済みまたは完了待ちで保留中のsessionで新しいturnを開始したことを示す。
    `reply_failed`は新しいturnを開始できなかったこと、`reply_ambiguous`は開始の成否を確定できなかったことを示し、
    いずれも`atk agents wait`で状態を確認してから次の操作を選ぶ。
    sessionの起動後に工程別モデル設定の候補列が変わっても、保持したengine・model・effortで継続する。
    相対effort操作を受理した会話は、その変更を次turnから使う。
    採用済みのengineが実際に利用不能で継続できない場合は、backendが返す理由に従って回復手段を選ぶ。
    保持済みsessionを失って継続できない場合は`unknown session: <session_id>`を返す。
    """
    if instruction is None and extra_params is None:
        response = await _manager().send_message(session_id, prompt, timeout)
    else:
        response = await _manager().send_message(
            session_id, prompt, timeout, instruction=instruction, extra_params=extra_params
        )
    return public_send_message_response(response)


@mcp.tool(
    name="kill",
    title="実行中turnの中断",
    # 実行中turnを中断し、`stop=true`ではsessionも破棄する。
    annotations=ToolAnnotations(destructive_hint=True),
    structured_output=True,
)
async def kill(
    session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)],
    timeout: Annotated[
        float,
        Field(
            description=parameter_description(
                "中断要求後に終了を待つ上限秒数。"
                "固有のtimeout要件がなければ引数を省略して待機上限を270秒とする。"
                "0を指定すると、中断要求を配送した直後の状態を返す。"
            )
        ),
    ] = DEFAULT_KILL_TIMEOUT,
    stop: Annotated[
        bool,
        Field(description=parameter_description("終了時の結果を返した応答に限り、同じsessionを応答後に破棄する。")),
    ] = False,
) -> dict[str, Any]:
    """実行中turnへ中断を要求し、指定時間まで終了を待つ。

    停止は最終手段とする。実行中の委譲先には`send_message`で訂正を配送できるため、
    そちらで意図を満たせる場合は、停止によって失われる作業と再起動の費用の方が大きい。
    本ツールを選ぶ前に、`send_message`による訂正では足りないことと、その作業の継続自体が不要であることを確認する。
    利用上限の解除待ちのsessionは、解除後に同じsessionで自動的に続行する。上限への到達だけを停止の理由にしない。
    引数を省略すると270秒を待機上限とする。固有のtimeout要件がなければ引数を省略する。
    `timeout=0`を指定すると、中断要求を配送した直後の状態を返す。
    timeoutに達した場合もsessionとbackend processは破棄しないため、`atk agents wait`で状態を確認してから次の操作を選ぶ。
    終了時の結果の保持期限を過ぎたsessionでは中断する実行中turnが無いため、`status`へ`expired`、`kill_requested`へ`false`を設定した応答を返す。
    `show`の`result_held`が真のsessionでは、保留中の結果をそのまま終了時の結果として返し、以後の`send_message`を受け付ける。
    `show`の`ownership`が`owner_lost`のsessionでは、登録簿のpidと起動時刻が一致する委譲先CLIだけを停止し、
    停止を確かめてから`interrupted`を返す。所有していたagents_serverのプロセスは止めない。以後の`send_message`は同じ会話を再開する。
    """
    return await _manager().kill(session_id, timeout, stop)


@mcp.tool(
    name="stop",
    title="終了済みsessionの破棄",
    annotations=ToolAnnotations(destructive_hint=True),
    structured_output=True,
)
async def stop_session(session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)]) -> dict[str, Any]:
    """再開する予定の無い終了済みsessionを明示的に破棄する。

    statusLineの表示対象と`list`の応答から除き、backendがsession専用に保持する資源を解放する。
    実行中turnを持つsessionは破棄しない。中断が必要な場合は先に`kill`を発行する。
    破棄後も同じ`session_id`への`send_message`で会話を暗黙再開できる。
    成功時は空のオブジェクトを返し、失敗は例外で示す。
    """
    return await _manager().stop(session_id)


@mcp.tool(
    name="list",
    title="委譲sessionの一覧",
    # 内部で保持期限に達したsessionを失効させるが、保持期限のタイマーが同じ時刻に行う失効を先に反映するだけで、
    # 呼び出しの有無や回数によって観測できる状態は変わらない。Claude Codeは読み取り専用のツールを、
    # 実行済みの可能性がある接続断の後も再試行する。
    annotations=ToolAnnotations(read_only_hint=True),
    structured_output=True,
)
async def list_sessions(
    include_terminated: Annotated[
        bool,
        Field(
            description=parameter_description(
                "真のとき、未回収結果を持たない終了済みと`expired`のsessionも返す。除いた件数があるときだけ`omitted`へ返す。"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """保持中のsessionの状態を開始順に返す。

    所有する`root_session_id`を常に返す。
    各sessionの`session_id`・`status`と現在の`engine`・`model`・`effort`を返す。
    稼働中のsessionへ最終活動時刻からの経過秒数`seconds_since_activity`を加える。
    ClaudeとCodexのAPI再試行中は`api_error`に種別、HTTPステータスと経過秒を返す。
    Claudeの`api_retry`が示す次の再試行予定時刻は`retry_at`へ返す。
    Claude Codeの利用上限（Weekly limitと5時間）の解除待ちでは`api_error.type`が`usage_limit`となり、
    種類の`limit_type`と解除予定時刻の`resets_at`も返す。
    解除待ちのsessionは解除後に同じsessionで作業を続けるため、
    催促、巻き取りおよび別の候補での起動し直しの理由にしない。
    起動条件は`show`で取得する。
    結果本文は返さないため、終了の観測と結果の受領には`atk agents wait`を使う。
    表示範囲を指定しない場合は未回収結果を持たない終了済みまたは`expired`のsessionを除き、除いた件数を`omitted`へ返す。
    全件が必要な場合は`include_terminated`へ真を渡す。除いた件数が0なら`omitted`は省く。
    保持していた`session_id`を失った場合の回復と、並行する委譲先の残作業の把握へ用いる。
    照会したサーバーの起動単位を`queried_server_instance`へ、各sessionを所有するサーバーの起動単位を`owner_server_instance`へ返す。
    共有管理サービスでは退出していない世代を`server_generations`へ返し、受付中の世代（`current`）と照会した世代（`queried`）を示す。
    """
    response = await _manager().list_sessions(include_terminated=include_terminated)
    response["queried_server_instance"] = session_registry.owner_server_instance()
    if _SERVICE_MANAGERS is not None and (generations := await _SERVICE_MANAGERS.generations()):
        # 退出していない世代が複数あれば更新中か旧世代の残存であり、sessionの所有者を確かめる手掛かりにする。
        response["server_generations"] = generations
    return response


@mcp.tool(
    name="show",
    title="委譲sessionの詳細",
    # `list`と同じく、期限に達したsessionの失効を先に反映するだけである。
    annotations=ToolAnnotations(read_only_hint=True),
    structured_output=True,
)
async def show_session(
    session_id: Annotated[str, Field(description=SESSION_ID_DESCRIPTION)],
    verbose: Annotated[
        bool,
        Field(
            description=parameter_description(
                "真のとき、engine、model、effort、開始・更新時刻、turn番号および解決可能なroot sessionも返す。"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """1件のsessionについて、文脈復旧またはトラブルシューティング用の詳細を返す。

    `verbose`を指定しない場合は識別名、起動prompt、cwd、種別、model_type、status、結果の有無および進行中の停滞診断を返す。
    `model_type`は工程別設定の種別名、または起動ツールの`model_type`へ渡した候補列である。
    停滞診断の`seconds_since_activity`はツール呼び出しを含む最後の活動からの経過秒数であり、停滞の可能性はこの値で判定する。
    ClaudeとCodexのAPI再試行中は`api_error`に種別、HTTPステータスと経過秒を返す。
    Claudeの`api_retry`が示す次の再試行予定時刻は`retry_at`へ返す。
    Claude Codeの利用上限（Weekly limitと5時間）の解除待ちでは`api_error.type`が`usage_limit`となり、
    種類の`limit_type`と解除予定時刻の`resets_at`も返す。
    解除待ちのsessionは解除後に同じsessionで作業を続けるため、
    催促、巻き取りおよび別の候補での起動し直しの理由にしない。
    `status`が`running`で未完了のツール呼び出しがある場合は、`active_tool_uses`へ各呼び出しの種別、開始時刻および入力の要約を返す。
    前回の照会と同じ呼び出しが同じ開始時刻で続いている場合も、長時間のコマンドの実行中として扱う。
    `status`が`running`で、このsessionが`start`で起動し終了をまだ観測していない子sessionがある場合は、
    安定した順序の`live_child_sessions`（`session_id`と`cwd`の対）を返す。
    この一覧は子の終了を観測するまで残るため、子が稼働中である根拠にしない。
    子の状態は、子の`session_id`を渡した`show`の`status`と`seconds_since_activity`で判定する。
    `cwd`を解決できない識別子は`live_child_session_ids_without_cwd`へ分けて返し、その識別子へは追送と打ち切りを発行できない。
    `result_held`が真のsessionは、委譲先のモデルのturnが終わり、
    バックグラウンドタスクまたは子sessionの終了か利用上限の解除を待って結果を保留している。
    活動が止まるため`seconds_since_activity`が増えても停滞を意味しない。追跡中のバックグラウンドタスクは`live_background_tasks`
    （`task_id`・`task_type`・`description`・`seconds_since_start`）で返す。
    バックグラウンドタスクの後の結果が不要なら`kill`で保留中の結果を受け取れる。
    完了通知が届かない保留は、背景実行のBashの上限（Claude Codeで環境変数を設定しない場合は2時間）に余裕を加えた期限で打ち切る。
    子session・バックグラウンドタスクの完了待ちの保留は追送では確定しない。
    打ち切り、`kill`、または期限を持たない利用上限の解除待ち・過負荷の継続待ちの保留への追送で確定した時点で
    待機対象が残った結果は`error`の`heldResultFinalized`が真で、再開したturnの結果ではない。残ったバックグラウンドタスクは`unfinishedBackgroundTasks`、子sessionは
    `unobservedSessions`に識別子を持つ。`unobservedSessions`だけでは保留の確定と区別できない。
    終了したsessionが空でない`error`を保持する場合は、`error`を返す。失敗の原因と次の操作の判断に使える。
    共有の登録簿だけから復元したsessionは`error`を保持しないため返さず、原因は`atk agents wait`の終了行で受け取る。
    ただし所有するagents_serverが終了を公開せずに終了し、turnを実行するプロセスも存在しないsessionは、
    `status`を`interrupted`とし、`error`の`interruptionReason`が`owner_processes_exited`を示す。
    所有するagents_serverの停止で中断したturnは`interruptionReason`が`owner_server_stopped`を示す。
    所有するagents_serverが状態を公開しなくなった後も委譲先CLIが生存するsessionは`status`を`running`とし、
    `ownership`へ`owner_lost`、`recorded_processes`、`owner_root_session_id`、`stranded_notice_count`と
    終了を待つか`kill`の後に`send_message`する`next_action`を返す。旧ルートに残った通知は`notices`で返す。
    中断の`error`は`diagnostics`へ、そのturnを所有したサーバー起動単位の終了診断への参照を持つ。
    `diagnostics.status`は`collected`（終了の記録あり）、`incomplete`（記録はあるが終了を示す記録が無く原因を判断できない）、
    `not_collected`（終了診断の導入前の記録）、`missing`、`unreadable`のいずれかで、`next_action`が読み取り操作を示す。
    過去に中断したturnがあるsessionは、再開・解放の後も`interrupted_turns`へturn番号・理由・`diagnostics`の組を返す。
    `verbose=True`はengine、model、effort、開始・更新時刻、turn番号および解決可能なroot sessionも加える。
    終了時の結果本文は返さないため、受領には`atk agents wait`を使う。
    """
    await _manager().take_over_orphaned_session(session_id)
    return await _manager().show_session(session_id, verbose=verbose)


@mcp.tool(name="send_to_user", title="ユーザー画面への本文表示", annotations=ToolAnnotations(destructive_hint=False))
async def send_to_user(
    message: Annotated[str, Field(description="画面へそのまま表示する日本語のMarkdown本文")],
) -> dict[str, Any]:
    """メイン専用。会話内modが表示を確認した場合だけ成功を返す。表示後の本文は再掲しない。"""
    caller = runtime_environment.current()
    if _UI_DELEGATED if caller is None else delegated_session.is_delegated(caller.environment):
        raise ValueError("メイン専用ツール。結果本文は委譲元へ最終応答で返せ。")
    if response_language_check.check_text(message)[0] is response_language_check.CheckOutcome.WARN:
        raise ValueError("本文を日本語で書き直すまで画面へ表示していない。")
    try:
        guidance = ""
        if runtime_inserted.reported_improvement_lines(message) or runtime_inserted.report_cues(message):
            root = pathlib.Path(__file__).resolve().parents[2]
            checked = await run_file_operation(
                subprocess.run,
                [sys.executable, str(root / "scripts/launch.py"), "agent_toolkit/hook.py", "improvement_status_advisor"],
                input=json.dumps({"session_id": _manager().launcher_session_id(), "last_assistant_message": message}),
                capture_output=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )
            if checked.returncode != 0:
                raise ValueError("報告の案内を取得するhookが失敗した")
            guidance = json.loads(checked.stdout)["hookSpecificOutput"]["additionalContext"]
    except Exception:  # noqa: BLE001 -- 補助案内の失敗で本文の送達を止めない
        _LOG.exception("報告の取得案内を作成できない")
        guidance = (
            "報告の判定資料を確認できなかった。改善点はshare/rules/rules-main.mdの作業中の改善点の報告を、"
            "逸脱の報告はagent-basics:bugfixを確認せよ。"
        )
    # 再開後の画面で、同じ本文を持つ別の呼出しの表示を取り違えないよう、呼出しの識別子を添える。
    tool_use_id = _TOOL_USE_ID.get()
    result = await _ui_request("send_to_user", {"message": message, **({"tool_use_id": tool_use_id} if tool_use_id else {})})
    result["guidance"] = guidance
    return result


@mcp.tool(name="permissions", title="操作権限の状態・要求・取消", annotations=ToolAnnotations(destructive_hint=True))
async def permissions(
    action: Annotated[
        Literal["list", "get", "prepare", "adopt", "revoke"],
        Field(description="一覧・詳細取得・要求準備・所有子からの引継ぎ・取消を選ぶ。承認は書き込まない"),
    ] = "list",
    request_id: Annotated[str | None, Field(description="list以外で対象とする要求ID")] = None,
    source_session: Annotated[
        str | None, Field(description="adoptで拒否を引き継ぐ、現在のManagerが所有する子のsession ID")
    ] = None,
    scope: Annotated[str | None, Field(description="prepareで表示する試行範囲。onceまたはcount:N")] = None,
) -> dict[str, Any]:
    """listで入力本文を除く一覧、getとrequest_idで1要求の全文を得る。prepare/adopt/revokeは要求の準備・子からの引継ぎ・取消。承認や回答は書き込まない。scopeはonceかcount:N。"""
    manager = _manager()
    identifier = manager.launcher_session_id()
    if identifier is None:
        raise ValueError("呼出元会話を解決できない")
    if action == "list":
        if any(value is not None for value in (request_id, source_session, scope)):
            raise ValueError("listはaction以外の入力を受け取らない")
        return await run_file_operation(operation_permissions.permission_status, identifier)
    if not request_id:
        raise ValueError("request_idが必要")
    if action != "prepare" and scope is not None:
        raise ValueError("scopeはprepareだけに指定する")
    if action != "adopt" and source_session is not None:
        raise ValueError("source_sessionはadoptだけに指定する")
    if action == "get":
        return await run_file_operation(operation_permissions.permission_status, identifier, request_id=request_id)
    if action == "prepare":
        request = await run_file_operation(
            operation_permissions.prepare_request,
            identifier,
            request_id,
            scope="once" if scope is None else scope,
        )
    elif action == "adopt":
        owned = await manager.list_sessions(include_terminated=True)
        if source_session is None or not any(child["session_id"] == source_session for child in owned["sessions"]):
            raise ValueError("source_sessionが現在のManagerの所有する子ではない")
        request = await run_file_operation(operation_permissions.adopt_request, identifier, source_session, request_id)
    elif action == "revoke":
        return await run_file_operation(operation_permissions.revoke_permission, identifier, request_id)
    else:
        raise ValueError("actionはlist、get、prepare、adopt、revokeだけを指定する")
    if request is None:
        raise ValueError("要求が存在しないか、指定した範囲で準備・引継ぎできない")
    display = request_display(request)
    status = await run_file_operation(operation_permissions.permission_status, identifier, request_id=request["request_id"])
    if status["legacy_answers"]:
        display += "\n既存UWIの回答原文と出所:\n" + json.dumps(status["legacy_answers"], ensure_ascii=False, indent=2)
    return {"request": request, "display": display}


@mcp.tool(name="compact_conversation", title="会話圧縮の予約", annotations=ToolAnnotations(destructive_hint=False))
async def compact_conversation() -> dict[str, Any]:
    """メイン専用。会話圧縮を予約する。受付後はターンを終え、ホストの圧縮結果を待つ。受付は完了ではない。"""
    return await _ui_request("compact_conversation", {})


@mcp.tool(name="apply_runtime_changes", title="同一会話への変更反映予約", annotations=ToolAnnotations(destructive_hint=False))
async def apply_runtime_changes(
    targets: Annotated[list[str], Field(min_length=1, description="plugins、agents_server、pyfltrから重複せず指定")],
) -> dict[str, Any]:
    """メイン専用。ターン完了後のプラグイン再読込・MCP再接続を予約する。受付は反映完了ではない。agents_serverの再接続は子の実行中turnを中断し、既存の復旧で同じ会話を再開する。"""
    if len(set(targets)) != len(targets) or not set(targets) <= {"plugins", "agents_server", "pyfltr"}:
        raise ValueError("targetsはplugins、agents_server、pyfltrから重複せず指定する")
    return await _ui_request("apply_runtime_changes", {"targets": targets})


def _prepare_child_environment() -> None:
    """起動元の仮想環境と会話contextを、以降に起動する委譲先から取り除く。

    Claude backendが渡す`ClaudeAgentOptions.env`は継承環境へ重なる仕様であり、
    キーの削除を表現できない。Codex backendのApp Server子プロセスも本プロセスの環境を継承する。
    このため両方の処理を起動する本プロセスの環境を、起動時に1回だけ整える。
    """
    launch_requests.capture_handoff_session()
    child_environment.prepare(os.environ)
    _inherited_venv.strip_inherited_venv(os.environ)
    mcp_environment.strip_conversation_context(os.environ)
    # 起動ランチャーとの合流ファイルはこのプロセスだけが使い、委譲先の内側のagents_serverへ継承させない。
    os.environ.pop(logging_config.LAUNCH_RECORD_ENV, None)


def _configure_logging() -> pathlib.Path:
    """標準エラーと永続ファイルへagents_serverの診断ログを出力する。"""
    return logging_config.configure_logging()


def serve(*, check_dependencies: bool = False) -> int:
    """診断や初期化が停止しても起動単位の有限回収を維持する。"""
    if check_dependencies:
        return _serve(check_dependencies=True)
    guard = lifetime.ServerLifetime()
    guard.start()
    mcp.server_lifetime = guard
    try:
        try:
            _serve()
        except BaseException as error:
            # signal終了の状態は保持し、EOF等の取消しでは終了処理と無関係な実装例外を隠さない。
            if guard.exitcode == 0:
                if isinstance(error, BaseExceptionGroup):
                    _, remaining = error.split(asyncio.CancelledError)
                    if remaining is not None:
                        raise
                elif not isinstance(error, asyncio.CancelledError):
                    raise
            if not guard.stopping:
                raise
        return guard.exitcode
    finally:
        guard.request_stop()
        guard.finish()
        mcp.server_lifetime = None


def _serve(*, check_dependencies: bool = False) -> int:
    """委譲先の環境を整え、依存の確認またはMCP stdio transportの起動を行う。

    stdio起動では起動単位を開始し、正常・例外の終了を起動単位の終了診断へ記録する。
    """
    environment = dict(os.environ)
    _prepare_child_environment()
    log_path = _configure_logging()
    mode = "check-dependencies" if check_dependencies else "stdio"
    instance = None if check_dependencies else logging_config.start_server_instance(environment=environment)
    if instance is not None:
        session_registry.set_owner_server_instance(instance.instance_id)
    _LOG.info(
        "agents_serverを起動します: mode=%s log=%s server_instance=%s",
        mode,
        log_path,
        instance.instance_id if instance is not None else "none",
    )
    try:
        if check_dependencies:
            claude_backend.check_dependencies()
        else:
            mcp.run(transport="stdio")
    except BaseException as exc:
        _LOG.exception("agents_serverが異常終了しました: mode=%s", mode)
        logging_config.record_instance_event(
            "exited",
            outcome="exception",
            exception_type=type(exc).__name__,
            message=logging_config.redact_secrets(str(exc))[: logging_config.MESSAGE_LIMIT],
        )
        _end_server_instance()
        logging_config.close_logging()
        raise
    _LOG.info("agents_serverが正常終了しました: mode=%s", mode)
    logging_config.record_instance_event("exited", outcome="normal", exception_type=None, message=None)
    _end_server_instance()
    logging_config.close_logging()
    return 0


def _end_server_instance() -> None:
    """起動単位を終え、同じプロセスで続く処理（テストなど）が終えた起動単位を記録へ書かないようにする。"""
    logging_config.end_server_instance()
    session_registry.set_owner_server_instance(None)
