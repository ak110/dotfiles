"""agents_serverのテストが共有する偽のbackend・クライアントと補助。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import pathlib
import typing
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import (
    backends,
    launch_requests,
    mcp_tools,
    resume_waits,
    session_errors,
    session_registry,
    shared_layout,
    shared_roots,
    state,
    status_file,
)
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError


def install_backend(manager: server_manager.AgentsServerManager, engine: str, backend: object) -> None:
    """テスト用のbackendを、managerがengineに使うbackendとして保持させる。"""
    manager._backends[engine] = typing.cast(backends.Backend, backend)  # pylint: disable=protected-access


_FORBIDDEN_PUBLIC_KEYS = {"turn_id", "result_available"}


_REAL_PLUGIN_PREFLIGHT = launch_requests._check_plugin_commands_sync


_REAL_PLUGIN_PREFLIGHT_ASYNC = launch_requests.check_plugin_commands


def _use_real_plugin_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """事前確認を実装どおりに実行させる。"""
    monkeypatch.setattr(launch_requests, "check_plugin_commands", _REAL_PLUGIN_PREFLIGHT_ASYNC)


def _assert_no_forbidden_keys(value: Any) -> None:
    """応答と入れ子のprevious_resultから内部状態キーを除外する。"""
    if isinstance(value, dict):
        assert _FORBIDDEN_PUBLIC_KEYS.isdisjoint(value)
        for nested in value.values():
            _assert_no_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_forbidden_keys(nested)


def _actionable_message(error: BaseException) -> str:
    """共通の例外型であることを確かめ、ツールのエラー本文として届く理由と次の操作の2行を返す。"""
    assert isinstance(error, ActionableError)
    assert f"\n{NEXT_ACTION_PREFIX}" in error.message
    return error.message


def _complete(session: state.SessionState, *, message: str = "完了", error: Any = None) -> None:
    """テスト用sessionを結果取得可能な終端状態へ進める。"""
    session.status = "failed" if error is not None else "completed"
    session.agent_message = message
    session.error = error
    session.turn_completed = True
    session.turn_start_ambiguous = False
    session.touch()


def _default_identity_fields(engine: str = "codex") -> dict[str, object]:
    """起動候補だけが確定したsessionの公開identity項目を返す。"""
    return {
        "engine": engine,
        "model": None,
        "effort": None,
        "model_type": None,
        "launch_identity": {
            "engine": engine,
            "model": None,
            "effort": None,
            "source": "launch_candidate",
        },
        "observed_identity": None,
    }


def _write_notice(directory: pathlib.Path, session_id: str, sequence: int, sent_at: str, body: str) -> None:
    """待機テスト用の未回収通知を保存する。"""
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "session_id": session_id, "sent_at": sent_at, "body": body}
    (directory / f"{session_id}.{sequence}.json").write_text(json.dumps(payload), encoding="utf-8")


class FakeBackend:
    """共有MCP層の契約だけを検証するバックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str, delivery: str = "reply_started") -> None:
        self.sessions = sessions
        self.engine = engine
        self.delivery = delivery
        self.interrupt_calls = 0
        self.send_calls = 0
        self.resume_calls: list[str] = []
        self.release_calls: list[str] = []
        self.start_calls: list[tuple[str | None, str | None, str]] = []
        self.prompts: list[str] = []
        # 実backendと同じく、再開で再生成したsessionを登録簿へ公開させる場合に真にする。
        self.publish_registry = False

    async def list_models(self) -> list[dict[str, Any]]:
        """系列の候補を指定しない場合も、起動せずに解決できる一覧を返す。"""
        return [
            {
                "model": model,
                "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in efforts],
            }
            for model, efforts in (
                ("gpt-6-sol", ("medium", "high")),
                ("gpt-6-luna", ("medium", "xhigh")),
                ("gpt-5.6-terra", ("medium", "high")),
                ("gpt-6-astra", ("medium", "high")),
            )
        ]

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        model_type: str | None = None,
        launch_kind: state.LaunchKind = "delegate",
        excluded_candidates: frozenset[state.ModelCandidate] = frozenset(),
    ) -> state.SessionState:
        self.prompts.append(prompt)
        self.start_calls.append((model, effort, launch_kind))
        session_number = len(self.start_calls)
        session = state.SessionState(
            session_id=f"{self.engine}-session" if session_number == 1 else f"{self.engine}-session-{session_number}",
            cwd=cwd,
            model=model,
            effort=effort,
            engine=self.engine,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            turn_seq=1,
        )
        self.sessions[session.session_id] = session
        state.initialize_turn(session)
        return session

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        model_type: str | None = None,
        launch_kind: state.LaunchKind = "delegate",
        excluded_candidates: frozenset[state.ModelCandidate] = frozenset(),
        turn_seq: int = 0,
        fast_mode: bool | None = None,
    ) -> state.SessionState:
        async def accept_prompt(value: str) -> None:
            del value

        self.resume_calls.append(session_id)
        session = state.SessionState(
            session_id=session_id,
            cwd=cwd,
            model=model,
            effort=effort,
            engine=self.engine,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            turn_seq=turn_seq + 1,
            fast_mode=fast_mode,
            publish_registry=self.publish_registry,
        )
        self.sessions[session_id] = session
        state.initialize_turn(session)
        if self.publish_registry:
            # 実backendは起動情報を写される前に、再生成したsessionの状態を公開する。
            session.touch()
        await prompt.deliver(accept_prompt)
        return session

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.send_calls += 1
        if resume_waits.HeldTurn.capture(session) is not None:
            state.begin_reply(session, preserve_waits=True)
            return {"delivery": self.delivery}
        if session.terminal:
            previous = session.previous_result()
            state.begin_reply(session)
            return {"delivery": self.delivery, "previous_result": previous}
        return {"delivery": "steered"}

    async def interrupt(self, session: state.SessionState) -> None:
        del session
        self.interrupt_calls += 1

    async def release_session(self, session: state.SessionState) -> None:
        self.release_calls.append(session.session_id)

    async def close(self) -> None:
        """バックエンド終了処理のダミー。"""


