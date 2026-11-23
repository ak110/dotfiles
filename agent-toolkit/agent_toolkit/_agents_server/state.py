from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing
from collections.abc import Callable
from typing import Any

from agent_toolkit._agents_server import state_publication, state_resume, turn_state
from agent_toolkit._agents_server.result_projection import append_result_next_action
from agent_toolkit._common import task_documents

PreviousResult = state_resume.PreviousResult
RegistryResumeState = state_resume.RegistryResumeState
ResumeDelivery = state_resume.ResumeDelivery
ResumePrompt = state_resume.ResumePrompt
SessionResumeState = state_resume.SessionResumeState
begin_reply = state_resume.begin_reply
fast_mode_fields = state_resume.fast_mode_fields
has_uncollected_result = state_resume.has_uncollected_result
initialize_turn = state_resume.initialize_turn
selected_candidate = state_resume.selected_candidate
terminal_result_payload = state_resume.terminal_result_payload
previous_result_payload = state_resume.previous_result_payload
utc_now = state_resume.utc_now
add_lifecycle_listener = state_publication.add_lifecycle_listener
add_terminal_listener = state_publication.add_terminal_listener
add_touch_listener = state_publication.add_touch_listener
notify_touch_listeners = state_publication.notify_touch_listeners
remove_lifecycle_listener = state_publication.remove_lifecycle_listener
remove_terminal_listener = state_publication.remove_terminal_listener
remove_touch_listener = state_publication.remove_touch_listener

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.session_state import SessionState as SessionState  # pylint: disable=useless-import-alias


def __getattr__(name: str) -> Any:
    """遅延importを無効にしても、状態契約の初期化前に型の再公開へ入らない。"""
    if name == "SessionState":
        from agent_toolkit._agents_server.session_state import SessionState  # pylint: disable=import-outside-toplevel

        return SessionState
    raise AttributeError(name)


_LOG = logging.getLogger("agent-toolkit.agents-server.state")

RESULT_RETENTION_SECONDS = 1800.0
# 委譲先の最終活動時刻からの経過が本値を超えた待機の応答へ、停滞の可能性を示す項目を加える。
# 値はユーザーの提案に基づく300秒とする。長時間のコマンドの実行待ちでも超過し得るため、
# 超過は委譲元が状況を調べる契機として扱う。
STALL_NOTICE_SECONDS = 300.0
# ホストが応答しないMCPツール呼び出しをバックグラウンドタスクへ移すまでの秒数。
# 以降の上限はこの閾値を制約として導出する。単独の値として決めない。
HOST_BACKGROUND_THRESHOLD_SECONDS = 120.0
# backendがsessionの初期化を完了するまでagents_serverが待つ上限秒数と、同じ候補で試みる回数。
# Claude Codeの記録では、`start`の呼び出しから起動された子sessionの記録の先頭エントリまでの
# 経過が233件中232件で47.65秒以内に収まり、残る1件が604.22秒だった。
# 同じ母集団のうち7件は初期化が到達せず、ホストがMCPツール呼び出しを1800.5秒で打ち切っていた。
# 1回の上限は観測の上位側の47.65秒を含む値とし、回数との積へ起動直後の可用性失敗を待つ上限
# （`_agents_server/manager.py`のSTART_AVAILABILITY_TIMEOUT）を直列に加えた和が
# HOST_BACKGROUND_THRESHOLD_SECONDSを下回るように選ぶ。
# この関係が成立しなくなると、初期化の失敗が確定する前にホストがツール呼び出しを背景へ移し、
# 委譲元は`start`の失敗を受け取らないまま待機へ進む。
# 監査記録は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_agents_server/state.py：session初期化の待機上限：2026年9月11日」にある。
SESSION_INITIALIZATION_TIMEOUT = 50.0
SESSION_INITIALIZATION_ATTEMPTS = 2
# 起動直後の可用性失敗を確定するために`start`が終了を待つ上限秒数。
# Codex CLI 0.152.0で利用上限に達した状態のturnは、backendの起動応答から3.84〜4.27秒後に
# `turn/completed`で失敗した（2026-09-02、`explore_fast`候補`gpt-5.6-terra/medium`で3回測定）。
# 再検証は同じ失敗状態で`AppServerManager.start`を呼び、応答から終了までの経過を測る。
# 可用性失敗は最初のモデル出力より前に生じるため、正常起動ではモデル出力の観測で上限を待たずに打ち切る。
# Claudeでは、失敗した要求では始まらないAPIの応答開始（`message_start`）をモデル出力の観測として扱う。
START_AVAILABILITY_TIMEOUT = 15.0
TERMINAL_STATUSES = turn_state.TERMINAL_STATUSES


ModelCandidate = tuple[str, str, str]
LaunchKind = task_documents.LaunchKind
_TOUCH_LISTENERS: set[Callable[[], None]] = set()
_TOUCH_OWNERS: dict[Callable[[], None], dict[str, SessionState]] = {}
_TERMINAL_LISTENERS: set[Callable[[SessionState], None]] = set()
_LIFECYCLE_LISTENERS: set[Callable[[SessionState, str], None]] = set()


def append_bounded(existing: str, delta: str, limit: int = 4000) -> str:
    value = existing + delta
    return value if len(value) <= limit else value[-limit:]


@dataclasses.dataclass(frozen=True)
class RetryChain:
    """再試行中だけ存在する回数と初回時刻。"""

    count: int
    first_at: str

    @classmethod
    def advance(cls, previous: RetryChain | None) -> RetryChain:
        return cls(1, utc_now()) if previous is None else cls(previous.count + 1, previous.first_at)


@dataclasses.dataclass(frozen=True)
class ActiveItem:
    """Codexから受け取った進行中itemと受信時刻。"""

    item: dict[str, Any]
    started_at: str


@dataclasses.dataclass(frozen=True)
class LiveTask:
    """Claude backendが追跡する背景task（`TaskStartedMessage`の内容と受信時刻）。

    結果を保留している間の待機対象を`show`で委譲元へ示すために保持する。
    """

    task_type: str
    description: str
    started_at: str


def finish_unowned_start(
    initialized: asyncio.Future[SessionState],
    turn_seq: int,
    error: BaseException,
    *,
    session: SessionState | None = None,
) -> bool:
    if initialized.done() and not initialized.cancelled() and initialized.exception() is None:
        session = initialized.result()
    if session is None or session.turn_seq != turn_seq or session.turn_completed or session.terminal:
        return False
    session.clear_hold()
    session.status = "failed"
    session.error = append_result_next_action(
        {"message": f"初期化の受付確定前に所有タスクが終了した: {type(error).__name__}"},
        "showで元のturnの状態と結果を確認し、新しいturnが始まっていなければ同じ入力を送る",
    )
    session.record_turn_completed()
    session.touch()
    return True
