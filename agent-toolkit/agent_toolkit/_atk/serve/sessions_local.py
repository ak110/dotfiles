"""セッション画面のローカル記録の一覧・詳細・費用。"""

import asyncio
import contextlib
import dataclasses
import json
import logging
import math
import os
import pathlib
import threading
import time
import typing

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import session_costs as _session_costs
from agent_toolkit._atk.serve import session_parents, session_watch
from agent_toolkit._atk.serve.sessions_events import deliver_refresh
from agent_toolkit._atk.serve.sessions_models import (
    LOCAL_LIST_REUSE_SEC,
    MAX_LIST_ENTRIES,
    MAX_RECORD_BYTES,
    SessionNotFoundError,
    SessionsContext,
    SessionSummary,
    _isoformat,
    _LocalListCache,
    _started_at,
    build_detail,
)
from agent_toolkit._common import session_cost_records, session_cost_slim, session_launchers
from agent_toolkit._common.atomic_file import atomic_write

logger = logging.getLogger("agent_toolkit._atk.serve.sessions")


def _local_entry(
    index: session_watch.RecordSummaryIndex, path: pathlib.Path, engine: str, session_id: str, host: str
) -> tuple[SessionSummary, bool | None, str | None]:
    """ローカルの記録1件を一覧の項目へ変換し、ユーザー発話の有無とCodexの親threadの識別子とともに返す。"""
    (cwd, started_at, has_user, parent_thread_id), st = index.summary(path, engine)
    if isinstance(st, OSError):
        summary = SessionSummary(
            engine=engine,
            host=host,
            cwd=cwd,
            first_user_message=None,
            session_id=session_id,
            path=str(path),
            started_at=started_at,
            updated_at=None,
            size=None,
            warning=f"記録の情報を取得できません: {st}",
        )
        return summary, has_user, parent_thread_id
    summary = SessionSummary(
        engine=engine,
        host=host,
        cwd=cwd,
        first_user_message=None,
        session_id=session_id,
        path=str(path),
        started_at=started_at,
        updated_at=_isoformat(st.st_mtime),
        size=st.st_size,
    )
    return summary, has_user, parent_thread_id


def list_local_sessions(context: SessionsContext) -> list[SessionSummary]:
    """ローカルの保存済みセッションを開始日時の新しい順に返す。

    Claude Codeはセッション本体と、metadataで親子を確定できるサブエージェントを含める。
    Codexは`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`を対象とする。
    ユーザー発話の記録行を持たない記録は、件数上限による切り詰めより前に除外する。
    変更監視が動いている場合は、判定した発話の有無を監視側の判定へ引き継ぐ。
    サーバーの停止要求を受けた場合は記録1件ごとの確認で`ServeStopping`を送出して打ち切る。

    変更監視が動いている間は、一覧の集合か親子関係を変え得る変更が無ければ前回の結果を再利用し、
    追記された記録の更新日時と大きさだけを取り直す。全件の走査は記録数千件で1秒前後かかるためである。
    """
    tracker = context.state.record_watch.tracker if context.state.record_watch is not None else None
    if tracker is not None:
        saved, context.state.saved_local_list = context.state.saved_local_list, None
        if context.state.local_list is None and saved is not None:
            # 保存した一覧を上限時間の過ぎた前回の一覧として扱い、返した後に背景の走査で取り直させる。
            context.state.local_list = _LocalListCache(tracker=tracker, entries=saved, scanned_at=-math.inf)
        reused = _reuse_local_list(context, tracker)
        if reused is not None:
            return reused
    with context.state.local_scan_lock:
        return _scan_local_sessions(context, tracker)


