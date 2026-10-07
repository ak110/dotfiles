"""`atk wi process-loop`の待機中の変更の監視と、private-notesの同期。"""

import pathlib
import subprocess
import threading

import watchdog.events
import watchdog.observers

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_STATE_INBOX, WI_STATE_PROCESSING
from agent_toolkit._common import next_action as _next_action

# 読み取り由来の`FileOpenedEvent`・`FileClosedNoWriteEvent`を除外した監視対象イベント型。
WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)


# 主待機のタイムアウト秒（他端末からのAWI投入を`remote`同期で拾う間隔）。
_POLL_INTERVAL_SEC = 600.0


# 変更検知後、追加イベント発火が無くなるまでの畳み込み待機秒
# （1回のファイル操作で複数イベントが連続発火するという観測に対応する）。
_DEBOUNCE_SEC = 3.0


class _ChangeHandler(watchdog.events.FileSystemEventHandler):
    """inbox配下の`.md`変更検知時に`change_event`をsetするハンドラ。"""

    def __init__(self, change_event: threading.Event) -> None:
        super().__init__()
        self._change_event = change_event

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """監視対象イベント型・非ディレクトリ・`.md`拡張子の全条件を満たす場合にsetする。"""
        if not isinstance(event, WATCHED_EVENT_TYPES):
            return
        if event.is_directory:
            return
        if pathlib.Path(str(event.src_path)).suffix != ".md":
            return
        self._change_event.set()


def wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> bool:
    """watchdogでinbox配下を監視し、変更検知またはタイムアウトまで待機する。

    変更検知時はデバウンス窓（3秒）で追加イベントを畳み込んでから返る
    （他端末書き込みは10分タイムアウト側のremote同期で拾うため、変更検知時は同期しない）。
    タイムアウト時は他端末投入を反映するため`_wi_sync.repo_lock`保持下で`_wi_sync.pull`する。
    他プロセスとの一時的な競合・ネットワーク断等で`_wi_sync.pull`が失敗した場合は例外を捕捉して
    stderrへ警告出力し、常駐ループの待機動作を続ける。
    戻り値は`True`=変更検知で復帰、`False`=タイムアウトで復帰を表す。
    呼び出し元は`False`復帰時のみ常駐コードの更新チェック（`_pl_update.check_and_restart_on_update`）を行う。
    """
    del target_repo_id  # 現状の監視粒度ではrepo単位フィルタは行わない
    change_event = threading.Event()
    observer = watchdog.observers.Observer()
    handler = _ChangeHandler(change_event)
    _ensure_inbox_dirs(private_notes)
    observer.schedule(handler, str(private_notes / WI_STATE_PROCESSING), recursive=False)
    observer.schedule(handler, str(private_notes / WI_STATE_INBOX), recursive=False)
    observer.start()
    try:
        if change_event.wait(timeout=_POLL_INTERVAL_SEC):
            while True:
                change_event.clear()
                if not change_event.wait(timeout=_DEBOUNCE_SEC):
                    break
            return True
        try:
            with _wi_sync.repo_lock(private_notes):
                _wi_sync.pull(private_notes)
        except (subprocess.CalledProcessError, _atk_git_sync.RebaseInProgressError) as exc:
            _next_action.report(
                f"remote同期に失敗（待機ループ続行）: {exc}",
                next_action=f"対応不要（待機は継続した）。繰り返す場合は`git -C {private_notes} status`で同期状態を確認する",
            )
        return False
    finally:
        observer.stop()
        observer.join()


def pull_private_notes(private_notes: pathlib.Path) -> bool:
    """private-notesをlock下で同期し、処理開始に利用できる状態かを返す。"""
    try:
        with _wi_sync.repo_lock(private_notes):
            _wi_sync.pull(private_notes)
    except (subprocess.CalledProcessError, _atk_git_sync.RebaseInProgressError) as exc:
        _next_action.report(
            f"remote同期に失敗（子セッションを起動せず待機します）: {exc}",
            next_action=(
                f"`git -C {private_notes} status`で同期状態を確認し、競合やrebase中の状態を解消する。"
                "解消後はprocess-loopが次の反復で再試行する"
            ),
        )
        return False
    return True


def _ensure_inbox_dirs(private_notes: pathlib.Path) -> None:
    """watchdog監視対象のinboxディレクトリを事前作成する。"""
    (private_notes / WI_STATE_PROCESSING).mkdir(parents=True, exist_ok=True)
    (private_notes / WI_STATE_INBOX).mkdir(parents=True, exist_ok=True)