class UnavailableStartBackend(FakeBackend):
    """起動応答の前にengineの可用性で終端する偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        engine: str,
        error: Any = None,
    ) -> None:
        super().__init__(sessions, engine)
        self.error = error if error is not None else {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        _complete(session, message="", error=self.error)
        return session


class DelayedUnavailableBackend(FakeBackend):
    """起動応答を返した後にengineの可用性で終端する偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        engine: str,
        condition: asyncio.Condition,
        delay: float = 0.0,
        error: Any = None,
    ) -> None:
        super().__init__(sessions, engine)
        self._condition = condition
        self._delay = delay
        self.error = error if error is not None else {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
        self.pending: list[asyncio.Task[None]] = []

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        self.pending.append(asyncio.create_task(self._fail_after_response(session)))
        return session

    async def _fail_after_response(self, session: state.SessionState) -> None:
        await asyncio.sleep(self._delay)
        _complete(session, message="", error=self.error)
        async with self._condition:
            self._condition.notify_all()


class OutputObservedBackend(FakeBackend):
    """起動応答を返した後にモデル出力を受信し、終端しないまま実行を続ける偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str, condition: asyncio.Condition) -> None:
        super().__init__(sessions, engine)
        self._condition = condition
        self.pending: list[asyncio.Task[None]] = []

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        self.pending.append(asyncio.create_task(self._observe_output(session)))
        return session

    async def _observe_output(self, session: state.SessionState) -> None:
        await asyncio.sleep(0.01)
        session.model_output_observed = True
        async with self._condition:
            self._condition.notify_all()


class BlockingInterruptBackend(FakeBackend):
    """中断要求の配送を解除イベントまで停止する偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.interrupt_started = asyncio.Event()
        self.release_interrupt = asyncio.Event()

    async def interrupt(self, session: state.SessionState) -> None:
        del session
        self.interrupt_calls += 1
        self.interrupt_started.set()
        await self.release_interrupt.wait()


class BlockingResumeBackend(FakeBackend):
    """保存済みsessionの再開を解除イベントまで停止する偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.resume_started = asyncio.Event()
        self.resume_finished = asyncio.Event()
        self.release_resume = asyncio.Event()

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        **kwargs: Any,
    ) -> state.SessionState:
        self.resume_started.set()
        try:
            await self.release_resume.wait()
            return await super().resume(session_id, prompt, cwd, model, effort, **kwargs)
        finally:
            self.resume_finished.set()


class FailedResumeBackend(FakeBackend):
    """再開turnを結果本文付きの失敗として確定する偽バックエンド。"""

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        **kwargs: Any,
    ) -> state.SessionState:
        session = await super().resume(session_id, prompt, cwd, model, effort, **kwargs)
        _complete(session, message="reply失敗結果", error={"message": "reply failed"})
        return session


class ConcurrentOwnerGoneBackend(FakeBackend):
    """2件の継続要求へ同時に所有主体終了を返す偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.owner_gone_session: state.SessionState | None = None
        self.send_started = 0
        self.both_started = asyncio.Event()

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, Any]:
        if session is self.owner_gone_session:
            self.send_started += 1
            if self.send_started == 2:
                self.both_started.set()
            await self.both_started.wait()
            raise session_errors.SessionOwnerGoneError("owner gone")
        return await super().send_message(session, prompt)


def _without_body_path(result: dict[str, Any]) -> dict[str, Any]:
    """`atk agents wait`の終端行から、本文ファイルの内容が`agent_message`と一致することを確かめて`agent_message_path`を除く。"""
    body = dict(result)
    assert pathlib.Path(body.pop("agent_message_path")).read_text(encoding="utf-8") == body["agent_message"]
    return body


