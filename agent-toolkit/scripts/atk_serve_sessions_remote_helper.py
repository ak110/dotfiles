# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""`atk serve`のセッション画面が使うリモートホスト側ヘルパー。

操作種別はargvで受け取る（`list`・`read`・`serve`）。
`list`は保存済みセッションの一覧を、`read`は1件の記録本文とサブエージェント記録の一覧をJSONで返す。
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
import typing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agent_toolkit._atk import session_record_format  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.serve import (  # noqa: E402  # pylint: disable=wrong-import-position
    session_parents,
    session_watch,
)
from agent_toolkit._common import (  # noqa: E402  # pylint: disable=wrong-import-position
    host_homes,
    session_launchers,
    state_paths,
)

# 1件の記録から取得する最大バイト数。過大な記録の全文転送により接続が占有される事態を避ける上限とする。
MAX_RECORD_BYTES = 64 * 1024 * 1024
# 一覧が返す最大件数。古い記録は調査対象になりにくいため、開始日時の新しい順で打ち切る。
MAX_LIST_ENTRIES = 2000


def _entry(index: session_watch.RecordSummaryIndex, path: pathlib.Path, engine: str, session_id: str) -> dict[str, typing.Any]:
    """一覧の1件を組み立てる。読み取れない情報は`None`のままとする。

    `has_user_message`と`codex_parent_thread_id`は除外と親子付けの判定材料であり、`_list_payload`が応答から取り除く。
    """
    (cwd, first_user_message, started_at, has_user, parent_thread_id), st = index.summary(path, engine)
    if isinstance(st, OSError):
        return {
            "engine": engine,
            "session_id": session_id,
            "cwd": cwd,
            "first_user_message": first_user_message,
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
        "first_user_message": first_user_message,
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
    """ローカルの保存済みセッション一覧を返す。

    ユーザー発話の記録行を持たない記録は、件数上限による切り詰めより前に除外する。
    `tracker`を渡した場合は、判定した発話の有無を変更監視の判定へ引き継ぐ。
    `index`を渡した場合は記録単位の解析結果を再利用する。常駐モードが1プロセスに1つ持ち、
    単発の`list`モードは保持先が無いため毎回新しい索引で走査する。
    """
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
        # 読めずに判定できなかった記録（`None`）は警告とともに一覧へ残す。
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
    entries.sort(key=lambda item: item["started_at"] or "", reverse=True)
    limited = session_parents.limit_with_ancestors(
        entries, MAX_LIST_ENTRIES, path_of=lambda item: item.get("path"), parent_of=lambda item: item.get("parent_path")
    )
    return {"host": socket.gethostname(), "entries": limited}


def _state_dir() -> pathlib.Path | None:
    """agents_serverの状態ディレクトリを返す。解決に要る`platformdirs`が無い起動形では`None`を返す。

    登録簿の委譲元は親子付けの情報源の1つであり、読めない場合も他の情報源で一覧を返す。
    """
    try:
        return state_paths.state_dir()
    except ImportError:
        return None


def _is_safe_record_path(raw: str) -> bool:
    """読み取り要求のパスが保存先配下の記録を指すかを検証する。

    上位ディレクトリへの参照と対象外の接尾辞を拒否し、いずれかの保存先の配下だけを受理する。
    """
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
    for root in (host_homes.claude_config_dir() / "projects", host_homes.codex_home() / "sessions"):
        try:
            target.relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        return True
    return False


def _read_payload(path_b64: str) -> dict[str, typing.Any]:
    """指定パスの記録本文をbase64で返す。サブエージェント記録の一覧も同時に返す。"""
    raw = base64.b64decode(path_b64).decode("utf-8")
    if not _is_safe_record_path(raw):
        raise ValueError("invalid record path")
    path = pathlib.Path(raw)
    data = path.read_bytes()
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError(f"record too large: {len(data)} bytes")
    return {
        "data": base64.b64encode(data).decode("ascii"),
        "mtime_epoch": path.stat().st_mtime,
        # サブエージェント記録が無い場合も空のリストを返す。応答が本欄を持つこと自体を、
        # 本欄を返さない旧版のヘルパーとサーバー側が区別する根拠とするためである。
        "subagents": session_record_format.claude_subagents(path) or [],
    }


_STDOUT_LOCK = threading.Lock()


def _emit(payload: dict[str, typing.Any]) -> None:
    """1行JSONとして出力し、SSH切断時のSIGPIPEを即時に拾えるよう毎回フラッシュする。"""
    line = json.dumps(payload, ensure_ascii=False)
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _handle_request(
    req: dict[str, typing.Any],
    tracker: session_watch.RecordChangeTracker | None = None,
    index: session_watch.RecordSummaryIndex | None = None,
) -> dict[str, typing.Any]:
    """RPCリクエストを処理して応答辞書を返す。"""
    req_id = req.get("id")
    op = req.get("op")
    if not isinstance(req_id, int):
        return {"type": "response", "id": -1, "ok": False, "error": "invalid id"}
    try:
        if op == "list":
            return {"type": "response", "id": req_id, "ok": True, **_list_payload(tracker, index)}
        if op == "read":
            return {"type": "response", "id": req_id, "ok": True, **_read_payload(str(req.get("path", "")))}
        return {"type": "response", "id": req_id, "ok": False, "error": f"unknown op: {op}"}
    except Exception as error:  # noqa: BLE001  pylint: disable=broad-exception-caught
        return {"type": "response", "id": req_id, "ok": False, "error": f"{type(error).__name__}: {error}"}


def _start_watch() -> tuple[session_watch.RecordWatch | None, session_watch.RecordChangeTracker]:
    """記録のrootの変更監視を開始する。監視を開始できない場合もRPCは続ける。"""

    def on_flush(refresh: bool, records: list[tuple[str, str]]) -> None:
        # SSH接続の切断後の通知は届け先が無いため破棄する。RPCの読み取り側が切断を検知して終了する。
        with contextlib.suppress(OSError):
            if refresh:
                _emit({"type": session_watch.REFRESH_TYPE})
            for engine, path in records:
                _emit({"type": session_watch.RECORD_TYPE, "engine": engine, "path": path})

    tracker = session_watch.RecordChangeTracker(
        [(host_homes.claude_config_dir() / "projects", "claude"), (host_homes.codex_home() / "sessions", "codex")],
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
    """argvの操作種別に応じて一覧・読み取り・常駐モードを実行する。"""
    if len(sys.argv) < 2:
        sys.stderr.write("missing operation\n")
        return 2
    op = sys.argv[1]
    if op == "list":
        json.dump(_list_payload(), sys.stdout, ensure_ascii=False)
        return 0
    if op == "read":
        if len(sys.argv) < 3:
            sys.stderr.write("missing path\n")
            return 2
        json.dump(_read_payload(sys.argv[2]), sys.stdout, ensure_ascii=False)
        return 0
    if op == "serve":
        return _serve()
    sys.stderr.write(f"unknown operation: {op}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
