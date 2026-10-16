"""`atk wi process-loop`の待機中の変更の監視と、private-notesの同期。"""

import contextlib
import dataclasses
import importlib
import os
import pathlib
import select
import subprocess
import sys
import threading
import time
import typing
from collections.abc import Callable, Generator

import watchdog.events
import watchdog.observers

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_log as _pl_log
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi_scan as _uwi_scan
from agent_toolkit._atk.wi.constants import WI_ACTIVE_STATES, WI_STATES, WI_TYPE_UWI, normalized_wi_type
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import session_activity as _session_activity
from agent_toolkit._git import remote as _git_remote

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


# 変更を検知した後、追加のイベントが発生しなくなるまで待つ秒数
# （1回のファイル操作で複数のイベントが続けて発生するという観測に対応する）。
_DEBOUNCE_SEC = 3.0

_START_GRACE_SEC = 600.0
_SESSION_RECHECK_SEC = 3.0

# 開始前待機中にEnterの入力を確かめる間隔秒数。
_INPUT_POLL_SEC = 0.2

# 入力取得の境界を選ぶOS名（`os.name`の値）。
_INPUT_PLATFORM = os.name


class _ConsoleKeys(typing.Protocol):
    """Windowsの対話コンソールからキーを取得する`msvcrt`の関数。"""

    def kbhit(self) -> bool:
        """読み取れるキーがあれば真を返す。"""
        raise NotImplementedError

    def getwch(self) -> str:
        """キーを1文字読み取る。"""
        raise NotImplementedError


class _StartWaitInput:
    """開始前待機の間だけ、同じ端末のEnterを待たずに読み取る。

    読取はOSの端末から行い、子CLIへ渡る入力を待機の外で消費しないよう`close`後は読まない。
    監視の開始前に届いていた入力と、解除に使ったEnterの後に続けて届いた入力は破棄し、解除の効力を次回の待機へ持ち越さない。
    POSIXは前景の端末で行単位の入力を、Windowsは対話コンソールのキーを対象にする。
    """

    def __init__(self, fd: int | None, console: _ConsoleKeys | None) -> None:
        self._fd = fd
        self._console = console
        self._closed = False
        self.entered = False

    def poll(self) -> bool:
        """Enterを受け取っていれば真を返す。入力が無ければ待たずに偽を返す。"""
        if self.entered or self._closed:
            return self.entered
        try:
            if self._console is not None:
                while self._console.kbhit():
                    if self._console.getwch() in {"\r", "\n"}:
                        self.entered = True
                        break
                if self.entered:
                    self.discard_pending()
                return self.entered
            assert self._fd is not None
            # バックグラウンドジョブが端末を読むとSIGTTINで停止するため、前景の間だけ読む。
            if os.tcgetpgrp(self._fd) != os.getpgrp():
                return False
            readable, _, _ = select.select([self._fd], [], [], 0)
            if not readable:
                return False
            data = os.read(self._fd, 4096)
        except OSError:
            self._closed = True
            return False
        if not data:
            # EOFは入力の終わりであり、以降は従来の待機だけを続ける。
            self._closed = True
            return False
        self.entered = b"\n" in data or b"\r" in data
        if self.entered:
            self.discard_pending()
        return self.entered

    def discard_pending(self) -> None:
        """端末に届いて未読の入力を破棄する。

        監視の開始前に届いた入力と、解除に使ったEnterの後に続けて届いた入力（重ね押しの2行目など）を
        今回や次回の開始前待機の解除に使わないためである。
        """
        try:
            if self._console is not None:
                while self._console.kbhit():
                    self._console.getwch()
                return
            assert self._fd is not None
            # バックグラウンドジョブが端末を操作するとSIGTTOUで停止するため、前景の間だけ破棄する。
            if os.tcgetpgrp(self._fd) != os.getpgrp():
                return
            termios = importlib.import_module("termios")
            termios.tcflush(self._fd, termios.TCIFLUSH)
        except AttributeError, ImportError, OSError, ValueError:
            return

    def close(self) -> None:
        """以降の読取を止める。"""
        self._closed = True


def _open_start_wait_input() -> _StartWaitInput | None:
    """入力可能な端末がある場合だけ監視を返す。非TTY、閉鎖済み、入力不能、POSIXのバックグラウンドジョブでは返さない。"""
    stream = sys.stdin
    try:
        if stream is None or not stream.isatty():
            return None
        if _INPUT_PLATFORM == "nt":
            return _StartWaitInput(None, typing.cast("_ConsoleKeys", importlib.import_module("msvcrt")))
        fd = stream.fileno()
        # 制御端末を持たない端末は前景・背景を判定できず、バックグラウンドジョブは端末を読めないため、どちらも監視しない。
        if os.tcgetpgrp(fd) != os.getpgrp():
            return None
    except AttributeError, ImportError, OSError, ValueError:
        return None
    return _StartWaitInput(fd, None)