def _without_root(response: dict[str, Any]) -> dict[str, Any]:
    """`send_message`の応答から、書込主体を持つmanagerが加える`root_session_id`を確かめて除いた本体を返す。"""
    body = dict(response)
    assert shared_layout.valid_session_id(body.pop("root_session_id"))
    return body


def _manager_with_fake(engine: str, delivery: str = "reply_started") -> tuple[server_manager.AgentsServerManager, FakeBackend]:
    """指定engineだけをFakeBackendへ差し替えた共有managerを返す。"""
    manager = server_manager.AgentsServerManager()
    backend = FakeBackend(manager.sessions, engine, delivery)
    _install_backend(manager, engine, backend)
    return manager, backend


def _install_backend(
    manager: server_manager.AgentsServerManager, engine: str, backend: FakeBackend | claude_backend.ClaudeServerManager
) -> None:
    """指定engineのバックエンドを差し替える。"""
    if engine == "codex":
        install_backend(manager, "codex", backend)
    elif engine == "agy":
        install_backend(manager, "agy", backend)
    else:
        install_backend(manager, "claude", backend)


def _set_wait_timeout(manager: server_manager.AgentsServerManager, timeout: float) -> None:
    """導出を経由せずに指定したmanagerの待機上限を確定する。"""
    manager._wait_timeouts["main"] = timeout


def _start_tool() -> Any:
    tool = mcp_tools.mcp._tool_manager.get_tool("start")
    assert tool is not None
    return tool


def _write_declared_task_document(tmp_path: pathlib.Path, input_block: str) -> pathlib.Path:
    """agent-toolkitのshare配下と同じ構造の一時の`<役割名>.subagent.md`を作成し、その絶対パスを返す。"""
    plugin_root = tmp_path / "plugin"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document = plugin_root / "share" / "declared.subagent.md"
    task_document.parent.mkdir()
    task_document.write_text(f"# 担当\n\n## 入力\n\n```text\n{input_block}\n```\n\n## 出力\n\n結果を返す。\n", encoding="utf-8")
    return task_document


def _observed_input_lines(task_name: str, root: pathlib.Path, *, rereview: bool = False) -> list[str]:
    """実運用で観測した委譲プロンプトの名前付き入力を組み立てる。"""
    handoff = f"引き継ぎ記録先: {root / 'handoff.md'}"
    if task_name == "exec-review.subagent.md":
        return [
            "レビュー基準: 計画",
            f"計画: {root / 'plan.md'}",
            "未判定検証記録: なし",
            f"完成条件証拠: {root / 'completion-evidence.json'}",
            handoff,
        ]
    if task_name == "exec.subagent.md":
        return [
            "担当種別: レーン担当",
            f"選定結果の出力先ファイル: {root / 'selection.json'}",
            "レーン識別子: lane-01",
            handoff,
        ]
    if task_name == "lane-integration.subagent.md":
        return [
            "統合区分: マージあり",
            "レーン開始時刻: 2026-10-08T00:38:16Z",
            "実行レビュー済みHEAD: 0123abc",
            f"統合先worktree: {root}",
            "統合先branch: develop",
            '計画ファイル名一覧: ["plan.md"]',
            "AWI終端区分: 20260101-000000-001.md=adopt",
        ]
    if task_name == "session-termination.subagent.md":
        return [
            "bump種別: bump不要",
            handoff,
        ]
    if task_name == "add-wi.subagent.md":
        return ["投入する要求: request-1=/repo=awi=検出条件の追加", handoff]
    if task_name == "external-write-review.subagent.md":
        return [
            f"文面ファイル: {root / 'pr-body.md'}（12行）",
            "投稿先と目的: GitHubのPR本文。変更の目的をレビュアーへ伝える",
            f"根拠の所在: {root / 'diff.patch'}",
        ]
    if task_name == "copilot-review-audit.subagent.md":
        return [f"pending取得結果: {root / 'pending.json'}", handoff]
    if task_name == "reader-fit-review.subagent.md":
        lines = [
            f"成果物: {root / 'guide.md'}（40行）",
            "種別: エンドユーザー向け文書",
            "読者像: ツールを初めて導入するエンドユーザー。内部の実装は知らない",
            f"修正範囲: {root / 'before.md'}と成果物の設定保存節の差分、直接影響は再読込節",
        ]
        if rereview:
            lines.extend(
                [
                    "レビュー種別: 再レビュー",
                    "未解決事項: なし",
                ]
            )
        return lines
    if task_name == "bulk-replace-review.subagent.md":
        return [f"差分ファイル: {root / 'word-diff.txt'}（120行）"]
    if task_name == "pick-wi-explain.subagent.md":
        return [
            f"説明対象の選定結果の出力先ファイル: {root / 'selection.json'}",
            "選定理由への質問: 20260101-000000-001.mdを別レーンにした理由",
        ]
    if task_name == "defect-investigation.subagent.md":
        return ["対象の不良: 委譲プロンプトの必須入力検査が見出しを誤認する（agents_server_mcp.py）", handoff]
    raise ValueError(f"未対応の`<役割名>.subagent.md`: {task_name}")


