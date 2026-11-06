"""pytestが保持する検証の実行枠を、試験の区切りで待機中の別の検証へ譲る。

pytestはホスト共有の実行枠（pyfltrの`execution_slot`）を全寿命で保持する。長い試験群の間に別の作業の短い検証や
commit hookが待ち続けないよう、子の処理を回収した試験の区切りで待機者へ譲り、取り直してから残りの試験を続ける。

- xdistのcontrollerが最外側で枠を取得した場合: 所有スレッドが`cooperative_hold`を保持し、workerへ参加券を渡す。
  workerは試験の後に自分の子孫が残っていなければ区切りを通知し、全workerが区切りで止まった時点で所有スレッドが譲る。
  参加券は`pytest_configure_node`で`workerinput`へ渡す（参加券は環境変数から自動採用されないため）。
- xdistを使わない実行: 試験を実行する主スレッドが`hold`を保持し、試験の区切りで`yield_to_waiters`を呼ぶ。
- 祖先（pyfltr・`atk run-command`など）から所有を継承した場合: 参加券を発行せず、従来の`hold`で排他を保つ。
  継承した子から祖先の所有を単独で解除すると、祖先が起動した別の子と待機者の実行が重なるためである。

workerの参加区間は全workerの終了後（pytestのcleanup）に閉じる。xdistはsessionの終了時にworkerを終了させてから
cleanupへ進むため、終了・回収前のworkerの参加を外して譲渡を許すことがない。
"""

import collections.abc
import contextlib
import os
import queue
import threading
from typing import Any

import pytest
from pyfltr.command import execution_slot

from agent_toolkit._common import process_tree

# workerinputで参加券を渡すキー。
TICKET_KEY = "agent_toolkit_execution_slot_participant"
# 所有スレッドが参加者の停止を確かめる間隔（秒）。pyfltrの待機の再試行間隔と同程度にする。
_CHECK_INTERVAL = 0.2


class CooperativeOwner:
    """xdistのcontroller内で協調所有を保持し、参加券の発行と区切りでの譲渡を担うスレッド。

    `cooperative_hold`と`checkpoint`・`participant`は保持したスレッドから呼ぶ契約のため、専用のスレッドへ集約する。
    """

    def __init__(self, on_wait: collections.abc.Callable[[execution_slot.SlotOwner | None], None]) -> None:
        self._on_wait = on_wait
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._requests: queue.Queue[queue.Queue[str]] = queue.Queue()
        self._error: BaseException | None = None
        self.inherited = False
        self._thread = threading.Thread(target=self._run, name="pytest-execution-slot", daemon=True)

    def start(self) -> None:
        """枠を取得するまで待つ。取得できず祖先から継承した場合は`inherited`を真にして戻る。"""
        self._thread.start()
        try:
            # KeyboardInterruptを主スレッドで受けられるよう、短い間隔で待つ。
            while not self._ready.wait(0.5):
                pass
        except BaseException:
            self.close()
            raise
        if self._error is not None:
            raise self._error

    def ticket(self) -> str | None:
        """workerへ渡す参加券を発行する。継承時と停止後は`None`を返す。"""
        if self.inherited or self._stop.is_set() or not self._thread.is_alive():
            return None
        reply: queue.Queue[str] = queue.Queue()
        self._requests.put(reply)
        while self._thread.is_alive():
            with contextlib.suppress(queue.Empty):
                return reply.get(timeout=0.5)
        return None

    def close(self) -> None:
        """参加区間と協調所有を閉じてスレッドの終了を待つ。"""
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        entered = False
        try:
            with execution_slot.cooperative_hold(
                label="pytest", should_abort=self._stop.is_set, on_wait=self._on_wait
            ) as coordinator:
                entered = True
                self._ready.set()
                with contextlib.ExitStack() as participants:
                    while not self._stop.is_set():
                        while True:
                            try:
                                reply = self._requests.get_nowait()
                            except queue.Empty:
                                break
                            reply.put(participants.enter_context(coordinator.participant()))
                        coordinator.checkpoint()
                        self._stop.wait(_CHECK_INTERVAL)
        except ValueError:
            if entered:
                raise
            # 祖先から継承した枠は協調所有者へ変えられない。主スレッドが従来のholdで排他を保つ。
            self.inherited = True
        except BaseException as error:  # noqa: BLE001  # 主スレッドへ取得前の失敗を渡すため全例外を受ける
            if not entered:
                self._error = error
        finally:
            self._ready.set()


class TicketDistribution:
    """xdistのcontrollerで、起動するworkerごとに参加券を`workerinput`へ渡す。"""

    def __init__(self, owner: CooperativeOwner) -> None:
        self._owner = owner

    def pytest_configure_node(self, node: Any) -> None:
        """workerの起動前に参加券を割り当てる。"""
        ticket = self._owner.ticket()
        if ticket is not None:
            node.workerinput[TICKET_KEY] = ticket


class BoundaryYield:
    """試験の区切りで、子孫が残っていなければ待機中の検証へ実行枠を譲る。"""

    def __init__(self, baseline: collections.abc.Callable[[], set[int]]) -> None:
        self._baseline = baseline

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem: pytest.Item | None) -> Any:
        """試験の後始末とtimeoutの解除を終えた後に譲る。"""
        del item, nextitem  # noqa
        result = yield
        if execution_slot.yield_requested():
            ignored = self._baseline()
            # 試験が起動した子が残る間は譲らない。子の稼働中に待機者の検証が重なるためである。
            if not [child for child in process_tree.collect_descendants(os.getpid()) if child.pid not in ignored]:
                execution_slot.yield_to_waiters()
        return result