def _open_discarding_pending() -> _StartWaitInput | None:
    """監視を開き、開始前に端末へ届いていた入力を破棄する。案内の表示後に入力されたEnterだけで解除するためである。"""
    opened = _open_start_wait_input()
    if opened is not None:
        opened.discard_pending()
    return opened


@contextlib.contextmanager
def _start_wait_input() -> Generator[Callable[[], _StartWaitInput | None]]:
    """最初の待機で監視を開き、期限・Enter・中断・例外のいずれでも監視を閉じてから抜ける。"""
    opened: list[_StartWaitInput | None] = []

    def current() -> _StartWaitInput | None:
        if not opened:
            opened.append(_open_discarding_pending())
        return opened[0]

    try:
        yield current
    finally:
        if opened and opened[0] is not None:
            opened[0].close()


@dataclasses.dataclass(frozen=True)
class StartDecision:
    """開始前の抑止理由と、同時評価した解除期限。"""

    reasons: tuple[str, ...]
    deadline: float | None
    extended: bool = False


class StartGate:
    """1つの常駐起動が観測したWI登録と活動猶予を保持する。"""

    def __init__(self, private_notes: pathlib.Path) -> None:
        self.private_notes = private_notes
        self._known_names = self._names()
        self._activity_deadline: float | None = None

    def _names(self) -> set[str]:
        return {path.name for state in WI_STATES for path in (self.private_notes / state).glob("*.md")}

    def started(self) -> None:
        """完了した開始判定の猶予を次のprocess-wiの1回の実行へ持ち越さない。"""
        self._activity_deadline = None
        self._known_names.update(self._names())

    def evaluate(self, target_repo_ids: set[str], *, activity: bool, now: float | None = None) -> StartDecision:
        """活動の期限とUWI更新の期限を加算せず、同時に判定する。"""
        timestamp = time.time() if now is None else now
        names = self._names()
        arrivals = names - self._known_names
        self._known_names.update(names)
        extended = bool(arrivals and self._activity_deadline is not None)
        if extended or (activity and self._activity_deadline is None):
            self._activity_deadline = timestamp + _START_GRACE_SEC
        reasons: list[str] = []
        deadlines: list[float] = []
        if activity and self._activity_deadline is not None and timestamp < self._activity_deadline:
            reasons.append("対象リポジトリへのWI投入作業が活動中")
            deadlines.append(self._activity_deadline)
        unanswered, latest_update = _uwi_updates(self.private_notes, target_repo_ids)
        if unanswered and timestamp < latest_update + _START_GRACE_SEC:
            reasons.append(f"未回答UWIが{unanswered}件")
            deadlines.append(latest_update + _START_GRACE_SEC)
        return StartDecision(tuple(reasons), max(deadlines) if deadlines else None, extended)


def _uwi_updates(private_notes: pathlib.Path, target_repo_ids: set[str]) -> tuple[int, float]:
    """回答後の終端移動も含む更新時刻と、未終端の未回答数を得る。"""
    canonical_cache: dict[str, str | None] = {}
    targets = {_git_remote.canonical_repo(repo, canonical_cache) for repo in target_repo_ids}
    unanswered = 0
    latest_update = 0.0
    for state in WI_STATES:
        for path in (private_notes / state).glob("*.md"):
            text = path.read_text(encoding="utf-8")
            parsed = parse_frontmatter(text)
            if parsed is None:
                continue
            metadata, _body = parsed
            if normalized_wi_type(metadata.get("type")) != WI_TYPE_UWI:
                continue
            repo = metadata.get("target_repo")
            if not isinstance(repo, str) or _git_remote.canonical_repo(repo, canonical_cache) not in targets:
                continue
            latest_update = max(latest_update, path.stat().st_mtime)
            if state in WI_ACTIVE_STATES and not _uwi_scan.is_uwi_answered(text):
                unanswered += 1
    return unanswered, latest_update


def wait_for_start_permission(private_notes: pathlib.Path, target_repo_ids: set[str], gate: StartGate) -> bool:
    """活動と回答の解除、WI更新、中断、Enterによる今回の待機の解除を開始前に再評価する。

    Enterは活動猶予とUWI猶予を一括で解除し、効力はこの呼び出しの中だけに留まる。
    中断要求はEnterより先に判定し、終了を優先する。
    """
    previous: StartDecision | None = None
    with _start_wait_input() as start_input:
        while True:
            if _pl_control.consume_process_loop_abort():
                return False
            entry = start_input() if previous is not None else None
            if entry is not None and entry.entered:
                print("Enterの入力により開始前の待機を解除しました。処理可能なWIを再評価します。", flush=True)
                _pl_log.append("start_wait_released", trigger="enter")
                return True
            activity = _submission_active(target_repo_ids)
            now = time.time()
            decision = gate.evaluate(target_repo_ids, activity=activity, now=now)
            if not decision.reasons:
                if previous is not None:
                    print("開始前の待機条件が解除されました。処理可能なWIを再評価します。", flush=True)
                    _pl_log.append("start_wait_released")
                return True
            assert decision.deadline is not None
            entry = start_input()
            if previous is None or (decision.reasons, decision.deadline) != (previous.reasons, previous.deadline):
                deadline = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(decision.deadline))
                reason = "、".join(decision.reasons)
                extension = "（新規WIの到着により延長）" if decision.extended else ""
                guide = "Enterで今回の待機を解除して開始できます。" if entry is not None else ""
                print(f"開始前に待機します: {reason}。解除期限: {deadline}{extension}。{guide}", flush=True)
                _pl_log.append("start_wait", reasons=reason, deadline=decision.deadline, extended=decision.extended)
                previous = decision
            wait_for_changes(
                private_notes,
                None,
                timeout=min(_SESSION_RECHECK_SEC, decision.deadline - now),
                state_directories=(
                    _session_activity.activity_directory(),
                    _session_activity.submission_directory(),
                    _pl_log.abort_path().parent,
                ),
                interrupt=entry.poll if entry is not None else None,
            )