def _observed_input_params(task_name: str, root: pathlib.Path) -> dict[str, str]:
    """実運用の入力行を専用startの名前付きパラメータへ変換する。"""
    return dict(line.split(": ", 1) for line in _observed_input_lines(task_name, root))


class _RegistryBackend(FakeBackend):
    """作成したsessionを登録簿へ公開するバックエンド。委譲元の記録を登録簿で観測するテストが使う。"""

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.publish_registry = True
        return session


_LAUNCHER_ENVIRONMENT_NAMES = (
    "CLAUDE_CODE_SESSION_ID",
    "AGENT_TOOLKIT_OWNER_SESSION",
    "AGENT_TOOLKIT_DELEGATED_SESSION",
    "AGENT_TOOLKIT_STATUS_HOST_SESSION",
    "CODEX_THREAD_ID",
)


async def _carry_over_late_unavailability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    candidates: list[tuple[str, str, str]],
) -> tuple[server_manager.AgentsServerManager, FakeBackend]:
    """開始確認の上限後に候補が失敗した状態まで進め、利用できるbackendへ差し替える。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    manager = server_manager.AgentsServerManager()
    delayed = DelayedUnavailableBackend(manager.sessions, "codex", manager._condition, delay=0.01)
    _install_backend(manager, "codex", delayed)

    await manager.start("plan", "調査", str(tmp_path))
    await asyncio.gather(*delayed.pending)
    assert (await manager.wait())["status"] == "failed"
    available = FakeBackend(manager.sessions, "codex")
    _install_backend(manager, "codex", available)
    return manager, available


def _codex_model_rejected_error(param: str = "model") -> dict[str, Any]:
    """接続先がCodex候補の引数を拒否した失敗（2026-09-30に観測した`TurnError`の形）を返す。"""
    body = {
        "error": {
            "message": "gpt-6.1-sol は利用できません。使用可能なモデル: gpt-5.6-sol, gpt-6-sol",
            "type": "aichat_error",
            "code": "invalid_parameter_value",
            "param": param,
        }
    }
    return {
        "message": json.dumps(body, ensure_ascii=False),
        "codexErrorInfo": "other",
        "additionalDetails": None,
        "misalignment": None,
    }


_LAUNCH_VALUES: dict[str, Any] = {
    "label": "lane-05-review",
    "prompt": "起動時の依頼本文",
    "created_at": "2026-09-25T21:58:13+00:00",
}
"""起動情報の各項目へ設定する、フィールドの初期値と異なる値。labelは`-review`で終わり、完了結果の採否確定の案内も確かめる。"""


def _resume_test_manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> tuple[server_manager.AgentsServerManager, status_file.StatusFileWriter]:
    """状態ファイルと登録簿を`tmp_path`へ置き、再開後の状態を観測できるmanagerを返す。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = FakeBackend(manager.sessions, "claude")
    backend.publish_registry = True
    _install_backend(manager, "claude", backend)
    writer.activate()
    return manager, writer


def _expire_launched_session(manager: server_manager.AgentsServerManager, session: state.SessionState) -> None:
    """保持期限を過ぎた終端sessionとして、同じmanagerの`send_message`で再開させる。"""
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session


def _restart_with_registry_record(manager: server_manager.AgentsServerManager, session: state.SessionState) -> None:
    """登録簿のレコードだけを残し、再起動したmanagerが登録簿から復元して再開させる。"""
    del manager, session  # 再起動したmanagerは元のsessionを保持せず、登録簿は終端の公開で書かれている


class FakeCodexClient:
    """Codex App Server要求を記録する偽クライアント。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.reader_failure = None
        self._turn_count = 0

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        self.requests.append((method, params))
        if on_sent is not None:
            on_sent()
        if method in {"thread/start", "thread/resume"}:
            thread_id = params.get("threadId", "thread-codex")
            return {"thread": {"id": thread_id}}
        if method == "turn/start":
            self._turn_count += 1
            return {"turn": {"id": f"turn-{self._turn_count}"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        return {}

    async def close(self) -> None:
        """実クライアントと同じ終了インターフェースを提供する。"""
        self.closed = True


class BlockingResumeCodexClient(FakeCodexClient):
    """最初のthread/resume応答を明示イベントまで保留する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.resume_started = asyncio.Event()
        self.release_resume = asyncio.Event()
        self.resume_finished = asyncio.Event()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "thread/resume":
            self.resume_started.set()
            await self.release_resume.wait()
        result = await super().request(method, params, on_sent=on_sent)
        if method == "thread/resume":
            self.resume_finished.set()
        return result


class LostTurnStartClient(FakeCodexClient):
    """thread/start成功後にturn/start応答を喪失する偽クライアント。"""

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/start":
            raise codex_backend.AppServerError("turn/start response lost")
        return await super().request(method, params, on_sent=on_sent)