def _scan_local_sessions(context: SessionsContext, tracker: session_watch.RecordChangeTracker | None) -> list[SessionSummary]:
    """ローカルの記録を全件走査して一覧を作り、再利用する一覧として保持する。"""
    scanned_at = time.monotonic()
    stop = context.state.stop_requested
    index = context.state.record_index
    index.begin_scan()
    collected: list[tuple[SessionSummary, bool | None, str | None]] = []
    for path in session_record_format.claude_session_records(context.claude_home):
        _atk_serve_remote.raise_if_stopping(stop)
        collected.append(_local_entry(index, path, "claude", path.stem, context.hostname))
        for agent_id, child_path, parent_path in session_record_format.claude_subagent_records(path):
            child, child_has_user, child_thread = _local_entry(index, child_path, "claude", agent_id, context.hostname)
            collected.append((dataclasses.replace(child, parent_path=parent_path), child_has_user, child_thread))
    for path in session_record_format.codex_session_records(context.codex_home):
        _atk_serve_remote.raise_if_stopping(stop)
        collected.append(_local_entry(index, path, "codex", session_record_format.codex_session_id(path), context.hostname))
    watch = context.state.record_watch
    if watch is not None:
        for entry, has_user, _ in collected:
            if has_user is not None:
                watch.tracker.prime(entry.path, has_user)
    # 読めずに判定できなかった記録（`None`）は警告とともに一覧へ残す。
    kept = [(entry, parent_thread_id) for entry, has_user, parent_thread_id in collected if has_user is not False]
    links = session_parents.resolve_parent_paths(
        [
            session_parents.ParentSource(
                path=entry.path,
                engine=entry.engine,
                session_id=entry.session_id,
                subagent_parent_path=entry.parent_path,
                codex_parent_thread_id=parent_thread_id,
            )
            for entry, parent_thread_id in kept
        ],
        delegated_ids=lambda path, engine: index.delegated_ids(pathlib.Path(path), engine),
        launcher_of=session_launchers.launcher_reader(context.state_dir),
    )
    entries = [dataclasses.replace(entry, parent_path=links.get(entry.path)) for entry, _ in kept]
    entries.sort(key=lambda entry: entry.started_at or "", reverse=True)
    limited = session_parents.limit_with_ancestors(
        entries, MAX_LIST_ENTRIES, path_of=lambda entry: entry.path, parent_of=lambda entry: entry.parent_path
    )
    messages = index.display_messages((pathlib.Path(entry.path), entry.engine) for entry in limited)
    # 表示用の本文を求めた後に走査を終え、保存する索引へ本文を含める。先に保存すると、全件を読み直した最初の走査の
    # 直後にserveを止めた場合、次の起動の最初の一覧が表示件数分の本文を読み直して数十秒かかる。
    index.finish_scan()
    result = [dataclasses.replace(entry, first_user_message=messages[entry.path]) for entry in limited]
    context.state.local_list = (
        _LocalListCache(tracker=tracker, entries=result, scanned_at=scanned_at) if tracker is not None else None
    )
    _save_local_list(context.state.local_list_path, result)
    if context.state.cost_warmer is not None:
        # 一覧の並び（開始日時の新しい順）で算出し、画面で開かれやすい最近の記録から保持する。
        context.state.cost_warmer.request([(entry.engine, entry.path) for entry in result if entry.warning is None])
    return list(result)


def _load_local_list(path: pathlib.Path | None) -> list[SessionSummary] | None:
    """保存したローカル一覧を読む。不在・破損・項目の不一致は`None`を返し、要求の中の走査へ戻す。"""
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [SessionSummary(**item) for item in payload["sessions"]]
    except OSError, ValueError, KeyError, TypeError:
        return None


def _save_local_list(path: pathlib.Path | None, entries: list[SessionSummary]) -> None:
    """走査したローカル一覧を保存する。失敗は一覧の取得を失敗させない（次の起動が要求の中で走査するだけである）。"""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps({"sessions": [entry.to_json() for entry in entries]}, ensure_ascii=False))
    except OSError as error:
        logger.debug("セッション一覧を保存できません: %s: %s", path, error)


