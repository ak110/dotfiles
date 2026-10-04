"""serveと生存期間を共有する、タスクバー選択と1回の移動。

フックは短い入力記録だけを行い、UI Automationと位置変更はワーカーへ渡す。
クリックとWinEventの時刻を対応付け、中クリック時の前面を推測しない。
選択後に通常操作（左押下・右押下・サイドボタン押下・ホイール・キー押下）を受けたら選択を解除し、
その後の中クリックはアプリへ渡す。
"""

import logging
import queue
import threading
from typing import Any

from pytools.media_remote import _window_api, _window_uia

logger = logging.getLogger(__name__)


class WindowMover:
    """監視の開始・終了と、選択から1回の移動までを所有する。"""

    def __init__(self, *, api: Any = None, uia_factory: Any = None, events: Any = None) -> None:
        self.api = api if api is not None else _window_api.WindowsAPI()
        self.uia_factory = uia_factory if uia_factory is not None else _window_uia.UIAutomation
        self.events = events if events is not None else queue.Queue()
        self.running = threading.Event()
        self.lock = threading.Lock()
        self.selection: tuple[int, tuple[int, int]] | None = None
        self.suppress_release = False
        self.click_generation = 0
        self.pending_click: tuple[int, int] | None = None
        self.failure: BaseException | None = None
        self.worker: threading.Thread | None = None
        self.hooks: threading.Thread | None = None

    def __enter__(self) -> "WindowMover":
        self.running.set()
        ready = threading.Event()
        self.worker = threading.Thread(target=self._work, args=(ready,), name="media-window-worker")
        self.worker.start()
        ready.wait()
        try:
            if self.failure is not None:
                raise self.failure
            ready.clear()
            self.hooks = threading.Thread(target=self._hooks, args=(ready,), name="media-window-hooks")
            self.hooks.start()
            ready.wait()
            if self.failure is not None:
                raise self.failure
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_: Any) -> None:
        self.running.clear()
        with self.lock:
            self.selection = None
            self.pending_click = None
            self.suppress_release = False
        if self.hooks is not None and self.hooks.is_alive():
            self.api.stop_hooks()
            self.hooks.join()
        if self.worker is not None and self.worker.is_alive():
            self.events.put(None)
            self.worker.join()

    def on_mouse(self, message: int, point: tuple[int, int], time: int, root: str) -> bool:
        """マウスフックから受け取り、奪う中クリックだけTrueを返す。"""
        with self.lock:
            if not self.running.is_set():
                return False
            if message == _window_api.WM_LBUTTONDOWN:
                # タスクバーの別選択は、ワーカーが新しい対象を選択へ設定する。
                self._cancel_selection()
                self.events.put(("click", self.click_generation, time, point, root))
            elif message == _window_api.WM_LBUTTONUP:
                self.events.put(("release", self.click_generation, time))
            elif message in (
                _window_api.WM_RBUTTONDOWN,
                _window_api.WM_XBUTTONDOWN,
                _window_api.WM_MOUSEWHEEL,
                _window_api.WM_MOUSEHWHEEL,
            ):
                self._cancel_selection()
            elif message == _window_api.WM_MBUTTONDOWN:
                selected = self.selection
                self._cancel_selection()
                self.suppress_release = selected is not None
                if selected is not None:
                    self.events.put(("move", selected, point))
                    return True
            elif message == _window_api.WM_MBUTTONUP:
                suppress = self.suppress_release
                self.suppress_release = False
                return suppress
        return False

    def on_key(self) -> None:
        """キー操作を通常操作として扱い、選択と未解決のタスクバークリックを解除する。"""
        with self.lock:
            self._cancel_selection()

    def _cancel_selection(self) -> None:
        """lockの下で、選択と未解決のクリックを以降の中クリックから外す。"""
        self.click_generation += 1
        self.pending_click = None
        self.selection = None

    def on_window(self, event: int, hwnd: int, time: int) -> None:
        """登録されたWinEventから対象の情報だけをワーカーへ渡す。"""
        if self.running.is_set():
            self.events.put(("window", event, hwnd, time))

    def _hooks(self, ready: threading.Event) -> None:
        try:
            self.api.run_hooks(self.on_mouse, self.on_window, self.on_key, ready.set)
        except BaseException as error:
            self.failure = error
            logger.error("ウィンドウ移動の監視を開始・継続できません。serveを再起動してください。", exc_info=True)
        finally:
            self.running.clear()
            ready.set()

    def _work(self, ready: threading.Event) -> None:
        try:
            with self.api.physical_coordinates(), self.uia_factory() as uia:
                ready.set()
                while True:
                    item = self.events.get()
                    try:
                        if item is None:
                            break
                        if self.running.is_set():
                            self._process(item, uia)
                    except OSError as error:
                        logger.warning("ウィンドウを移動できません: %s。対象をタスクバーで選び直してください。", error)
                    finally:
                        self.events.task_done()
        except BaseException as error:
            self.failure = error
            logger.error("ウィンドウ移動の処理を開始・継続できません。serveを再起動してください。", exc_info=True)
        finally:
            self.running.clear()
            ready.set()

    def _process(self, item: tuple[Any, ...], uia: Any) -> None:
        if item[0] == "click":
            _, generation, time, point, root = item
            selected = uia.is_task_selection(point, root)
            with self.lock:
                if generation == self.click_generation:
                    self.pending_click = (generation, time) if selected else None
        elif item[0] == "release":
            _, generation, time = item
            with self.lock:
                if self.pending_click is not None and self.pending_click[0] == generation:
                    self.pending_click = (generation, time)
        elif item[0] == "window":
            _, event, hwnd, time = item
            with self.lock:
                if event == _window_api.EVENT_OBJECT_DESTROY:
                    if self.selection is not None and self.selection[0] == hwnd:
                        self.selection = None
                    return
                click = self.pending_click
            if click is None or (time - click[1]) & 0xFFFFFFFF >= 0x80000000:
                return
            # OSのクリック間隔を入力操作の時間幅として使う。キーボードで割り込まれた
            # 操作はon_keyで無効にし、遅延したイベントは選択待ちへの遷移から除く。
            if (time - click[1]) & 0xFFFFFFFF > self.api.click_interval_ms:
                with self.lock:
                    if self.pending_click == click:
                        self.pending_click = None
                return
            identity = self.api.identity(hwnd)
            if identity is not None:
                with self.lock:
                    if self.pending_click == click:
                        self.selection = (hwnd, identity)
                        self.pending_click = None
        elif item[0] == "move":
            _, (hwnd, identity), point = item
            self.api.move(hwnd, identity, point)
