"""xdist 3.8の停止理由をworkerへ伝え、失敗上限で未実行キューを打ち切る。

上流PR https://github.com/pytest-dev/pytest-xdist/pull/1395 の局所適応。
修正を含む安定版へ更新し、同じ早期停止・正常完了試験を通した時点で本モジュールを撤去する。
"""

# MIT License
#
# Copyright (c) 2010 Holger Krekel and contributors.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import contextlib
import time
from typing import Any

import pytest
from xdist.dsession import DSession
from xdist.workermanage import WorkerController


def _shutdown(node: WorkerController, shouldstop: bool | str = False) -> None:
    if not node._down:  # pylint: disable=protected-access
        with contextlib.suppress(OSError):
            node.sendcommand("shutdown", shouldstop=shouldstop)
        node._shutdown_sent = True  # pylint: disable=protected-access


def _trigger_shutdown(controller: DSession) -> None:
    if not controller.shuttingdown:
        controller.log("triggering shutdown")
        controller.shuttingdown = True
        assert controller.sched is not None
        for node in controller.sched.nodes:
            _shutdown(node, controller.shouldstop)


def _patch_worker(worker: Any, patch: pytest.MonkeyPatch) -> None:
    original_command = worker.handle_command
    title = worker.run_one_test.__func__.__globals__["worker_title"]

    def handle_command(command: Any) -> None:
        if isinstance(command, tuple) and command[0] == "shutdown" and command[1].get("shouldstop"):
            worker.session.shouldstop = command[1]["shouldstop"]
        original_command(command)

    def run_one_test() -> None:
        assert isinstance(worker.nextitem_index, int)
        worker.item_index = worker.nextitem_index
        worker.nextitem_index = worker.torun.get()
        # lookaheadの待機中に停止要求が届く場合も、選択済みだが未開始の試験を実行しない。
        if worker.session.shouldstop:
            return
        items = worker.session.items
        item = items[worker.item_index]
        nextitem = items[worker.nextitem_index] if isinstance(worker.nextitem_index, int) else None
        title(f"[pytest-xdist running] {item.nodeid}")
        start = time.perf_counter()
        worker.config.hook.pytest_runtest_protocol(item=item, nextitem=nextitem)
        duration = time.perf_counter() - start
        title("[pytest-xdist idle]")
        worker.sendevent("runtest_protocol_complete", item_index=worker.item_index, duration=duration)

    patch.setattr(worker, "handle_command", handle_command)
    patch.setattr(worker, "run_one_test", run_one_test)


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """controllerとexecnetが実際に登録したworkerだけを、同じpytestの寿命で補う。"""
    patch = pytest.MonkeyPatch()
    session.config.add_cleanup(patch.undo)
    patch.setattr(WorkerController, "shutdown", _shutdown)
    patch.setattr(DSession, "triggershutdown", _trigger_shutdown)
    for plugin in session.config.pluginmanager.get_plugins():
        # execnetはremote.pyを__channelexec__として実行するため、通常importの同名クラスとは実体が異なる。
        if type(plugin).__name__ == "WorkerInteractor" and hasattr(session.config, "workerinput"):
            _patch_worker(plugin, patch)
