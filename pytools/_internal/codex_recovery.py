"""通常Codexの公開RPCから、同じ会話を再開するための条件を保持する。"""

from __future__ import annotations

import copy
import dataclasses
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_CONTINUATION = (
    '<atk-auto source="dotfiles" kind="daemon-recovery">'
    "ローカルdaemonの再起動後に同じ会話を自動再開した。"
    "保存された元の要求・作業記録と未回収の子孫の結果を確認し、未完了の仕事を続行せよ。"
    "実行済み操作は結果を確認してから扱い、応答喪失だけを理由に再実行しない。"
    "</atk-auto>"
)
_RESUME_FIELDS = frozenset(
    {
        "model",
        "modelProvider",
        "cwd",
        "approvalPolicy",
        "approvalsReviewer",
        "sandbox",
        "config",
        "baseInstructions",
        "developerInstructions",
        "personality",
        "serviceTier",
    }
)


class _Thread(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    id: str
    cwd: str
    model: str | None = None
    model_provider: str | None = Field(default=None, alias="modelProvider")


class _ThreadResult(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    thread: _Thread
    approval_policy: Any = Field(alias="approvalPolicy")
    approvals_reviewer: Any = Field(default=None, alias="approvalsReviewer")
    sandbox: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class UnselectedConversation:
    """ユーザーの会話が未選択である。"""


@dataclasses.dataclass(frozen=True)
class SelectedConversation:
    """会話を選択済みで、観測したturnが無い。"""

    thread_id: str
    cwd: str


@dataclasses.dataclass(frozen=True)
class RunningTurn:
    """選択した会話のturnが実行中である。"""

    thread_id: str
    cwd: str
    turn_id: str


@dataclasses.dataclass(frozen=True)
class CompletedTurn:
    """観測したturnが終了している。"""

    thread_id: str
    cwd: str
    turn_id: str


@dataclasses.dataclass(frozen=True)
class InterruptedConversation:
    """ユーザーが中断または購読解除を要求した会話である。"""

    thread_id: str
    cwd: str
    turn_id: str | None


type ConversationState = UnselectedConversation | SelectedConversation | RunningTurn | CompletedTurn | InterruptedConversation


@dataclasses.dataclass
class ConversationRecovery:
    """1個の通常TUIが選択した会話と、再起動で失われた仕事を保持する。

    タイトル生成などの一時threadは同じ接続上にも現れるため、ユーザーの会話と分離する。
    RPC本文は中継プロセスの寿命内だけで保持し、プロンプトや認証値を記録へ複製しない。
    """

    _conversation: ConversationState = dataclasses.field(default_factory=UnselectedConversation, init=False)
    initialize: dict[str, Any] | None = None
    _pending: dict[str | int, tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=dict)
    _startup: dict[str, Any] = dataclasses.field(default_factory=dict)
    _turn: dict[str, Any] = dataclasses.field(default_factory=dict)
    _policy: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def conversation(self) -> ConversationState:
        """会話の現在状態を読み取り専用で返す。"""
        return self._conversation

    @property
    def thread_id(self) -> str | None:
        """選択した会話のIDを返す。"""
        return None if isinstance(self._conversation, UnselectedConversation) else self._conversation.thread_id

    @property
    def cwd(self) -> str | None:
        """選択した会話の作業ディレクトリを返す。"""
        return None if isinstance(self._conversation, UnselectedConversation) else self._conversation.cwd

    def restore_running_turn(self, original: RunningTurn) -> bool:
        """復旧前のturnを、中断要求を保ったまま続行判定へ戻す。"""
        selected = self._conversation
        if isinstance(selected, (UnselectedConversation, InterruptedConversation)):
            return False
        self._conversation = RunningTurn(selected.thread_id, selected.cwd, original.turn_id)
        return True

    def observe_client(self, message: dict[str, Any]) -> None:
        """中継する要求から会話の選択と明示中止を記録する。"""
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict):
            return
        if method == "initialize":
            self.initialize = copy.deepcopy(params)
        identifier = message.get("id")
        if (
            method in {"thread/start", "thread/resume"}
            and not params.get("ephemeral")
            and (method == "thread/resume" or params.get("threadSource") in {None, "user"})
            and isinstance(identifier, (str, int))
            and not isinstance(identifier, bool)
        ):
            self._pending[identifier] = (method, copy.deepcopy(params))
        if params.get("threadId") != self.thread_id or self.thread_id is None:
            return
        if method == "turn/start":
            self._turn = copy.deepcopy(params)
            if isinstance(self._conversation, InterruptedConversation):
                selected = self._conversation
                self._conversation = (
                    SelectedConversation(selected.thread_id, selected.cwd)
                    if selected.turn_id is None
                    else CompletedTurn(selected.thread_id, selected.cwd, selected.turn_id)
                )
        if method in {"turn/interrupt", "thread/unsubscribe"}:
            selected = self._conversation
            assert not isinstance(selected, UnselectedConversation)
            self._conversation = InterruptedConversation(
                selected.thread_id, selected.cwd, None if isinstance(selected, SelectedConversation) else selected.turn_id
            )

    def observe_server(self, message: dict[str, Any]) -> None:
        """同じ会話の通知だけを活動と終了へ反映する。"""
        identifier = message.get("id")
        request = self._pending.pop(identifier, None) if isinstance(identifier, (str, int)) else None
        if request is not None and "result" in message:
            result = _ThreadResult.model_validate(message["result"])
            self._conversation = SelectedConversation(result.thread.id, result.thread.cwd)
            self._startup = {key: value for key, value in request[1].items() if key in _RESUME_FIELDS}
            self._startup.update(threadId=self.thread_id, cwd=self.cwd)
            for key, value in result.thread.model_dump(by_alias=True).items():
                if key in {"model", "modelProvider"} and value is not None:
                    self._startup[key] = value
            self._policy = {
                "approvalPolicy": copy.deepcopy(result.approval_policy),
                "approvalsReviewer": copy.deepcopy(result.approvals_reviewer),
                "sandboxPolicy": copy.deepcopy(result.sandbox),
            }
        params = message.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id or self.thread_id is None:
            return
        turn = params.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            return
        selected = self._conversation
        assert not isinstance(selected, UnselectedConversation)
        if message.get("method") == "turn/started" and not isinstance(selected, InterruptedConversation):
            self._conversation = RunningTurn(selected.thread_id, selected.cwd, turn["id"])
        if message.get("method") == "turn/completed" and isinstance(selected, RunningTurn) and turn["id"] == selected.turn_id:
            self._conversation = CompletedTurn(selected.thread_id, selected.cwd, selected.turn_id)

    def resume_parameters(self) -> dict[str, Any]:
        """元の公開起動条件を使うthread/resumeの入力を返す。"""
        if self.thread_id is None:
            raise RuntimeError("Codexの会話IDを取得していないため、自動再開できない")
        result = copy.deepcopy(self._startup)
        result.update({key: value for key, value in self._policy.items() if key != "sandboxPolicy"})
        sandbox = {"readOnly": "read-only", "workspaceWrite": "workspace-write", "dangerFullAccess": "danger-full-access"}.get(
            self._policy.get("sandboxPolicy", {}).get("type")
        )
        if sandbox is not None:
            result["sandbox"] = sandbox
        return result

    def continuation_parameters(self, thread: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any] | None:
        """元turnが未完了で現在のwriterが無い場合に限り、自動続行の入力を返す。"""
        if thread.get("id") != self.thread_id or thread.get("cwd") != self.cwd:
            raise RuntimeError("再開したCodexの会話IDまたは作業ディレクトリが元の実行と一致しない")
        selected = self._conversation
        if not isinstance(selected, RunningTurn) or not turns or turns[0].get("id") != selected.turn_id:
            return None
        status = thread.get("status")
        if isinstance(status, dict) and status.get("type") == "active":
            return None
        if turns[0].get("status") in {"completed", "failed"}:
            self._conversation = CompletedTurn(selected.thread_id, selected.cwd, selected.turn_id)
            return None
        result = copy.deepcopy(self._turn)
        result.update(
            threadId=self.thread_id,
            cwd=self.cwd,
            clientUserMessageId=str(uuid.uuid4()),
            input=[{"type": "text", "text": _CONTINUATION}],
            **copy.deepcopy(self._policy),
        )
        return result
