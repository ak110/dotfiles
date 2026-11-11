"""Claude SDKの長命受信タスクと終了処理。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import asyncio
import contextlib
import datetime
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from agent_toolkit._agents_server import (
    claude_retry_record,
    claude_settings,
    configured_model,
    resume_waits,
    session_errors,
    session_identity,
    wait_compaction,
    wait_output_tracking,
)
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.claude_channels import _Command, _CommandChannel, _ContextUsageObserver, _DeliveryResult
from agent_toolkit._agents_server.claude_diagnostics import _InitializationDiagnostic, _message_name, _owned_runtime_processes
from agent_toolkit._agents_server.claude_messages import consume_assistant_message, recover_api_retry
from agent_toolkit._agents_server.claude_plugins import ClaudePluginReloader
from agent_toolkit._agents_server.claude_turns import ClaudeTurns
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.session_errors import SessionOwnerGoneError
from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, ResumePrompt, SessionState
from agent_toolkit._atk import claude_connection
from agent_toolkit._common import claude_usage_limit, process_tree

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")


async def _raise_changed_model(session: SessionState, status: object) -> None:
    """拒否された利用枠がある429だけを、現在設定への引き継ぎに変換する。"""
    if (
        status == 429
        and not session.interrupt_requested
        and session.usage_limit is not None
        and session.usage_limit.status == "rejected"
        and (changed := await configured_model.changed_model_error(session)) is not None
    ):
        raise configured_model.ConfiguredModelChanged(changed)


async def _apply_api_retry(session: SessionState, data: dict[str, Any], started_at: str) -> None:
    """SDK通知に無い利用枠を同じ試行の記録から補い、再試行の状態を更新する。"""
    if session.usage_limit is None or not session.usage_limit.is_wait_target:
        limit = await run_file_operation(claude_retry_record.retry_usage_limit, session.session_id, data, started_at=started_at)
        if limit is not None:
            session.usage_limit = limit
    await _raise_changed_model(session, data.get("error_status"))
    recover_api_retry(session, data)


async def _consume_rate_limit(message: Any, session: SessionState | None) -> claude_usage_limit.UsageLimitState | None:
    """利用枠の更新と、既に429を受けた会話の再試行状態を反映する。"""
    limit = claude_usage_limit.from_event(message)
    if limit is not None and session is not None:
        session.usage_limit = limit
        if (
            limit.status == "rejected"
            and not session.interrupt_requested
            and session.api_error is not None
            and session.api_error.get("http_status") == 429
        ):
            await _raise_changed_model(session, 429)
            recover_api_retry(session, {"error_status": 429}, record_notification=False)
    return limit


class ClaudeStream(ClaudeTurns):
    """Claude SDKの受信タスクを所有する。"""

    async def _receive_failed(
        self,
        session: SessionState | None,
        error: BaseException,
        diagnostic: _InitializationDiagnostic,
        initialized: asyncio.Future[SessionState],
    ) -> None:
        """初期化の待機と終了時の失敗結果を、同じ受信失敗から確定する。"""
        diagnostic.record_exception(error)
        if session is None:
            if not initialized.done():
                initialized.set_exception(error)
        else:
            self._record_failure(session, error, diagnostic=diagnostic)
            if not initialized.done():
                initialized.set_result(session)
            await self._notify_waiters()

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
        request_prompt: str | None = None,
        result_contract_recovery_consumed: bool = False,
        existing_session: SessionState | None = None,
    ) -> None:
        from claude_agent_sdk import TERMINAL_TASK_STATUSES

        client: Any = None
        usage_observer: _ContextUsageObserver | None = None
        wait_compactor: wait_compaction.WaitCompaction | None = None
        session: SessionState | None = None
        channel = _CommandChannel()
        iterator: Any = None
        message_task: asyncio.Task[Any] | None = None
        command_task: asyncio.Task[Any] | None = None
        plugin_change_task: asyncio.Task[None] | None = None
        reloader = ClaudePluginReloader(vars(options), asyncio.get_running_loop())
        retrieved: _Command | None = None
        active_future: asyncio.Future[_DeliveryResult] | None = None
        # `SystemMessage(init)`より前に届いた利用枠の報告。sessionの作成時に引き継ぐ。
        early_usage_limit: claude_usage_limit.UsageLimitState | None = None
        early_api_retry: dict[str, Any] | None = None
        # `SystemMessage(init)`より前に届いたturn状態の報告。CLIはコマンドループの先頭で最初のturnの`running`を
        # 発行するため、initより先に届き得る。捨てると最初のturnの判定が追跡集合だけの方法へ戻るため、
        # sessionの作成時に引き継ぐ。
        early_cli_turn_state: str | None = None
        runtime_started_at = shared_state.utc_now()

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
                prompt=request_prompt or "",
                result_contract_recovery_consumed=result_contract_recovery_consumed,
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
                await _apply_api_retry(session, early_api_retry, runtime_started_at)
            session.touch()
            self._channels[session_id] = channel
            current_task = asyncio.current_task()
            if current_task is not None:
                self._task_sessions[current_task] = session_id
            if not initialized.done():
                initialized.set_result(session)

        try:
            await reloader.prepare()
            plugin_change_task = asyncio.create_task(self._watch_plugin_changes(reloader, channel))
            diagnostic.stage = "creating_client"
            client = self._client_factory(options)
            usage_observer = _ContextUsageObserver(client)
            # 使用量APIの解決を待機時まで遅らせ、未対応SDKでも通常の起動・結果回収を保つ。
            wait_compactor = wait_compaction.WaitCompaction(
                lambda: client.get_context_usage(),  # pylint: disable=unnecessary-lambda
                lambda key: channel.send("compact", key),
            )
            diagnostic.stage = "connecting"
            await client.connect()
            diagnostic.capture_client(client)
            diagnostic.stage = "connected"
            query_with_resources = functools.partial(self._query_with_resources, client, options, reloader, cwd)
            if isinstance(prompt, ResumePrompt):
                await prompt.deliver(query_with_resources)
            else:
                await query_with_resources(prompt)
            diagnostic.stage = "query_sent"
            iterator = aiter(client.receive_messages())
            diagnostic.stage = "receiving_messages"
            while True:
                await reloader.reload_if_idle(client, session)
                if session is not None:
                    wait_compactor.observe(session)
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
                        iterator = await self._handle_command(
                            client, session, command, iterator, query=query_with_resources, reloader=reloader
                        )
                        active_future = None
                        continue
                pending = {task for task in (message_task, command_task, plugin_change_task) if task is not None}
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

                if plugin_change_task is not None and plugin_change_task in done:
                    await plugin_change_task

                if message_task is not None and message_task in done:
                    completed_message_task = message_task
                    message_task = None
                    try:
                        message = completed_message_task.result()
                    except StopAsyncIteration:
                        if (
                            session is not None
                            and resume_waits.is_background_completion_wait(session)
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
                                and resume_waits.is_background_completion_wait(session)
                                and not resume_waits.has_pending_auto_resume_targets(session)
                            ):
                                (await self._finalize_pending_result(session))
                                await self._notify_waiters()
                        elif name == "SystemMessage" and getattr(message, "subtype", None) == "api_retry":
                            data = getattr(message, "data", {})
                            if isinstance(data, dict):
                                if session is None:
                                    early_api_retry = data
                                else:
                                    await _apply_api_retry(session, data, runtime_started_at)
                                    await self._notify_waiters()
                        elif name == "RateLimitEvent":
                            # 利用枠の報告はモデル活動ではないため活動時刻を進めない。
                            usage_limit = await _consume_rate_limit(message, session)
                            if session is None:
                                early_usage_limit = usage_limit or early_usage_limit
                            elif usage_limit is not None:
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
                                session.model_output_observed = True
                                session.record_turn_started()
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
                            await self._consume_result_message(session, message)
                            await self._notify_waiters()

        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._receive_failed(session, exc, diagnostic, initialized)
        finally:

            async def cleanup_runtime(pins: process_tree.ProcessIdentityPins) -> None:
                nonlocal retrieved
                if wait_compactor is not None:
                    await wait_compactor.close()
                if diagnostic.mcp_recovery_task is not None:
                    if not diagnostic.mcp_recovery_task.done():
                        diagnostic.mcp_recovery_task.cancel()
                    await asyncio.gather(diagnostic.mcp_recovery_task, return_exceptions=True)
                for task in (message_task, command_task, plugin_change_task):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (message_task, command_task, plugin_change_task) if task is not None),
                    return_exceptions=True,
                )
                if command_task is not None and not command_task.cancelled() and command_task.exception() is None:
                    retrieved = command_task.result()
                if retrieved is not None and not retrieved[2].done():
                    retrieved[2].set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))
                if active_future is not None and not active_future.done():
                    active_future.set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))
                if client is not None:
                    try:
                        try:
                            # 最後のyieldで停止した受信列も、所有タスクの終了時に閉じる。
                            close_iterator = getattr(iterator, "aclose", None)
                            if callable(close_iterator):
                                # Pylintは動的取得したacloseのCallableへのcastを呼出不能と誤推論する。
                                # pylint: disable-next=not-callable
                                await cast(Callable[[], Awaitable[None]], close_iterator)()
                        finally:
                            if usage_observer is not None:
                                await usage_observer.close()
                    finally:
                        if diagnostic.child_process is None:
                            diagnostic.capture_client(client)
                        # CLIの正常終了を妨げず、切断中に観測した子孫のhandleを保持する。
                        # POSIXで列挙間隔内に生成と親終了が完結した子孫は追跡できない。
                        descendants = [diagnostic.child_process] if diagnostic.child_process is not None else []
                        tracking_done = asyncio.Event()

                        async def track_descendants() -> None:
                            nonlocal descendants
                            while not tracking_done.is_set():
                                descendants = await run_file_operation(
                                    _owned_runtime_processes, diagnostic.child_process, tuple(descendants), pins=pins
                                )
                                with contextlib.suppress(TimeoutError):
                                    await asyncio.wait_for(tracking_done.wait(), timeout=0.05)

                        try:
                            descendants = await run_file_operation(
                                _owned_runtime_processes, diagnostic.child_process, pins=pins
                            )
                        finally:
                            tracker = asyncio.create_task(track_descendants())
                            try:
                                await self._disconnect_client(client)
                            finally:
                                tracking_done.set()
                                try:
                                    await tracker
                                finally:
                                    runtime_key = (
                                        session.session_id if session is not None else f"initialization:{diagnostic.debug_file}"
                                    )
                                    if descendants:
                                        self._residual_processes[runtime_key] = descendants
                                    # 導入一覧の取得で回収できなかった子孫もCLIの子孫と同じ所有対象として保持する。
                                    self._adopt_unreclaimed(runtime_key, reloader)
                                    await self._reclaim_runtime(runtime_key, pins=pins)
                elif reloader.unreclaimed:
                    # 接続前の入力準備で回収できなかった子孫は、クライアントが無く接続の後始末を実行しないため、
                    # ここで所有対象へ移す。
                    runtime_key = session.session_id if session is not None else f"initialization:{diagnostic.debug_file}"
                    self._adopt_unreclaimed(runtime_key, reloader)
                    await self._reclaim_runtime(runtime_key, pins=pins)

            async def cleanup() -> None:
                async def close_plugin_input() -> None:
                    runtime_key = session.session_id if session is not None else f"initialization:{diagnostic.debug_file}"
                    await reloader.close(retain_input=runtime_key in self._residual_processes)

                async with contextlib.AsyncExitStack() as resources:
                    resources.push_async_callback(close_plugin_input)
                    resources.push_async_callback(run_file_operation, claude_settings.cleanup_settings, options)
                    resources.callback(channel.close)
                    if session is not None:
                        resources.callback(self._channels.pop, session.session_id, None)
                        resources.push_async_callback(session.drain_registration)
                    await cleanup_runtime(resources.enter_context(process_tree.ProcessIdentityPins()))

            await finish_task(asyncio.create_task(cleanup()))
