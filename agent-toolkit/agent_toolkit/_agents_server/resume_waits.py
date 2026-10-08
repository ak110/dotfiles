"""孫sessionの終端、Codexの過負荷、Claudeの利用上限の解除を待って同じsessionを継続する自動再開の状態遷移。"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import Iterable
from typing import Any

from agent_toolkit._agents_server import state
from agent_toolkit._agents_server.result_projection import nonempty_error
from agent_toolkit._agents_server.state import SessionState, utc_now

# Claude CodeのBashツールが背景実行へ許す実行時間の上限（ミリ秒を秒へ換算）。Claude Code 2.1.291の
# Bashツールの入力スキーマは背景実行の`timeout`を「default 1800000, max 7200000」と説明し、上限に達した
# バックグラウンドタスクを止めて完了通知を送る。本体の実装は環境変数による上限の引き上げも受け付けるため、
# この値は環境変数を設定しない場合の上限である。
CLAUDE_BACKGROUND_BASH_MAX_SECONDS = 7200.0


# 待機表明の保留を、完了通知が届かない場合に打ち切る期限。保留が待つバックグラウンドタスクの完了通知は背景実行の上限以内に
# 届くため、上限より短い期限は稼働中のバックグラウンドタスクを待つ保留を打ち切ってしまう。完了通知の配送と再開turnの開始に
# 要する時間の余裕を上限へ加える。結果保持期限とは目的が異なり、値も共有しない。
AUTO_RESUME_DEADLINE_SECONDS = CLAUDE_BACKGROUND_BASH_MAX_SECONDS + 600.0


UNFINISHED_BACKGROUND_TASKS_KEY = "unfinishedBackgroundTasks"
"""保留した結果を確定した時点で稼働中だったバックグラウンドタスクの識別子を`error`へ記録する項目名。"""


UNOBSERVED_SESSIONS_KEY = "unobservedSessions"
"""終端を観測していなかった孫sessionの識別子を`error`へ記録する項目名。

保留の確定に加え、自動再開を消費した後のturnの終端など保留と無関係な処理でも記録する。
"""


HELD_RESULT_FINALIZED_KEY = "heldResultFinalized"
"""待機表明の保留を、待機対象が残ったまま確定したことを`error`へ示す項目名。

