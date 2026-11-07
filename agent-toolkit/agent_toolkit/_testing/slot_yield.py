"""pytestが保持する検証の実行枠を、試験の区切りで待機中の別の検証へ譲る。

pytestはホスト共有の実行枠（pyfltrの`execution_slot`）を全寿命で保持する。長い試験群の間に別の作業の短い検証や
commit hookが待ち続けないよう、子の処理を回収した試験の区切りで待機者へ譲り、取り直してから残りの試験を続ける。

- xdistのcontrollerが最外側で枠を取得した場合: 所有スレッドが`cooperative_hold`を保持し、workerへ参加券を渡す。
  workerは試験の後に自分の子孫が残っていなければ区切りを通知し、全workerが区切りで止まった時点で所有スレッドが譲る。
  参加券は`pytest_configure_node`で`workerinput`へ渡す（参加券は環境変数から自動採用されないため）。
- xdistを使わない実行: 試験を実行する主スレッドが`hold`を保持し、試験の区切りで`yield_to_waiters`を呼ぶ。
- 祖先（pyfltr・`atk run-command`など）から所有を継承した場合: 参加券を発行せず、従来の`hold`で排他を保つ。
  継承した子から祖先の所有を単独で解除すると、祖先が起動した別の子と待機者の実行が重なるためである。

pyfltrの協調所有者は、登録中の全参加者が区切りで停止した時点だけ譲る。終了した参加者の登録も停止として数えないため、
割り当てられた試験を先に終えたworkerの参加区間を残すと、区切りで止まった別のworkerが再開されずpytestが終了しない。
そこでworkerは自分の`hold`を抜けた後に完了印を書き、controllerは`pytest_testnodedown`の後にその印を確かめてから
該当workerの参加区間を閉じる。完了印より前に閉じないため、workerの後始末が終わる前に譲渡を許すことがない。
異常終了したworkerは完了印を書けないため、xdistが異常終了を通知した時点で閉じる。
残りの参加区間は全workerの終了後（pytestのcleanup）に閉じる。

各プロセスの最後の試験の後には譲らない。残りの試験が無い区切りで譲ると、取り直しが先に並んだ待機者の終了まで待ち、
全試験を終えたpytestの終了だけが遅れるためである。

1回のpytest実行が譲渡に費やす累計時間には上限（ini項目`execution_slot_yield_budget`、秒）を設け、達した後は譲らない。
取り直しは先に並んだ待機者の後になるため、長い検証が次々に並ぶと譲る側が際限なく進めなくなる。
譲る相手の種類や長さはpyfltrの公開APIで判別できないため、相手を選ばずに累計時間で打ち切る。
短い検証への譲渡は累計をほとんど消費しないため、元の目的（短い検証を区切りで先に通す）は保たれる。
上限の判定は譲渡が終わった時点で行い、進行中の1回は中断しない。
xdistでは協調所有者が上限到達を完了印と同じ一時ディレクトリの印でworkerへ伝え、workerは印があれば停止を通知しない。
印の書込前に停止を通知したworkerがあれば、協調所有者は最大1回の追加の譲渡でそのworkerを再開させる。
"""

import collections.abc
import contextlib
import os
import pathlib
import queue
import shutil
import tempfile
import threading
import time
from typing import Any

import pytest
from pyfltr.command import execution_slot

from agent_toolkit._common import process_tree

# workerinputで参加券を渡すキー。
TICKET_KEY = "agent_toolkit_execution_slot_participant"
# workerinputで完了印のパスを渡すキー。
DONE_KEY = "agent_toolkit_execution_slot_done"
# workerinputで譲渡の上限到達の印のパスを渡すキー。
EXHAUSTED_KEY = "agent_toolkit_execution_slot_exhausted"
# 譲渡に費やす累計時間の上限（秒）。ini設定`execution_slot_yield_budget`が無いときに使う。commit hookや限定検証の数回分を通し、
# 他レーンの全体検証のような長い検証へは高々1回で打ち切る長さにする。
DEFAULT_YIELD_BUDGET = 300.0
# 所有スレッドが参加者の停止を確かめる間隔（秒）。pyfltrの待機の再試行間隔と同程度にする。
_CHECK_INTERVAL = 0.2


