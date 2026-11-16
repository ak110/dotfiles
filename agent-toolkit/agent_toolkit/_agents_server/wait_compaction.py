"""背景waitの保留中だけ、所有タスクの入力キューへ圧縮を1回予約する。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Self

from agent_toolkit._agents_server import context_usage, resume_waits
from agent_toolkit._agents_server.state import SessionState

_LOG = logging.getLogger("agent-toolkit.agents-server.compaction")


def wait_key(session: SessionState) -> str | None:
    """有効な背景待機をエンジンとturnで識別し、対象の増減では同一性を変えない。

    予約・使用量照会・所有キューのawait中にも対象集合は変わる。
    配送側もこの判定を使い、同一turnに対象が残る間は同じ予約を有効とする。
    """
    if session.terminal or session.interrupt_requested or not resume_waits.is_background_completion_wait(session):
        return None
    if session.engine == "claude":
        children = session.live_child_session_ids | session.live_tasks.keys()
        if session.cli_turn_state != "idle" or not children:
            return None
        return json.dumps(["claude", session.turn_seq])
    if session.engine == "codex" and session.turn_completed:
        children = session.live_child_session_ids | session.codex_live_subagent_thread_ids | session.live_tasks.keys()
        return json.dumps(["codex", session.turn_seq]) if children else None
    return None


class WaitCompaction:
    """1つの所有タスクに結び付いた予約と、同じ待機の再実行抑止を保持する。"""

    def __init__(
        self,
        get_usage: Callable[[], Awaitable[Mapping[str, object]]],
        enqueue: Callable[[str], Awaitable[object]],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._get_usage = get_usage
        self._enqueue = enqueue
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._task_key: str | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._used: set[str] = set()
        self._paused = False
        self._reservation_key: str | None = None
        self._deadline: float | None = None

    def checkpoint(self) -> dict[str, object]:
        """同じOS起動中の移譲へ渡すJSON値を返す。旧所有者は移譲前にcloseする。

        単調時計の期限を渡し、転送と再接続に掛かった時間も予約の経過へ含める。
        taskや配送中の処理は複製せず、配送開始済みの待機はusedで抑止する。
        """
        return {
            "used": sorted(self._used),
            "paused": self._paused,
            "reservation_key": self._reservation_key,
            "deadline": self._deadline,
        }

    def restore(self, checkpoint: Mapping[str, object]) -> None:
        """休止中の新所有者へ状態だけを復元し、observeまで予約を開始しない。"""
        if self._tasks:
            raise RuntimeError("予約の実行中には復元できません")
        used = checkpoint.get("used")
        paused = checkpoint.get("paused")
        key = checkpoint.get("reservation_key")
        deadline = checkpoint.get("deadline")
        if (
            not isinstance(used, list)
            or not all(isinstance(item, str) for item in used)
            or not isinstance(paused, bool)
            or (key is not None and not isinstance(key, str))
            or (key is None) != (deadline is None)
            or (
                deadline is not None
                and (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline))
            )
        ):
            raise ValueError("待機圧縮のcheckpointが不正です")
        self._used = set(used)
        self._paused = paused
        self._reservation_key = key
        self._deadline = float(deadline) if deadline is not None else None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    def observe(self, session: SessionState) -> None:
        """受信済みの状態から予約し、SDKの読取ループを占有しない。"""
        tokens = context_usage.valid_number(session.context_tokens)
        window = context_usage.valid_number(session.context_window)
        if tokens is not None and window is not None and window > 0 and tokens < max(400_000, window * 0.4):
            self._paused = False
        key = wait_key(session)
        if self._task is not None and not self._task.done():
            # 同じturnの一部終了で待機時間を延ばさず、配送開始後の圧縮はbackendに所有させる。
            if (key is not None and key == self._task_key) or self._task_key in self._used:
                return
            self._task.cancel()
            self._task = None
        if key is None or key in self._used:
            self._reservation_key = None
            self._deadline = None
            return
        delay = 60.0
        if self._reservation_key != key or self._deadline is None:
            self._deadline = time.monotonic() + 60
        else:
            delay = max(0.0, self._deadline - time.monotonic())
        self._reservation_key = key
        self._task_key = key
        self._task = asyncio.create_task(self._compact(session, key, delay))
        self._tasks.add(self._task)
        self._task.add_done_callback(self._tasks.discard)

    async def _compact(self, session: SessionState, key: str, delay: float) -> None:
        try:
            await self._sleep(delay)
            if asyncio.current_task() is not self._task or wait_key(session) != key:
                return
            usage = await self._get_usage()
            tokens = context_usage.valid_number(usage.get("totalTokens"))
            window = context_usage.valid_number(usage.get("rawMaxTokens"))
            if tokens is None or window is None or window <= 0:
                return
            if tokens < max(400_000, window * 0.4):
                self._paused = False
                return
            if not self._paused and wait_key(session) == key and asyncio.current_task() is self._task:
                self._used.add(key)
                self._paused = True
                await self._enqueue(key)
        except Exception as error:  # pylint: disable=broad-exception-caught
            # 自動の付随処理の失敗は、同じ待機を再試行せず通常の結果回収へ戻す。
            self._used.add(key)
            _LOG.debug("wait_compaction_failed session_id=%s error=%s", session.session_id, error)
        finally:
            if asyncio.current_task() is self._task:
                self._reservation_key = None
                self._deadline = None

    async def close(self) -> None:
        """SDKを切断する前に、照会・配送を含めて予約タスクを回収する。"""
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