`unobservedSessions`は保留と無関係な処理でも記録されるため、確定した結果が再開したturnの結果ではないことは
この項目だけで判別する。
"""


# 起動の可用性確認を過ぎた後にCodexのturnがモデルの過負荷で終端した場合に、同じsessionへ継続を送るまでの待機秒数。
# 要素数が1回の失敗の連鎖で行う自動継続の上限回数となる。値はユーザー指示（15秒・30秒・60秒）による。
# 直後の再送では過負荷が解けなかった観測があるため待機を置き、間隔を広げる。上限に達しても解けない場合は
# 最後の失敗を公開し、その候補を次回の起動の除外対象として記録して、委譲元の起動し直しで別のモデルへ移す。
OVERLOAD_RESUME_DELAYS_SECONDS = (15.0, 30.0, 60.0)


OVERLOAD_ERROR_INFO = "serverOverloaded"


# Claude CodeのWeekly limitと5時間の利用上限の解除待ちを`api_error`の`type`で示す値。
# 解除待ちは回数と総時間の上限を持たない（解除まで待ち、別の候補へ切り替えない。ユーザー指示）。
USAGE_LIMIT_ERROR_TYPE = "usage_limit"


@dataclasses.dataclass(frozen=True)
class HeldTurn:
    """追送が拒否された場合に戻す保留結果。待機対象は現物を維持して完了観測を巻き戻さない。"""

    result: dict[str, Any]
    deadline: float | None
    turn_seq: int
    turn_id: str
    started_at: str

    @classmethod
    def capture(cls, session: SessionState) -> HeldTurn | None:
        """保留中だけ復元に必要なturn情報を退避する。"""
        if not session.awaiting_auto_resume or session.pending_result is None or session.auto_resume_deadline is None:
            return None
        return cls(session.pending_result, session.auto_resume_deadline, session.turn_seq, session.turn_id, session.started_at)

    def restore(self, session: SessionState) -> None:
        """未受理の追送を取り消し、同じ待機対象の観測を継続できる保留へ戻す。"""
        session.pending_result = self.result
        session.auto_resume_deadline = self.deadline
        session.awaiting_auto_resume = True
        session.turn_seq = self.turn_seq
        session.turn_id = self.turn_id
        session.started_at = self.started_at
        session.status = "running"
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.turn_start_sent = False
        session.reply_turn_started = False
        session.reply_retryable = True
        session.agent_message = self.result["agent_message"]
        session.error = self.result["error"]
        session.touch()


def cli_turn_may_continue(session: SessionState) -> bool:
    """Claude Code CLIが次のturnを開始し得る状態と報告しているかを返す。

    CLIは`session_state_changed`の`idle`を、保留した結果の送出と背景エージェントの待機を終えて
    次のturnが発生しないと確定した時点で発行する（Claude Code 2.1.289のスキーマ記述
    「authoritative turn-over signal」）。turnの終了前にキューへ入った完了通知は、`ResultMessage`の
    後に`idle`を送らず次のturnを開始させるため、`ResultMessage`の受信時点の最新の報告は`running`のままとなる。
    報告を一度も受けていないsessionでは判定に使わない。
    """
    return session.cli_turn_state is not None and session.cli_turn_state != "idle"


def has_pending_auto_resume_targets(session: SessionState) -> bool:
    """自動再開が追跡する子session、Claude taskまたはCLIの次のturnが残るかを返す。

    Claude・Codex backendとMCP層は、開始、解除、再開の全条件で本述語だけを使う。
    追跡集合（`live_tasks`・`live_child_session_ids`）だけでは、Stop hookの実行中などturnの最終応答から
    `ResultMessage`までの間にバックグラウンドタスクが終わった場合を判別できない。その完了通知で集合は空になるが、
    CLIは`ResultMessage`の後に完了通知の再開turnを開始する。このためCLIのturn状態の報告も判定へ加える。
    一方、シェルのバックグラウンドタスクが動いている間もCLIは`idle`を報告するため、追跡集合も残す。
    観測した版と順序は`docs/development/audit-records.md`
    「agent-toolkit/agent_toolkit/_agents_server/claude.py：結果の保留とturn状態の報告：2026年10月4日」にある。
    """
    return bool(session.live_tasks or session.live_child_session_ids) or cli_turn_may_continue(session)


def finalize_pending_result(
    session: SessionState,
    *,
    touch: bool = True,
    keep_resume_chain: bool = False,
    unobserved_sessions: Iterable[str] = (),
) -> None:
    """保留したturn結果を公開可能な終端状態へ移す。

    過負荷の自動継続と利用上限の解除待ちの継続を送る処理だけが`keep_resume_chain`を真にし、連鎖の回数と開始時刻を引き継ぐ。
    それ以外（`kill`、期限を持たない利用上限・過負荷の保留への委譲元の`send_message`、期限到来、ストリーム終端）は連鎖を終える。
    子session・バックグラウンドタスクの完了待ちの保留への`send_message`は本関数で確定せず、待機対象を引き継いで新しいturnを開始する。
    連鎖を終える確定では、確定の時点で稼働中のバックグラウンドタスクと終端を観測していない孫sessionを`error`へ記録し、
    待機対象が残ったまま確定したことを`heldResultFinalized`で示す。
    保留した結果は待機表明であり、待機対象が残るまま公開する結果は再開turnの結果ではないことを委譲元へ示すためである。
    追跡集合から既に外した未観測の孫sessionは`unobserved_sessions`で渡す。既存の`error`の内容は保つ。
    """
    result = session.pending_result
    if result is None:
        raise RuntimeError("auto-resume wait has no pending result")
    unfinished_tasks = set(session.live_tasks)
    unobserved = set(session.live_child_session_ids) | set(unobserved_sessions)
    session.overload_resume_at = None
    session.usage_limit_resume_at = None
    if not keep_resume_chain:
        clear_overload_resume(session)
        clear_usage_limit_wait(session)
    session.awaiting_auto_resume = False
    session.auto_resume_deadline = None
    session.pending_result = None
    session.live_child_session_ids.clear()
    session.status = result["status"]
    session.agent_message = result["agent_message"]
    session.error = result["error"]
    session.turn_completed = True
    session.turn_start_ambiguous = False
    if not keep_resume_chain and (unfinished_tasks or unobserved):
        _merge_error_identifiers(session, UNFINISHED_BACKGROUND_TASKS_KEY, unfinished_tasks)
        _merge_error_identifiers(session, UNOBSERVED_SESSIONS_KEY, unobserved)
        assert isinstance(session.error, dict)
        session.error[HELD_RESULT_FINALIZED_KEY] = True
    if touch:
        session.touch()


def is_overload_failure(session: SessionState) -> bool:
    """turnがCodexのモデルの過負荷で失敗したかを返す。"""
    return (
        session.status == "failed"
        and isinstance(session.error, dict)
        and session.error.get("codexErrorInfo") == OVERLOAD_ERROR_INFO
    )


def clear_overload_resume(session: SessionState) -> None:
    """過負荷による自動継続の連鎖を終える。"""
    session.overload_resume_count = 0
    session.overload_resume_at = None
    session.overload_first_at = None


def begin_overload_resume_wait(session: SessionState, result: dict[str, Any]) -> bool:
    """過負荷で終端したturnの結果を保留し、待機後の継続を予定する。

    上限に達していれば保留せずに連鎖を終えて偽を返し、呼び出し元は結果をそのまま公開する。
    待機中は既存の`api_error`へ種別`serverOverloaded`とHTTP状態の不明を記録して状態の読者へ公開する。
    """
    count = session.overload_resume_count
    if count >= len(OVERLOAD_RESUME_DELAYS_SECONDS):
        clear_overload_resume(session)
        return False
    session.pending_result = result
    session.awaiting_auto_resume = True
    session.auto_resume_deadline = None
    session.overload_resume_at = asyncio.get_running_loop().time() + OVERLOAD_RESUME_DELAYS_SECONDS[count]
    session.overload_resume_count = count + 1
    if session.overload_first_at is None:
        session.overload_first_at = utc_now()
    session.api_error = {
        "type": OVERLOAD_ERROR_INFO,
        "http_status": None,
        "first_at": session.overload_first_at,
        "count": session.overload_resume_count,
    }
    state.notify_touch_listeners()
    return True


def clear_usage_limit_wait(session: SessionState) -> None:
    """利用上限の解除待ちの連鎖を終える。"""
    session.usage_limit_wait_count = 0
    session.usage_limit_resume_at = None
    session.usage_limit_first_at = None


def begin_usage_limit_wait(session: SessionState, result: dict[str, Any]) -> bool:
    """Weekly limitか5時間の利用上限で失敗したturnの結果を保留し、解除後の継続を予定する。

    最後に報告された利用枠が待機の対象でなければ保留せずに連鎖を終えて偽を返し、呼び出し元は従来どおり扱う。
    保留した結果の`error.usageLimit`と`api_error`へ種類と解除予定時刻を記録し、状態の読者へ解除待ちを公開する。
    待機は`auto_resume_deadline`と`retention_deadline`の対象にせず、回数でも打ち切らない。
    """
    limit = session.usage_limit
    if result.get("status") != "failed" or limit is None or not limit.is_wait_target:
        clear_usage_limit_wait(session)
        return False
    original_error = result.get("error")
    error: dict[str, Any] = (
        dict(original_error)
        if isinstance(original_error, dict)
        else {"message": str(original_error or "Claude usage limit reached")}
    )
    resets_at = limit.resets_at_iso()
    error["usageLimit"] = {"type": limit.limit_type, "resetsAt": resets_at}
    result = {**result, "error": error}
    session.pending_result = result
    session.awaiting_auto_resume = True
    session.auto_resume_deadline = None
    session.usage_limit_resume_at = asyncio.get_running_loop().time() + limit.delay_seconds(time.time())
    session.usage_limit_wait_count += 1
    if session.usage_limit_first_at is None:
        session.usage_limit_first_at = utc_now()
    http_status = error.get("apiErrorStatus")
    session.api_error = {
        "type": USAGE_LIMIT_ERROR_TYPE,
        "http_status": http_status if isinstance(http_status, int) else None,
        "first_at": session.usage_limit_first_at,
        "count": session.usage_limit_wait_count,
        "limit_type": limit.limit_type,
        "resets_at": resets_at,
    }
    state.notify_touch_listeners()
    return True


def begin_auto_resume_wait(session: SessionState, result: dict[str, Any]) -> float:
    """終端結果を保留し、自動再開の待機期限を返す。"""
    session.pending_result = result
    session.awaiting_auto_resume = True
    deadline = asyncio.get_running_loop().time() + AUTO_RESUME_DEADLINE_SECONDS
    session.auto_resume_deadline = deadline
    return deadline


def release_auto_resume_hold(session: SessionState) -> bool:
    """次のturnの開始を観測した時点で、前のturnの自動再開待ちの保留を解除し、解除したかを返す。

    保留した結果は前のturnの待機表明であり、その待機期限は完了通知を待つ間だけに作用させる。
    通知で次のturnが始まった後も残すと、backendの待機ループとMCP層の常駐監視が期限の到来で
    実行中のturnより先に古い待機表明を公開する。次のturnの結果は、そのturnの`ResultMessage`で改めて保留か確定へ進む。
    バックグラウンドタスクと孫sessionの追跡、自動再開の消費の記録は、それぞれの寿命に従うため変えない。
    利用上限の解除待ちと過負荷の継続待ちは期限を持たず、MCP層が送る継続の処理で解除するため対象から外す。
    """
    if not session.awaiting_auto_resume or session.auto_resume_deadline is None:
        return False
    session.pending_result = None
    session.awaiting_auto_resume = False
    session.auto_resume_deadline = None
    return True


def record_unobserved_sessions(session: SessionState, session_ids: set[str]) -> None:
    """未観測の孫session識別子を既存のerror項目へ併合する。"""
    _merge_error_identifiers(session, UNOBSERVED_SESSIONS_KEY, session_ids)
    session.touch()


def _merge_error_identifiers(session: SessionState, key: str, identifiers: set[str]) -> None:
    """識別子の集合を既存のerrorの`key`項目へ併合する。集合が空ならerrorを変えない。"""
    if not identifiers:
        return
    merged = set(identifiers)
    current = session.error
    error: dict[str, Any]
    if isinstance(current, dict):
        error = dict(current)
    elif nonempty_error(current):
        error = {"message": str(current)}
    else:
        error = {}
    existing_identifiers = error.get(key)
    if isinstance(existing_identifiers, list):
        merged.update(item for item in existing_identifiers if isinstance(item, str))
    error[key] = sorted(merged)
    session.error = error


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