class CooperativeOwner:
    """xdistのcontroller内で協調所有を保持し、参加券の発行と区切りでの譲渡を担うスレッド。

    `cooperative_hold`と`checkpoint`・`participant`は保持したスレッドから呼ぶ契約のため、専用のスレッドへ集約する。
    """

    def __init__(
        self,
        on_wait: collections.abc.Callable[[execution_slot.SlotOwner | None], None],
        budget: float = DEFAULT_YIELD_BUDGET,
    ) -> None:
        self._on_wait = on_wait
        self._budget = budget
        self._spent = 0.0
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._requests: queue.Queue[queue.Queue[str]] = queue.Queue()
        self._releases: queue.Queue[tuple[str, pathlib.Path | None]] = queue.Queue()
        self._error: BaseException | None = None
        self.inherited = False
        self.done_directory = pathlib.Path(tempfile.mkdtemp(prefix="pytest-execution-slot-"))
        # 上限到達をworkerへ伝える印。完了印と同じ一時ディレクトリに置き、`close`でまとめて消す。
        self.exhausted_marker = self.done_directory / "yield-exhausted"
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

    def release(self, ticket: str, done: pathlib.Path | None) -> None:
        """workerの参加区間を閉じるよう求める。`done`があれば、その完了印が書かれてから閉じる。"""
        self._releases.put((ticket, done))

    def close(self) -> None:
        """参加区間と協調所有を閉じてスレッドの終了を待つ。"""
        self._stop.set()
        try:
            self._thread.join()
        finally:
            shutil.rmtree(self.done_directory, ignore_errors=True)

    def _run(self) -> None:
        entered = False
        try:
            with execution_slot.cooperative_hold(
                label="pytest", should_abort=self._stop.is_set, on_wait=self._on_wait
            ) as coordinator:
                entered = True
                self._ready.set()
                with contextlib.ExitStack() as participants:
                    # 参加券ごとの区間。終了したworkerの区間だけを先に閉じられるよう、個別のExitStackに持つ。
                    entries: dict[str, contextlib.ExitStack] = {}
                    pending: list[tuple[str, pathlib.Path | None]] = []
                    while not self._stop.is_set():
                        while True:
                            try:
                                reply = self._requests.get_nowait()
                            except queue.Empty:
                                break
                            entry = participants.enter_context(contextlib.ExitStack())
                            ticket = entry.enter_context(coordinator.participant())
                            entries[ticket] = entry
                            reply.put(ticket)
                        while True:
                            try:
                                pending.append(self._releases.get_nowait())
                            except queue.Empty:
                                break
                        for ticket, done in list(pending):
                            if done is None or done.exists():
                                pending.remove((ticket, done))
                                entry = entries.pop(ticket, None)
                                if entry is not None:
                                    entry.close()
                        started = time.monotonic()
                        if coordinator.checkpoint():
                            self._spent += time.monotonic() - started
                            if self._spent >= self._budget and not self.exhausted_marker.exists():
                                self.exhausted_marker.write_text("", encoding="utf-8")
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
    """xdistのcontrollerで、起動するworkerごとに参加券と完了印のパスを`workerinput`へ渡す。"""

    def __init__(self, owner: CooperativeOwner) -> None:
        self._owner = owner
        self._tickets: dict[str, tuple[str, pathlib.Path]] = {}

    def pytest_configure_node(self, node: Any) -> None:
        """workerの起動前に参加券を割り当てる。"""
        ticket = self._owner.ticket()
        if ticket is not None:
            done = self._owner.done_directory / f"{node.gateway.id}.done"
            node.workerinput[TICKET_KEY] = ticket
            node.workerinput[DONE_KEY] = str(done)
            node.workerinput[EXHAUSTED_KEY] = str(self._owner.exhausted_marker)
            self._tickets[node.gateway.id] = (ticket, done)

    def pytest_testnodedown(self, node: Any, error: object) -> None:
        """workerの終了後に参加区間を閉じる。異常終了では完了印を待たない。"""
        assigned = self._tickets.pop(node.gateway.id, None)
        if assigned is not None:
            ticket, done = assigned
            self._owner.release(ticket, done if error is None else None)


def signal_done_after_hold(stack: contextlib.ExitStack, workerinput: dict[str, Any]) -> None:
    """workerが後で開く`hold`を抜けた後に完了印を書く処理を、先に`stack`へ登録する。

    `stack`は後に登録した処理から閉じるため、`hold`より前に登録すると`hold`の終了後に印を書く。
    """
    done = workerinput.get(DONE_KEY)
    if done is not None:
        stack.callback(pathlib.Path(done).write_text, "", encoding="utf-8")


class BoundaryYield:
    """試験の区切りで、子孫が残っていなければ待機中の検証へ実行枠を譲る。

    譲渡に費やした累計時間が`budget`秒に達した後と、協調所有者の上限到達の印（`exhausted_marker`）がある時は譲らない。
    """

    def __init__(
        self,
        baseline: collections.abc.Callable[[], set[int]],
        budget: float = DEFAULT_YIELD_BUDGET,
        exhausted_marker: pathlib.Path | None = None,
    ) -> None:
        self._baseline = baseline
        self._budget = budget
        self._exhausted_marker = exhausted_marker
        self._spent = 0.0

    def _exhausted(self) -> bool:
        return self._spent >= self._budget or (self._exhausted_marker is not None and self._exhausted_marker.exists())

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem: pytest.Item | None) -> Any:
        """試験の後始末とtimeoutの解除を終えた後に、残りの試験がある場合だけ譲る。"""
        del item  # noqa
        result = yield
        if nextitem is not None and not self._exhausted() and execution_slot.yield_requested():
            ignored = self._baseline()
            # 試験が起動した子が残る間は譲らない。子の稼働中に待機者の検証が重なるためである。
            if not [child for child in process_tree.collect_descendants(os.getpid()) if child.pid not in ignored]:
                started = time.monotonic()
                if execution_slot.yield_to_waiters():
                    self._spent += time.monotonic() - started
        return result