class SteerRaceClient(FakeCodexClient):
    """steer拒否とturn終端の競合を再現する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.steer_called = asyncio.Event()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/steer":
            self.steer_called.set()
            raise codex_backend.JsonRpcResponseError(method, -32600, "turn is not active")
        return await super().request(method, params, on_sent=on_sent)


class ServerRequestClient(FakeCodexClient):
    """server-initiated requestへの応答を記録する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


class InterruptErrorClient(FakeCodexClient):
    """turn/interruptへJSON-RPC errorを返す偽クライアント。"""

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/interrupt":
            raise codex_backend.JsonRpcResponseError(method, -32600, "interrupt rejected")
        return await super().request(method, params, on_sent=on_sent)


class CompletingInterruptClient(FakeCodexClient):
    """中断要求の配送中に対象turnを終端させる偽クライアント。"""

    def __init__(self, backend: codex_backend.AppServerManager, session: state.SessionState) -> None:
        super().__init__()
        self.backend = backend
        self.session = session

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/interrupt":
            await self.backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": self.session.session_id,
                        "turn": {"id": self.session.turn_id, "status": "interrupted", "error": None},
                    },
                }
            )
        return await super().request(method, params, on_sent=on_sent)


class HoldingTurnStartClient(FakeCodexClient):
    """turn/start受理後の応答を保留し、中断で実作業を終える偽クライアント。"""

    def __init__(self, backend: codex_backend.AppServerManager) -> None:
        super().__init__()
        self.backend = backend
        self.turn_start_received = asyncio.Event()
        self.active_turn = False

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method != "turn/start":
            if method == "turn/interrupt":
                assert params is not None
                self.active_turn = False
                await self.backend._handle_notification(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": params["threadId"],
                            "turn": {"id": params["turnId"], "status": "interrupted", "error": None},
                        },
                    }
                )
            return await super().request(method, params, on_sent=on_sent)

        params = params or {}
        self.requests.append((method, params))
        if on_sent is not None:
            on_sent()
        self._turn_count += 1
        turn_id = f"turn-{self._turn_count}"
        self.active_turn = True
        await self.backend._handle_notification(
            {
                "method": "turn/started",
                "params": {"threadId": params["threadId"], "turn": {"id": turn_id}},
            }
        )
        self.turn_start_received.set()
        await asyncio.Event().wait()
        raise AssertionError("保留中のturn/start応答が予期せず完了した")


class NaturallyCompletingTurnStartClient(FakeCodexClient):
    """turn/start応答待ちの取消中に対象turnを自然終了できる偽クライアント。"""

    def __init__(self, backend: codex_backend.AppServerManager) -> None:
        super().__init__()
        self.backend = backend
        self.turn_start_received = asyncio.Event()
        self.active_turn = False
        self.turn_id = "turn-natural"

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params, on_sent=on_sent)

        params = params or {}
        self.requests.append((method, params))
        if on_sent is not None:
            on_sent()
        self.active_turn = True
        self.turn_start_received.set()
        await asyncio.Event().wait()
        raise AssertionError("保留中のturn/start応答が予期せず完了した")

    async def complete_turn(self, session_id: str) -> None:
        """開始通知と自然完了通知を連続配送する。"""
        await self.backend._handle_notification(
            {
                "method": "turn/started",
                "params": {"threadId": session_id, "turn": {"id": self.turn_id}},
            }
        )
        self.active_turn = False
        await self.backend._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": session_id,
                    "turn": {"id": self.turn_id, "status": "completed", "error": None},
                },
            }
        )


def _pending_codex_manager[PendingCodexClient: (HoldingTurnStartClient, NaturallyCompletingTurnStartClient)](
    monkeypatch: pytest.MonkeyPatch, client_type: type[PendingCodexClient]
) -> tuple[server_manager.AgentsServerManager, codex_backend.AppServerManager, PendingCodexClient]:
    """保留したturn/start応答を持つCodex再開テストの共通入力を作成する。"""
    manager = server_manager.AgentsServerManager()
    backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
    client = client_type(backend)

    async def ensure_client() -> PendingCodexClient:
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    backend.client = cast(Any, client)
    install_backend(manager, "codex", backend)
    return manager, backend, client


async def _start_blocked_codex_resume(
    manager: server_manager.AgentsServerManager,
    client: HoldingTurnStartClient | NaturallyCompletingTurnStartClient,
    session_id: str,
) -> None:
    """turn開始を確認した後、別の継続要求の短い期限切れを起こす。"""
    manager._start_resume(manager.expired_sessions[session_id], "準備指示", None)  # pylint: disable=protected-access
    await asyncio.wait_for(client.turn_start_received.wait(), timeout=1)
    with pytest.raises(TimeoutError, match=f"send_message timed out: {session_id}"):
        await manager.send_message(session_id, "再開指示", timeout=0.01)
    assert client.active_turn is True