def _submission_active(target_repo_ids: set[str]) -> bool:
    """対象リポジトリ集合へ投入するadd-awi-by-userのうち、自ループ以外で活動中の作業があるかを返す。

    同じoriginの別worktreeや別表記のURLを同一リポジトリとして扱うため、投入先の保存時と同じ正規化を通す。
    """
    canonical_cache: dict[str, str | None] = {}
    targets = {
        canonical for repo in target_repo_ids if (canonical := _git_remote.canonical_repo(repo, canonical_cache)) is not None
    }
    if not targets:
        return False
    return _session_activity.submission_activity(targets, excluded_pids={os.getpid()}).has_activity


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


class _StateChangeHandler(watchdog.events.FileSystemEventHandler):
    """活動と中断の状態ファイルが変化した時点で待機を解除する。"""

    def __init__(self, change_event: threading.Event) -> None:
        super().__init__()
        self._change_event = change_event

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """書込・移動・削除だけを再評価の契機にする。"""
        if isinstance(event, WATCHED_EVENT_TYPES) and not event.is_directory:
            self._change_event.set()


def wait_for_changes(
    private_notes: pathlib.Path,
    target_repo_id: str | None,
    *,
    timeout: float | None = None,
    state_directories: tuple[pathlib.Path, ...] = (),
    interrupt: Callable[[], bool] | None = None,
) -> bool:
    """保存状態と追加の状態ディレクトリを監視し、変更または期限まで待機する。

    変更検知時はデバウンス窓（3秒）内の追加イベントを1回の変更としてまとめてから返る
    （他端末書き込みは10分タイムアウト側のremote同期で拾うため、変更検知時は同期しない）。
    通常の同期間隔のタイムアウトでは、他端末投入を反映するためlock下でpullする。
    開始前の短い状態再評価へtimeoutを指定した場合は、そのたびにremote同期を繰り返さない。
    他プロセスとの一時的な競合・ネットワーク断等で`_wi_sync.pull`が失敗した場合は例外を捕捉して
    stderrへ警告出力し、常駐ループの待機動作を続ける。
    `interrupt`を指定した場合は短い間隔で呼び、真を返した時点で変更検知と同じく復帰する。
    戻り値は`True`=変更検知で復帰、`False`=タイムアウトで復帰を表す。
    呼び出し元は`False`復帰時のみ常駐コードの更新チェック（`_pl_update.check_and_restart_on_update`）を行う。
    """
    del target_repo_id  # 現状の監視粒度ではrepo単位フィルタは行わない
    change_event = threading.Event()
    observer = watchdog.observers.Observer()
    handler = _ChangeHandler(change_event)
    _ensure_inbox_dirs(private_notes)
    for state in WI_STATES:
        observer.schedule(handler, str(private_notes / state), recursive=False)
    state_handler = _StateChangeHandler(change_event)
    for directory in dict.fromkeys(state_directories):
        if directory.is_dir():
            observer.schedule(state_handler, str(directory), recursive=False)
    observer.start()
    try:
        if _wait_event(change_event, _POLL_INTERVAL_SEC if timeout is None else max(0.0, timeout), interrupt):
            if change_event.is_set():
                while True:
                    change_event.clear()
                    if not change_event.wait(timeout=_DEBOUNCE_SEC):
                        break
            return True
        if timeout is not None:
            return False
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


def _wait_event(event: threading.Event, timeout: float, interrupt: Callable[[], bool] | None) -> bool:
    """`event`か`interrupt`の成立まで最長`timeout`秒待ち、成立したかを返す。"""
    if interrupt is None:
        return event.wait(timeout=timeout)
    end = time.monotonic() + timeout
    while True:
        if interrupt():
            return True
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        if event.wait(timeout=min(_INPUT_POLL_SEC, remaining)):
            return True


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
    for state in WI_STATES:
        (private_notes / state).mkdir(parents=True, exist_ok=True)
