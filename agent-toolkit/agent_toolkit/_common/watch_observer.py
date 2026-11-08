"""process-loop（`atk wi process-loop`）と`atk serve`のファイル監視を開始する。

OSの通知資源を確保できなければポーリング監視へ切り替える。

watchdogが標準で選ぶObserver（Linuxでは`InotifyObserver`）は監視先ごとにinotifyのインスタンスを確保し、
その上限（`/proc/sys/fs/inotify/max_user_instances`）は同じOSアカウントの並行作業と共有される。
上限に達すると`start()`が`OSError`を送出し、監視を待機の補助に使う処理（process-loopの待機、
`atk serve`の変更通知）まで止まる。監視は補助機能であるため、同じ監視先を`PollingObserver`で開始し直して
主機能を続け、切り替えたことを呼び出し元のログ出力で警告させる。

新しい常駐の監視を書くときは、`Observer.start()`を直接呼ばず本モジュールの`start_observer`を使う。
開始失敗の扱いが箇所ごとに分かれると、同じ資源不足で一部の処理だけが止まるためである。
"""

import collections.abc

import watchdog.observers
import watchdog.observers.polling
from watchdog.observers.api import BaseObserver

POLLING_INTERVAL_SECONDS = 5.0
"""ポーリング監視の走査間隔秒。

private-notes（約12,000ファイル）の再帰スナップショットは2026-11-07の実測で0.07〜0.10秒で、
5秒間隔ならCPU負荷は約2%以下に収まる。process-loopの所有解放待ち（3秒）の検知は最大で1間隔遅れるが、
切替はinotifyの上限到達時の縮退に限る。
"""


def start_observer(
    schedule: collections.abc.Callable[[BaseObserver], object],
    *,
    on_fallback: collections.abc.Callable[[OSError], None],
    observer: BaseObserver | None = None,
) -> BaseObserver:
    """watchdogが標準で選ぶObserverへ`schedule`で監視先を登録して開始し、開始できなければポーリング監視で開始し直す。

    `schedule`は渡されたObserverへ監視先を登録する。切替時は新しい`PollingObserver`へ同じ登録をやり直し、
    `on_fallback`へ元の例外を渡す。開始に失敗したObserverは停止して回収してから切り替える。
    ポーリング監視の開始も失敗した場合は、その例外を送出する（監視先が無いなど、資源不足以外の原因を隠さない）。
    `observer`を渡すと最初の試行にそれを使い、省略時は`watchdog.observers.Observer`を作る。
    戻り値のObserverは開始済みで、停止・joinは呼び出し元が所有する。
    """
    try:
        if observer is None:
            observer = watchdog.observers.Observer()
        schedule(observer)
        observer.start()
    except OSError as error:
        if observer is not None:
            _discard(observer)
        polling = watchdog.observers.polling.PollingObserver(timeout=POLLING_INTERVAL_SECONDS)
        try:
            schedule(polling)
            polling.start()
        except BaseException:
            _discard(polling)
            raise
        on_fallback(error)
        return polling
    except BaseException:
        if observer is not None:
            _discard(observer)
        raise
    return observer


def fallback_message(error: OSError, *, restart: str) -> str:
    """切替の警告文を返す。`restart`は通常の監視へ戻すために再起動する対象の名前とする。"""
    return (
        f"ファイル変更の監視を開始できないため、{POLLING_INTERVAL_SECONDS:g}秒間隔のポーリング監視へ切り替えた: {error}。"
        f"同じOSアカウントのinotifyの使用（`/proc/sys/fs/inotify/max_user_instances`の上限）を減らした後に"
        f"{restart}を再起動すると、通常の監視へ戻る"
    )


def _discard(observer: BaseObserver) -> None:
    """開始に失敗したObserverを停止し、開始済みのthreadだけをjoinする。"""
    observer.stop()
    if observer.ident is not None:
        observer.join()