class SystemMessage:
    """Claude SDK initメッセージの偽型。"""

    subtype = "init"

    def __init__(self, session_id: str) -> None:
        self.data = {"session_id": session_id}


class AssistantMessage:
    """Claude SDK assistantメッセージの偽型。"""

    def __init__(self, text: str) -> None:
        self.content = [SimpleNamespace(text=text)]


class MultipleBlockAssistantMessage:
    """複数TextBlockを持つClaude assistantメッセージの偽型。"""

    def __init__(self, *texts: str) -> None:
        self.content = [SimpleNamespace(text=text) for text in texts]


class StreamEvent:
    """Claude SDKの部分出力イベントの偽型。"""

    def __init__(self, event_type: str) -> None:
        self.event = {"type": event_type}


class ErrorAssistantMessage(AssistantMessage):
    """API失敗を表すClaude assistantメッセージの偽型。"""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.error = "rate_limit"


class ResultMessage:
    """Claude SDK resultメッセージの偽型。"""

    def __init__(
        self,
        result: str,
        terminal_reason: str | None = None,
        *,
        is_error: bool = False,
        api_error_status: int | None = None,
    ) -> None:
        self.result = result
        self.errors: list[str] = []
        self.terminal_reason = terminal_reason
        self.is_error = is_error
        self.api_error_status = api_error_status


class FakeClaudeClient:
    """ClaudeSDKClientの最小互換。"""

    def __init__(self, streams: list[list[Any]]) -> None:
        self.streams = [list(stream) for stream in streams]
        self.queries: list[str] = []
        self.interrupts = 0
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def interrupt(self) -> None:
        self.interrupts += 1

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message

        return stream()

    async def disconnect(self) -> None:
        self.disconnected = True


class QueueClaudeClient(FakeClaudeClient):
    """queryと背景イベントが同じ長命なSDK受信列へ入るクライアント。"""

    def __init__(self, streams: list[list[Any]]) -> None:
        super().__init__(streams)
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        self.readers = 0
        self.receive_calls = 0

    async def query(self, prompt: str) -> None:
        await super().query(prompt)
        for message in self.streams.pop(0):
            self.messages.put_nowait(message)

    def receive_messages(self):
        self.receive_calls += 1

        async def stream():
            self.readers += 1
            try:
                while True:
                    yield await self.messages.get()
            finally:
                self.readers -= 1

        return stream()


class DelayedClaudeClient(FakeClaudeClient):
    """init後の通常メッセージ間隔を遅延できる偽クライアント。"""

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for index, message in enumerate(messages):
                if index:
                    await asyncio.sleep(0.08)
                yield message

        return stream()


class OpenStreamClaudeClient(FakeClaudeClient):
    """指定したメッセージを返した後、結果を返さずにstreamを開いたまま保つ偽クライアント。"""

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            await asyncio.Event().wait()

        return stream()


class InterruptAwareClaudeClient(FakeClaudeClient):
    """interruptの受理後に中断結果を返す偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([[SystemMessage("claude-interrupted")]])
        self.interrupt_event = asyncio.Event()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            await self.interrupt_event.wait()
            yield ResultMessage("中断結果", "aborted_streaming")

        return stream()

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.interrupt_event.set()


class FailingClaudeClient(FakeClaudeClient):
    """init後にmessage stream例外を発生させる偽クライアント。"""

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-failed")
            raise RuntimeError("stream failed")

        return stream()


class SynchronizedFailingClaudeClient(FakeClaudeClient):
    """同じ待機バッチでmessage stream例外を発生させる偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([])
        self.message_waiting = asyncio.Event()
        self.release_message = asyncio.Event()

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-batch-failed")
            self.message_waiting.set()
            await self.release_message.wait()
            raise RuntimeError("stream failed")

        return stream()


class BlockingContinuationClaudeClient(FakeClaudeClient):
    """継続入力のqueryを停止して所有タスクの取り消しを再現する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([[SystemMessage("claude-blocking")]])
        self.message_waiting = asyncio.Event()
        self.query_started = asyncio.Event()
        self.release_query = asyncio.Event()

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if len(self.queries) > 1:
            self.query_started.set()
            await self.release_query.wait()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            self.message_waiting.set()
            await self.release_query.wait()

        return stream()


class BlockingResumeClaudeClient(FakeClaudeClient):
    """resumeの最初のqueryを記録前に保留する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([])
        self.query_started = asyncio.Event()
        self.query_cancelled = asyncio.Event()
        self.release_query = asyncio.Event()
        self.stop_stream = asyncio.Event()
        self.connect_calls = 0
        self.query_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        await super().connect()

    async def query(self, prompt: str) -> None:
        self.query_calls += 1
        if self.query_calls == 1:
            self.query_started.set()
            try:
                await self.release_query.wait()
            except asyncio.CancelledError:
                self.query_cancelled.set()
                raise
        await super().query(prompt)

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-pending")
            await self.stop_stream.wait()

        return stream()


