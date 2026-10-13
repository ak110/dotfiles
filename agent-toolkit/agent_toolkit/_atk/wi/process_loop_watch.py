"""`atk wi process-loop`の待機中の変更の監視と、private-notesの同期。"""

import dataclasses
import os
import pathlib
import subprocess
import threading
import time

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


# 変更検知後、追加イベント発火が無くなるまでの畳み込み待機秒
# （1回のファイル操作で複数イベントが連続発火するという観測に対応する）。
_DEBOUNCE_SEC = 3.0

_START_GRACE_SEC = 600.0
_SESSION_RECHECK_SEC = 3.0


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
            reasons.append("別セッションが活動中")
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
    """活動と回答の解除、WI更新、中断を開始前に再評価する。"""
    previous: StartDecision | None = None
    while True:
        if _pl_control.consume_process_loop_abort():
            return False
        activity = _other_sessions_active()
        now = time.time()
        decision = gate.evaluate(target_repo_ids, activity=activity, now=now)
        if not decision.reasons:
            if previous is not None:
                print("開始前の待機条件が解除されました。処理可能なWIを再評価します。")
                _pl_log.append("start_wait_released")
            return True
        assert decision.deadline is not None
        if previous is None or (decision.reasons, decision.deadline) != (previous.reasons, previous.deadline):
            deadline = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(decision.deadline))
            reason = "、".join(decision.reasons)
            extension = "（新規WIの到着により延長）" if decision.extended else ""
            print(f"開始前に待機します: {reason}。解除期限: {deadline}{extension}。")
            _pl_log.append("start_wait", reasons=reason, deadline=decision.deadline, extended=decision.extended)
            previous = decision
        wait_for_changes(
            private_notes,
            None,
            timeout=min(_SESSION_RECHECK_SEC, decision.deadline - now),
            state_directories=(_session_activity.activity_directory(), _pl_log.abort_path().parent),
        )


def _other_sessions_active() -> bool:
    """ホスト共通の活動取得から自ループ以外の活動を得る。"""
    return _session_activity.scan_activity(excluded_pids={os.getpid()}).has_activity


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
) -> bool:
    """保存状態と追加の状態ディレクトリを監視し、変更または期限まで待機する。

    変更検知時はデバウンス窓（3秒）で追加イベントを畳み込んでから返る
    （他端末書き込みは10分タイムアウト側のremote同期で拾うため、変更検知時は同期しない）。
    通常の同期間隔のタイムアウトでは、他端末投入を反映するためlock下でpullする。
    開始前の短い状態再評価へtimeoutを指定した場合は、そのたびにremote同期を繰り返さない。
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
    for state in WI_STATES:
        observer.schedule(handler, str(private_notes / state), recursive=False)
    state_handler = _StateChangeHandler(change_event)
    for directory in dict.fromkeys(state_directories):
        if directory.is_dir():
            observer.schedule(state_handler, str(directory), recursive=False)
    observer.start()
    try:
        if change_event.wait(timeout=_POLL_INTERVAL_SEC if timeout is None else max(0.0, timeout)):
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