def _reuse_local_list(context: SessionsContext, tracker: session_watch.RecordChangeTracker) -> list[SessionSummary] | None:
    """前回のローカル一覧を再利用できれば、追記を反映した一覧を返す。再利用できなければ`None`を返す。

    一覧の集合を変え得る変更が届いた場合と再利用の上限時間を過ぎた場合も前回の一覧を返し、全件の走査は背景で行う。
    記録数千件の走査は負荷時に2秒を超え、他のプロセスが新しい記録を作るたびに要求の中で走査すると一覧の表示を待たせるためである。
    上限時間は監視の取りこぼしに備える見直しの間隔である。背景の走査の結果で一覧が変わった場合は`refresh`を配信し、
    画面が取り直す。前回の一覧が無い場合（起動後の最初の要求）と、結果を配信できない場合は要求の中で走査する。
    """
    state = context.state
    # 変更は再利用しない場合も消費する。続く全件の走査がそれまでの変更を全て反映するためである。
    invalid, appended = tracker.take_list_changes()
    cache = state.local_list
    if cache is None or cache.tracker is not tracker or (invalid and state.watch_loop is None):
        return None
    if invalid or time.monotonic() - cache.scanned_at >= LOCAL_LIST_REUSE_SEC:
        _start_background_rescan(context, tracker, changed=invalid)
    entries = list(cache.entries)
    for position, entry in enumerate(entries):
        if entry.path not in appended:
            continue
        if entry.warning is not None:
            return None
        try:
            st = os.stat(entry.path)
        except OSError:
            return None
        # 一覧の並び（開始日時）と表示する発話は追記で変わらないため、更新日時と大きさだけを更新する。
        entries[position] = dataclasses.replace(entry, updated_at=_isoformat(st.st_mtime), size=st.st_size)
    cache.entries = entries
    return list(entries)


def _start_background_rescan(context: SessionsContext, tracker: session_watch.RecordChangeTracker, *, changed: bool) -> None:
    """一覧の全件の走査を背景のスレッドで1つだけ始める。

    走査の間に一覧の集合を変え得る変更（`changed`）が届いた場合は、走査の開始後の変更を反映するため、
    終わった後にもう1回走査する。上限時間を過ぎただけの要求は、進行中の走査が同じ見直しを行うため重ねない。
    """
    state = context.state
    with state.local_rescan_flag_lock:
        if state.local_rescan_running:
            state.local_rescan_again = state.local_rescan_again or changed
            return
        state.local_rescan_running = True
        state.local_rescan_again = False

    def run() -> None:
        try:
            while True:
                previous = state.local_list.entries if state.local_list is not None else None
                with state.local_scan_lock:
                    scanned = _scan_local_sessions(context, tracker)
                loop = state.watch_loop
                if scanned != previous and loop is not None:
                    with contextlib.suppress(RuntimeError):
                        asyncio.run_coroutine_threadsafe(deliver_refresh(state), loop)
                with state.local_rescan_flag_lock:
                    if not state.local_rescan_again:
                        state.local_rescan_running = False
                        return
                    state.local_rescan_again = False
        except _atk_serve_remote.ServeStopping:
            pass
        except Exception:  # pylint: disable=broad-exception-caught
            # 背景の走査の失敗では前回の一覧を使い続け、次の変更か上限時間を過ぎた要求で再び走査する。
            logger.warning(
                "セッション一覧の背景の再走査に失敗しました。前回の一覧を表示し続け、"
                "次に記録が変わった時か、%.0f秒を過ぎた後の一覧の取得で再走査します。"
                "失敗が続く場合は、この警告に続く例外から原因（記録の保存先の権限やディスクの状態など）を取り除き、"
                "セッション画面を再読み込みしてください",
                LOCAL_LIST_REUSE_SEC,
                exc_info=True,
            )
        with state.local_rescan_flag_lock:
            state.local_rescan_running = False

    threading.Thread(target=run, name="atk-serve-session-rescan", daemon=True).start()


def is_local_record_path(context: SessionsContext, raw: str) -> bool:
    """読み取り要求のパスがいずれかの保存先の配下を指すかを検証する。"""
    if not raw or ".." in pathlib.PurePosixPath(raw).parts or not raw.endswith(session_record_format.RECORD_SUFFIX):
        return False
    try:
        target = pathlib.Path(raw).resolve()
    except OSError:
        return False
    for root in (context.claude_home / "projects", context.codex_home / "sessions"):
        try:
            target.relative_to(root.resolve())
        except ValueError, OSError:
            continue
        return True
    return False


def _read_local_records(
    context: SessionsContext, engine: str, raw_path: str
) -> tuple[pathlib.Path, list[dict[str, typing.Any]], int]:
    """ローカルの記録を検証して読み、記録のパス、解析した行、解析できない行数を返す。"""
    if engine not in {"claude", "codex"} or not is_local_record_path(context, raw_path):
        raise SessionNotFoundError(raw_path)
    return _read_record_file(raw_path)