def _blocking_resume_claude_manager(
    monkeypatch: pytest.MonkeyPatch, client: BlockingResumeClaudeClient
) -> tuple[server_manager.AgentsServerManager, claude_backend.ClaudeServerManager]:
    """保留したClaude queryを持つ再開テストの共通入力を作成する。"""
    manager = server_manager.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    install_backend(manager, "claude", backend)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    return manager, backend


class BlockingResultClaudeClient(FakeClaudeClient):
    """init後の結果を明示イベントまで保留する偽クライアント。"""

    def __init__(self, session_id: str) -> None:
        super().__init__([])
        self.session_id = session_id
        self.release_result = asyncio.Event()

    def receive_messages(self):
        async def stream():
            yield SystemMessage(self.session_id)
            await self.release_result.wait()
            yield ResultMessage("新しい結果")

        return stream()


async def _start_claude_until_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    messages: list[Any],
    availability_timeout: float,
) -> tuple[dict[str, Any], float, bool]:
    """指定メッセージの後に結果を返さないClaude sessionを`start`で起動し、応答、所要秒数、モデル出力の観測を返す。"""
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", availability_timeout)
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [("claude", "model", "high")])
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    client = OpenStreamClaudeClient([messages])
    manager = server_manager.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(manager.sessions, manager._condition, client_factory=lambda _options: client)
    _install_backend(manager, "claude", backend)
    loop = asyncio.get_running_loop()
    try:
        started = loop.time()
        response = await manager.start("plan", "調査", str(tmp_path))
        elapsed = loop.time() - started
        return response, elapsed, backend.sessions[response["session_id"]].model_output_observed
    finally:
        await manager.close()


def _publish_recovered_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    session_id: str,
    status: str,
) -> None:
    """再起動後の解決に用いるversion 2登録簿を保存する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        session_id,
        terminal=True,
        engine="codex",
        cwd=str(tmp_path),
        model="model",
        effort="high",
        model_type="execute",
        turn_seq=2,
        status=cast(Any, status),
    )


def _writer_backed_manager(tmp_path: pathlib.Path) -> tuple[server_manager.AgentsServerManager, status_file.StatusFileWriter]:
    """共有結果ファイルを公開するMCPマネージャーを既存のfake backendで組む。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    install_backend(manager, "codex", FakeBackend(manager.sessions, "codex"))
    writer.activate()
    return manager, writer


