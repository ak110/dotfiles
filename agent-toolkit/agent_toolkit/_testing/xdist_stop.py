"""xdist 3.8の停止理由と収集時の使用法エラーを、workerとcontrollerの間で補う。

上流PR https://github.com/pytest-dev/pytest-xdist/pull/1395 の局所適応。
停止理由の修正を含む安定版へ更新したら早期停止の適応を撤去する。
収集時のUsageErrorが元の文面と終了コードで中継される安定版へ更新したら、対応する結合試験を通して診断の適応も撤去する。
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
import sys
import time
from collections.abc import Generator
from typing import Any

import pytest
from xdist.dsession import DSession
from xdist.workermanage import WorkerController

_COLLECTION_USAGE_ERRORS = pytest.StashKey[list[str]]()


@pytest.hookimpl(wrapper=True)
def pytest_collection(session: pytest.Session) -> Generator[None, object, object]:
    """xdistが文面を中継しない収集時のUsageErrorを、終了時に転送するworkeroutputへ残す。"""
    try:
        return (yield)
    except pytest.UsageError as error:
        if hasattr(session.config, "workerinput"):
            session.config.workeroutput["collection_usage_errors"] = [str(message) for message in error.args]  # type: ignore[attr-defined]
        raise


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    """workerの収集時の使用法エラーをcontrollerの最終結果へ引き継ぐ。"""
    del error  # noqa
    messages = getattr(node, "workeroutput", {}).get("collection_usage_errors", [])
    errors = node.config.stash.setdefault(_COLLECTION_USAGE_ERRORS, [])
    errors.extend(message for message in messages if message not in errors)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """同じ引数を収集したworkerの診断を1回だけ報告し、pytest本来の使用法エラーとして終了する。"""
    errors = session.config.stash.get(_COLLECTION_USAGE_ERRORS, [])
    if errors:
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        print("次の操作: 引数のパス・node IDを確認して修正し、pytestを再実行してください。", file=sys.stderr)
        session.exitstatus = pytest.ExitCode.USAGE_ERROR


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
