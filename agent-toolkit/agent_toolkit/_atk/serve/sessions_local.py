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
from agent_toolkit._atk.serve.sessions_events import deliver_refresh, schedule_delivery
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
from agent_toolkit._common.session_record_paths import codex_record_roots

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
            context.state.local_list = _LocalListCache(tracker=tracker, entries=saved, scanned_at=-math.inf)
        reused = _reuse_local_list(context, tracker)
        if reused is not None:
            return reused
    with context.state.local_scan_lock:
        return _scan_local_sessions(context, tracker)


def _scan_local_sessions(context: SessionsContext, tracker: session_watch.RecordChangeTracker | None) -> list[SessionSummary]:
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
    entries = []
    for entry, _ in kept:
        parent = links.get(entry.path)
        label = index.delegation_labels(pathlib.Path(parent)).get(entry.session_id) if parent else None
        entries.append(dataclasses.replace(entry, parent_path=parent, label=label))
    entries.sort(key=lambda entry: entry.started_at or "", reverse=True)
    limited = session_parents.limit_with_ancestors(
        entries, MAX_LIST_ENTRIES, path_of=lambda entry: entry.path, parent_of=lambda entry: entry.parent_path
    )
    messages = index.display_messages((pathlib.Path(entry.path), entry.engine) for entry in limited)

    index.finish_scan()
    result = [dataclasses.replace(entry, first_user_message=messages[entry.path]) for entry in limited]
    context.state.local_list = (
        _LocalListCache(tracker=tracker, entries=result, scanned_at=scanned_at) if tracker is not None else None
    )
    _save_local_list(context.state.local_list_path, result)
    if context.state.cost_warmer is not None:
        context.state.cost_warmer.request([(entry.engine, entry.path) for entry in result if entry.warning is None])
    return list(result)


def _load_local_list(path: pathlib.Path | None) -> list[SessionSummary] | None:
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
    state = context.state

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

        entries[position] = dataclasses.replace(entry, updated_at=_isoformat(st.st_mtime), size=st.st_size)
    cache.entries = entries
    return list(entries)


def _start_background_rescan(context: SessionsContext, tracker: session_watch.RecordChangeTracker, *, changed: bool) -> None:
    state = context.state
    with state.local_rescan_flag_lock:
        if state.local_rescan_stopping:
            return
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
                    schedule_delivery(state, loop, lambda: deliver_refresh(state))
                with state.local_rescan_flag_lock:
                    if state.local_rescan_stopping or not state.local_rescan_again:
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

    with state.local_rescan_flag_lock:
        if state.local_rescan_stopping:
            state.local_rescan_running = False
            return
        state.local_rescan_thread = threading.Thread(target=run, name="atk-serve-session-rescan", daemon=True)
        state.local_rescan_thread.start()


def is_local_record_path(context: SessionsContext, raw: str) -> bool:
    if not raw or ".." in pathlib.PurePosixPath(raw).parts or not raw.endswith(session_record_format.RECORD_SUFFIX):
        return False
    try:
        target = pathlib.Path(raw).resolve()
    except OSError:
        return False
    for root in (context.claude_home / "projects", *codex_record_roots(context.codex_home)):
        try:
            target.relative_to(root.resolve())
        except ValueError, OSError:
            continue
        return True
    return False


def _read_local_records(
    context: SessionsContext, engine: str, raw_path: str
) -> tuple[pathlib.Path, list[dict[str, typing.Any]], int]:
    if engine not in {"claude", "codex"} or not is_local_record_path(context, raw_path):
        raise SessionNotFoundError(raw_path)
    return _read_record_file(raw_path)


def _read_record_file(raw_path: str) -> tuple[pathlib.Path, list[dict[str, typing.Any]], int]:
    path = pathlib.Path(raw_path)
    try:
        data = path.read_bytes()
    except FileNotFoundError as error:
        raise SessionNotFoundError(raw_path) from error
    if len(data) > MAX_RECORD_BYTES:
        raise SessionNotFoundError(f"{raw_path}（記録が大きすぎます）")
    records, broken = session_record_format.parse_records(data.decode("utf-8", errors="replace"), include_positions=True)
    return path, records, broken


def read_local_detail(context: SessionsContext, engine: str, raw_path: str) -> dict[str, typing.Any]:
    path, records, broken = _read_local_records(context, engine, raw_path)
    subagents = session_record_format.claude_subagents(path) if engine == "claude" else None

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
    path = pathlib.Path(raw_path)
    try:
        size = path.stat().st_size
    except FileNotFoundError as error:
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
