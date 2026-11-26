# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""`atk serve`のセッション画面が使うリモートホスト側ヘルパー。

操作種別はargvで受け取る（`list`・`read`・`serve`）。
`list`は保存済みセッションの一覧を、`read`は1件の記録本文とサブエージェント記録の一覧、Codexの記録ではthread名をJSONで返す。
`search`は一覧の範囲で記録本文を検索し、一致するパスだけを返す。
`serve`はstdinから行区切りJSONのRPCを受け取り、同じ内容をstdoutへ返す常駐モードとする。
`serve`は記録のrootの変更も監視し、一覧の再取得と記録1件の更新の通知を同じstdoutへ行で書く。

保存先の規約と記録の形式は、サーバー側と同じ`agent_toolkit._atk.session_record_format`で解釈する。
子セッションの解析と、一覧の判定・変更監視も、同じリポジトリの共通モジュールを読み込む。
ユーザー発話の記録行を持たない記録は一覧から除外する。
"""

import base64
import contextlib
import json
import pathlib
import socket
import sys
import threading
import time
import typing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agent_toolkit._atk import session_record_format  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.serve import (  # noqa: E402  # pylint: disable=wrong-import-position
    agent_records,
    session_parents,
    session_watch,
    sessions_models,
)
from agent_toolkit._atk.serve.remote import read_failure_status  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._common import (  # noqa: E402  # pylint: disable=wrong-import-position
    host_homes,
    session_cost_records,  # noqa: E402  # pylint: disable=wrong-import-position
    session_launchers,
    state_paths,
)
from agent_toolkit._common.session_record_paths import codex_record_roots  # pylint: disable=wrong-import-position

MAX_RECORD_BYTES = 64 * 1024 * 1024

MAX_LIST_ENTRIES = 2000


def _entry(index: session_watch.RecordSummaryIndex, path: pathlib.Path, engine: str, session_id: str) -> dict[str, typing.Any]:
    """一覧の1件を組み立てる。読み取れない情報は`None`のままとする。

    `has_user_message`と`codex_parent_thread_id`は除外と親子付けの判定材料であり、`_list_payload`が応答から取り除く。
    """
    (cwd, started_at, has_user, parent_thread_id), st = index.summary(path, engine)
    if isinstance(st, OSError):
        return {
            "engine": engine,
            "session_id": session_id,
            "cwd": cwd,
            "_record_path": str(path),
            "first_user_message": None,
            "path": None,
            "size": None,
            "started_at": started_at,
            "updated_at": None,
            "warning": f"記録の情報を取得できません: {st}",
            "has_user_message": has_user,
            "codex_parent_thread_id": parent_thread_id,
        }
    return {
        "engine": engine,
        "session_id": session_id,
        "cwd": cwd,
        "_record_path": str(path),
        "first_user_message": None,
        "path": str(path),
        "size": st.st_size,
        "started_at": started_at,
        "updated_at": st.st_mtime,
        "warning": None,
        "has_user_message": has_user,
        "codex_parent_thread_id": parent_thread_id,
    }


def _list_payload(
    tracker: session_watch.RecordChangeTracker | None = None,
    index: session_watch.RecordSummaryIndex | None = None,
) -> dict[str, typing.Any]:
    if index is None:
        index = session_watch.RecordSummaryIndex()
    index.begin_scan()
    entries: list[dict[str, typing.Any]] = []
    for path in session_record_format.claude_session_records(host_homes.claude_config_dir()):
        entries.append(_entry(index, path, "claude", path.stem))
        for agent_id, child_path, parent_path in session_record_format.claude_subagent_records(path):
            child = _entry(index, child_path, "claude", agent_id)
            child["parent_path"] = parent_path
            entries.append(child)
    for path in session_record_format.codex_session_records(host_homes.codex_home()):
        entries.append(_entry(index, path, "codex", session_record_format.codex_session_id(path)))
    kept: list[dict[str, typing.Any]] = []
    for entry in entries:
        has_user = entry.pop("has_user_message")
        if tracker is not None and has_user is not None and isinstance(entry.get("path"), str):
            tracker.prime(entry["path"], has_user)

        if has_user is not False:
            kept.append(entry)
    entries = kept
    links = session_parents.resolve_parent_paths(
        [
            session_parents.ParentSource(
                path=entry["path"],
                engine=entry["engine"],
                session_id=entry["session_id"],
                subagent_parent_path=entry.get("parent_path"),
                codex_parent_thread_id=entry.get("codex_parent_thread_id"),
            )
            for entry in entries
            if isinstance(entry.get("path"), str)
        ],
        delegated_ids=lambda path, engine: index.delegated_ids(pathlib.Path(path), engine),
        launcher_of=session_launchers.launcher_reader(_state_dir()),
    )
    index.finish_scan()
    for entry in entries:
        entry.pop("codex_parent_thread_id", None)
        parent_path = links.get(entry["path"]) if isinstance(entry.get("path"), str) else None
        if parent_path is not None:
            entry["parent_path"] = parent_path
            entry["label"] = index.delegation_labels(pathlib.Path(parent_path)).get(entry["session_id"])
    entries.sort(key=lambda item: item["started_at"] or "", reverse=True)
    limited = session_parents.limit_with_ancestors(
        entries, MAX_LIST_ENTRIES, path_of=lambda item: item.get("path"), parent_of=lambda item: item.get("parent_path")
    )
    messages = index.display_messages((pathlib.Path(entry["_record_path"]), entry["engine"]) for entry in limited)
    limited = [
        {
            **{key: value for key, value in entry.items() if key != "_record_path"},
            "first_user_message": messages[entry["_record_path"]],
        }
        for entry in limited
    ]
    return {"host": socket.gethostname(), "entries": limited}


def _state_dir() -> pathlib.Path | None:
    try:
        return state_paths.state_dir()
    except ImportError:
        return None


def _is_safe_record_path(raw: str) -> bool:
    if not raw:
        return False
    pure_path = pathlib.PureWindowsPath(raw) if "\\" in raw else pathlib.PurePosixPath(raw)
    if not pure_path.is_absolute() or ".." in pure_path.parts:
        return False
    if not raw.endswith(session_record_format.RECORD_SUFFIX):
        return False
    try:
        target = pathlib.Path(raw).resolve()
    except OSError:
        return False
    for root in (host_homes.claude_config_dir() / "projects", *codex_record_roots(host_homes.codex_home())):
        try:
            target.relative_to(root.resolve())
        except ValueError, OSError:
            continue
        return True
    return False


def _search_payload(
    query_b64: str,
    tracker: session_watch.RecordChangeTracker | None = None,
    index: session_watch.RecordSummaryIndex | None = None,
    paths_b64: str = "",
) -> dict[str, typing.Any]:
    query = base64.b64decode(query_b64, validate=True).decode("utf-8").strip().casefold()
    if not 0 < len(query) <= 1024:
        raise ValueError("検索語は1〜1024文字で指定してください")
    paths = []
    selected = json.loads(base64.b64decode(paths_b64, validate=True)) if paths_b64 else None
    if selected is not None and (not isinstance(selected, list) or not all(isinstance(path, str) for path in selected)):
        raise ValueError("検索対象が不正です")
    targets = set(selected) if selected is not None else None
    unavailable = False
    retry_paths = []
    deadline = time.monotonic() + sessions_models.OPERATION_TIMEOUT_SEC["search"]
    for entry in _list_payload(tracker, index)["entries"]:
        raw = entry.get("path")
        if not isinstance(raw, str) or not _is_safe_record_path(raw):
            unavailable = True
            continue
        if targets is not None and raw not in targets:
            continue
        try:
            if sessions_models.record_matches(pathlib.Path(raw), entry["engine"], query, deadline=deadline):
                paths.append(raw)
        except (OSError, ValueError) as error:
            status = read_failure_status(error)
            if status == 503:
                retry_paths.append(raw)
            elif status == 403:
                unavailable = True
    return {
        "paths": paths,
        "incomplete": bool(unavailable or retry_paths),
        "unavailable": unavailable,
        "retry_paths": retry_paths,
    }


def _read_payload(path_b64: str) -> dict[str, typing.Any]:
    """指定パスの記録本文をbase64で返す。サブエージェント記録の一覧も同時に返す。"""
    raw = base64.b64decode(path_b64).decode("utf-8")
    if not _is_safe_record_path(raw):
        raise ValueError("invalid record path")
    path = pathlib.Path(raw)
    data = path.read_bytes()
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError(f"record too large: {len(data)} bytes")
    records, broken = session_record_format.parse_records(data.decode("utf-8", errors="replace"))
    engine = (
        "codex"
        if any(path.resolve().is_relative_to(root.resolve()) for root in codex_record_roots(host_homes.codex_home()))
        else "claude"
    )
    payload: dict[str, typing.Any] = {
        "data": base64.b64encode(data).decode("ascii"),
        "mtime_epoch": path.stat().st_mtime,
        # サブエージェント記録が無い場合も空のリストを返す。応答が本欄を持つこと自体を、
        # 本欄を返さない旧版のヘルパーとサーバー側が区別する根拠とするためである。
        "subagents": session_record_format.claude_subagents(path) or [],
        "cost": session_cost_records.estimate_detail_cost(
            engine,
            path,
            records,
            include_descendants=True,
            broken_lines=broken,
        ),
    }
    if engine == "codex":
        # Codexのthread名はロールアウトの記録に無く、このホストの`session_index.jsonl`だけが持つ。
        payload["thread_name"] = session_record_format.codex_thread_name(
            host_homes.codex_home(), session_record_format.codex_session_id(path)
        )
    return payload


_STDOUT_LOCK = threading.Lock()


def _emit(payload: dict[str, typing.Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _agents_payload(op: str) -> dict[str, typing.Any]:
    # セッション記録だけを読む起動では、エージェントの状態管理を読み込まない。pyfltr: allow-comment
    from agent_toolkit._agents_server.session_listing import serve_agent_listing  # pylint: disable=import-outside-toplevel

    roots = serve_agent_listing(include_terminated=op == "agents-list-all")
    try:
        records = agent_records.locate_agent_records(
            host_homes.claude_config_dir(), host_homes.codex_home(), agent_records.agent_ids(roots)
        )
    except OSError:
        records = None
    return {"roots": roots, "records": records}


def _handle_request(
    req: dict[str, typing.Any],
    tracker: session_watch.RecordChangeTracker | None = None,
    index: session_watch.RecordSummaryIndex | None = None,
) -> dict[str, typing.Any]:
    req_id = req.get("id")
    op = req.get("op")
    if not isinstance(req_id, int):
        return {"type": "response", "id": -1, "ok": False, "error": "invalid id"}
    try:
        if op == "list":
            return {"type": "response", "id": req_id, "ok": True, **_list_payload(tracker, index)}
        if op in {"agents-list", "agents-list-all"}:
            return {"type": "response", "id": req_id, "ok": True, **_agents_payload(op)}
        if op == "search":
            payload = _search_payload(str(req.get("query", "")), tracker, index, str(req.get("paths", "")))
            return {"type": "response", "id": req_id, "ok": True, **payload}
        if op == "read":
            return {"type": "response", "id": req_id, "ok": True, **_read_payload(str(req.get("path", "")))}
        return {"type": "response", "id": req_id, "ok": False, "error": f"unknown op: {op}"}
    except Exception as error:  # noqa: BLE001  pylint: disable=broad-exception-caught
        return {
            "type": "response",
            "id": req_id,
            "ok": False,
            "read_failure": read_failure_status(error),
            "error": f"{type(error).__name__}: {error}",
        }


def _start_watch() -> tuple[session_watch.RecordWatch | None, session_watch.RecordChangeTracker]:

    def on_flush(refresh: bool, records: list[tuple[str, str]]) -> None:

        with contextlib.suppress(OSError):
            if refresh:
                _emit({"type": session_watch.REFRESH_TYPE})
            for engine, path in records:
                _emit({"type": session_watch.RECORD_TYPE, "engine": engine, "path": path})

    tracker = session_watch.RecordChangeTracker(
        [
            (host_homes.claude_config_dir() / "projects", "claude"),
            *((root, "codex") for root in codex_record_roots(host_homes.codex_home())),
        ],
        on_flush,
    )
    watch = session_watch.RecordWatch(tracker)
    try:
        watch.start()
    except (ImportError, OSError) as error:
        sys.stderr.write(f"warn: session record watch unavailable: {error}\n")
        return None, tracker
    return watch, tracker


def _serve() -> int:
    """stdinの行区切りJSONリクエストへ応答し、記録の変更を通知する常駐モード。

    RPCプロトコル（行区切りJSON）:
        リクエスト（stdin）: {"id":<int>, "op":"list"}
                            または{"id":<int>, "op":"read", "path":"<base64絶対パス>"}
                            または{"id":<int>, "op":"search", "query":"<base64検索語>"}
        応答（stdout）:
            成功: {"type":"response", "id":<int>, "ok":true, ...}
            失敗: {"type":"response", "id":<int>, "ok":false, "error":"<msg>"}
        変更通知（stdout）:
            一覧の再取得: {"type":"refresh"}
            記録1件の更新: {"type":"record", "engine":"claude"|"codex", "path":"<絶対パス>"}
    起動直後に`{"type":"ready","host":...}`を1行出力し、呼び出し側の接続確立の契機とする。
    """
    watch, tracker = _start_watch()
    index = session_watch.RecordSummaryIndex()
    _emit({"type": "ready", "host": socket.gethostname()})
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as error:
                _emit({"type": "response", "id": -1, "ok": False, "error": f"json: {error}"})
                continue
            try:
                _emit(_handle_request(req, tracker, index))
            except BrokenPipeError:
                return 0
    finally:
        if watch is not None:
            watch.stop()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("missing operation\n")
        return 2
    op = sys.argv[1]
    if op == "list":
        json.dump(_list_payload(), sys.stdout, ensure_ascii=False)
        return 0
    if op == "search" and len(sys.argv) in {3, 4}:
        paths = sys.argv[3] if len(sys.argv) == 4 else ""
        json.dump(_search_payload(sys.argv[2], paths_b64=paths), sys.stdout, ensure_ascii=False)
        return 0
    if op == "read":
        if len(sys.argv) < 3:
            sys.stderr.write("missing path\n")
            return 2
        try:
            payload = _read_payload(sys.argv[2])
        except (OSError, ValueError) as error:
            payload = {"read_failure": read_failure_status(error)}
        json.dump(payload, sys.stdout, ensure_ascii=False)
        return 0
    if op in {"agents-list", "agents-list-all"}:
        json.dump(_agents_payload(op), sys.stdout, ensure_ascii=False)
        return 0
    if op == "serve":
        return _serve()
    sys.stderr.write(f"unknown operation: {op}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