def _read_record_file(raw_path: str) -> tuple[pathlib.Path, list[dict[str, typing.Any]], int]:
    path = pathlib.Path(raw_path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SessionNotFoundError(raw_path) from error
    if len(data) > MAX_RECORD_BYTES:
        raise SessionNotFoundError(f"{raw_path}（記録が大きすぎます）")
    records, broken = session_record_format.parse_records(data.decode("utf-8", errors="replace"))
    return path, records, broken


def read_local_detail(context: SessionsContext, engine: str, raw_path: str) -> dict[str, typing.Any]:
    """ローカルの記録を読み、推定費用を除いた詳細応答を組み立てる。

    推定費用は子孫の全記録を読むため、子孫の多いセッションでは数秒かかる。本文の表示を待たせないよう、
    `cost_pending`を真にして返し、画面が`read_local_cost`を別の要求で取得する。
    """
    path, records, broken = _read_local_records(context, engine, raw_path)
    subagents = session_record_format.claude_subagents(path) if engine == "claude" else None
    # ローカルの記録は保存先を直接読むため、サブエージェント記録の有無を常に判定できる。
    detail = build_detail(engine, records, broken_lines=broken, subagents=subagents, subagents_unavailable=False)
    detail["session_id"] = path.stem if engine == "claude" else session_record_format.codex_session_id(path)
    detail["host"] = context.hostname
    detail["path"] = raw_path
    detail["started_at"] = _started_at(engine, records)
    detail["session_name"] = (
        session_record_format.claude_session_name(records)
        if engine == "claude"
        else session_record_format.codex_thread_name(context.codex_home, detail["session_id"])
    )
    detail["cost"] = None
    detail["cost_pending"] = True
    return detail


def read_local_cost(context: SessionsContext, engine: str, raw_path: str) -> dict[str, typing.Any]:
    """ローカルの記録と子孫から、詳細の見出しに表示する推定費用を求める。

    家族の記録が前回の算出から変わっていなければ、保持した結果を返す（`session_costs`）。
    """
    if engine not in {"claude", "codex"} or not is_local_record_path(context, raw_path):
        raise SessionNotFoundError(raw_path)
    cached = context.state.family_costs.get(pathlib.Path(raw_path))
    if cached is not None:
        return cached
    started_ns = _session_costs.now_ns()
    estimated = estimate_local_family(
        engine,
        raw_path,
        claude_home=context.claude_home,
        codex_home=context.codex_home,
        state_dir=context.state_dir,
        slim=context.state.slim_records,
    )
    if estimated.complete:
        context.state.family_costs.put(pathlib.Path(raw_path), estimated.members, estimated.cost, started_ns)
    return estimated.cost


def estimate_local_family(
    engine: str,
    raw_path: str,
    *,
    claude_home: pathlib.Path,
    codex_home: pathlib.Path,
    state_dir: pathlib.Path | None,
    slim: session_cost_slim.SlimRecordStore,
) -> session_cost_records.DetailCost:
    """ローカルの記録1件を起点に、子孫を含む家族の推定費用を求める。背景の算出の別プロセスも同じ処理を使う。

    家族の各記録は縮約記録（`slim`）から読み、変わった記録の追記分だけを解析する。
    起点の記録を縮約できない場合（解析できない行や書き込み途中の末尾を含む）は、従来どおり全行を読み、
    解析できない行数を不完全の判定へ渡す。
    """
    path = pathlib.Path(raw_path)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise SessionNotFoundError(raw_path) from error
    if size > MAX_RECORD_BYTES:
        raise SessionNotFoundError(f"{raw_path}（記録が大きすぎます）")
    slim_records = slim.records(raw_path)
    if slim_records is not None:
        records, broken = [record.entry for record in slim_records], 0
    else:
        path, records, broken = _read_record_file(raw_path)
    return session_cost_records.estimate_detail_family(
        engine,
        path,
        records,
        include_descendants=True,
        claude_home=claude_home,
        codex_home=codex_home,
        state_root=state_dir,
        broken_lines=broken,
        record_loader=slim.loader,
    )