def _recording_candidates(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """候補列の解決へ渡った`model_type`を記録し、常に同じ候補を返す。"""
    requested: list[str] = []

    def fake_parse(model_type: str) -> list[tuple[str, str, str]]:
        requested.append(model_type)
        return [("codex", "model", "high")]

    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", fake_parse)
    return requested


_MODE_INPUT_CASES = [
    pytest.param("task", {}, "subagent_md_path", id="task-missing-document"),
    pytest.param("task", {"subagent_md_path": "/abs/x.subagent.md", "prompt": "本文"}, "prompt", id="task-mixed-prompt"),
    pytest.param("delegate", {"prompt": "本文"}, "model_type", id="delegate-missing-model-type"),
    pytest.param(
        "delegate",
        {"prompt": "本文", "model_type": "high_tier", "extra_params": {}},
        "extra_params",
        id="delegate-mixed-extra-params",
    ),
    pytest.param("explore", {}, "prompt", id="explore-missing-prompt"),
    pytest.param("write", {"prompt": "本文", "command": "ls"}, "command", id="write-mixed-command"),
    pytest.param("shell", {"command": "make test"}, "summary_policy", id="shell-missing-policy"),
    pytest.param(
        "shell",
        {"command": "make test", "summary_policy": "終了状態", "prompt": "本文"},
        "prompt",
        id="shell-mixed-prompt",
    ),
    pytest.param(
        "explore",
        {"prompt": "本文", "subagent_md_path": "/abs/x.subagent.md"},
        "subagent_md_path",
        id="explore-mixed-document",
    ),
]


def _write_failing_uv(bin_dir: pathlib.Path) -> None:
    """未trustのmise shimと同じく、標準エラーへ理由を書いて終了コード1で終わる`uv`・`uvx`を置く。"""
    bin_dir.mkdir()
    for name in ("uv", "uvx"):
        path = bin_dir / name
        path.write_text("#!/bin/sh\necho 'mise ERROR Config files in ./mise.toml are not trusted.' >&2\nexit 1\n")
        path.chmod(0o755)


class ThreadReadBackend(FakeBackend):
    """Codex App Serverの`thread/read`へ固定のturn一覧を返すか、照会の失敗を返すバックエンド。"""

    def __init__(
        self, sessions: dict[str, state.SessionState], turns: list[tuple[str, str]] | None, error: Exception | None = None
    ) -> None:
        super().__init__(sessions, "codex")
        self.turns = turns
        self.error = error
        self.read_calls: list[str] = []

    async def read_thread_turns(self, session_id: str) -> list[tuple[str, str]]:
        self.read_calls.append(session_id)
        if self.error is not None:
            raise self.error
        assert self.turns is not None
        return self.turns


async def _show_through_tool(
    monkeypatch: pytest.MonkeyPatch, manager: server_manager.AgentsServerManager, session_id: str
) -> dict[str, Any]:
    """MCPの`show`ツールの関数を、指定したマネージャーで呼ぶ。"""
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    return await mcp_tools.show_session(session_id)


def _publish_orphaned_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, session_id: str, turn_id: str | None
) -> None:
    """所有者が終端を公開せずに終了したCodexの`running`記録を保存する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        session_id, terminal=False, engine="codex", cwd=str(tmp_path), turn_seq=3, status="running", turn_id=turn_id
    )


_OVERLOAD_ERROR = {
    "message": "Selected model is at capacity. Please try a different model.",
    "codexErrorInfo": "serverOverloaded",
}


async def _wait_until_terminal(manager: server_manager.AgentsServerManager, attempts: int = 50) -> dict[str, Any]:
    """委譲元と同じく、`running`が返る間は`wait`を再発行して終端結果を受け取る。"""
    _set_wait_timeout(manager, 0.2)
    for _ in range(attempts):
        response = await manager.wait()
        if response.get("status") in state.TERMINAL_STATUSES:
            return response
    raise AssertionError("終端結果を受け取れない")


class OverloadingBackend(FakeBackend):
    """起動応答の後のturnをCodexの過負荷で終端させる偽バックエンド。

    Codex backendの`turn/completed`の処理（可用性確認後の過負荷の保留）を、共有状態の同じ関数で再現する。
    `overloads`回まで過負荷で終え、その後の継続turnは`final_message`で正常に終える。
    """

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        condition: asyncio.Condition,
        overloads: int,
        final_message: str = "継続後の結果",
    ) -> None:
        super().__init__(sessions, "codex")
        self._condition = condition
        self._remaining = overloads
        self._final_message = final_message
        self.pending: list[asyncio.Task[None]] = []

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model = session.model or "gpt-6.1-sol"
        session.effort = session.effort or "high"
        self.pending.append(asyncio.create_task(self._finish_turn(session, delay=0.05)))
        return session

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, Any]:
        result = await super().send_message(session, prompt)
        self.pending.append(asyncio.create_task(self._finish_turn(session, delay=0.0)))
        return result

    async def _finish_turn(self, session: state.SessionState, *, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._remaining > 0:
            self._remaining -= 1
            session.status = "failed"
            session.error = dict(_OVERLOAD_ERROR)
            session.turn_completed = True
            if (session.availability_checked or session.turn_seq > 1) and resume_waits.begin_overload_resume_wait(
                session, {"status": "failed", "agent_message": "", "error": session.error}
            ):
                session.status = "running"
            else:
                session.touch()
        else:
            _complete(session, message=self._final_message)
        async with self._condition:
            self._condition.notify_all()


def _rate_limit_event(status: str, limit_type: str | None, resets_at: int | None = None) -> Any:
    """Claude Agent SDKが利用枠の報告として返すイベント。"""
    info = claude_agent_sdk.RateLimitInfo(status=cast(Any, status), rate_limit_type=cast(Any, limit_type), resets_at=resets_at)
    return claude_agent_sdk.RateLimitEvent(rate_limit_info=info, uuid="rate-limit", session_id="")


def _usage_limit_result(text: str = "You've hit your limit", api_error_status: int = 429) -> ResultMessage:
    """利用上限で拒否されたturnの`ResultMessage`。"""
    return ResultMessage(text, is_error=True, api_error_status=api_error_status)


def _rejected_stream(limit_type: str, resets_at: int | None, *, before_init: bool, session_id: str) -> list[Any]:
    event = _rate_limit_event("rejected", limit_type, resets_at)
    failure = [ErrorAssistantMessage("API Error: 429 usage limit"), _usage_limit_result()]
    if before_init:
        return [event, SystemMessage(session_id), *failure]
    return [SystemMessage(session_id), event, *failure]


async def _usage_limited_manager(
    monkeypatch: pytest.MonkeyPatch, streams: list[list[Any]]
) -> tuple[server_manager.AgentsServerManager, FakeClaudeClient, FakeBackend]:
    """先頭のClaude候補が利用上限で拒否され、後続にCodex候補を持つ起動条件のManagerを返す。"""
    candidates = [("claude", "opus", "high"), ("codex", "gpt-6.1-sol", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _m: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 5.0)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    client = FakeClaudeClient(streams)
    manager = server_manager.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(manager.sessions, manager._condition, client_factory=lambda _options: client)
    _install_backend(manager, "claude", backend)
    codex = FakeBackend(manager.sessions, "codex")
    _install_backend(manager, "codex", codex)
    return manager, client, codex
