# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""`atk serve`のセッション画面が使うリモートホスト側ヘルパー。

操作種別はargvで受け取る（`list`・`read`・`serve`）。
`list`は保存済みセッションの一覧を、`read`は1件の記録本文とサブエージェント記録の一覧をJSONで返す。
`serve`はstdinから行区切りJSONのRPCを受け取り、同じ内容をstdoutへ返す常駐モードとする。
`serve`は記録のrootの変更も監視し、一覧の再取得と記録1件の更新の通知を同じstdoutへ行で書く。

保存先の規約は`agent-toolkit/skills/writing-standards/references/session-records.md`を正本とし、
サーバー側`_atk/serve/sessions.py`と同じ規約で解決する。
子セッションの解析と、一覧の判定・変更監視は、同じリポジトリの共通モジュールを読み込む。
ユーザー発話の記録行を持たない記録は一覧から除外する。
"""

import base64
import contextlib
import json
import os
import pathlib
import socket
import sys
import threading
import typing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agent_toolkit._atk.serve import (  # noqa: E402  # pylint: disable=wrong-import-position
    session_delegations,
    session_watch,
)

# 1件の記録から取得する最大バイト数。過大な記録の全文転送により接続が占有される事態を避ける上限とする。
MAX_RECORD_BYTES = 64 * 1024 * 1024
# 一覧が返す最大件数。古い記録は調査対象になりにくいため、開始日時の新しい順で打ち切る。
MAX_LIST_ENTRIES = 2000

_CLAUDE_SUFFIX = ".jsonl"
_CODEX_PREFIX = "rollout-"


def _claude_home() -> pathlib.Path:
    """Claude Codeの記録の保存先を返す。"""
    return pathlib.Path.home() / ".claude"


def _codex_home() -> pathlib.Path:
    """Codexの記録の保存先を返す。空でない`CODEX_HOME`を優先する。"""
    value = os.environ.get("CODEX_HOME")
    if value:
        return pathlib.Path(value)
    return pathlib.Path.home() / ".codex"


def _iter_claude_records() -> typing.Iterator[pathlib.Path]:
    """Claude Codeのセッション本体の記録を返す。

    記録階層は深さ2（`<project>/<session-uuid>.jsonl`）をセッション本体とする。
    サブエージェント記録は深さ4に置かれ、一覧では本体へまとめるため列挙しない。
    """
    projects = _claude_home() / "projects"
    if not projects.is_dir():
        return
    for project_dir in projects.iterdir():
        if not project_dir.is_dir():
            continue
        for path in project_dir.glob(f"*{_CLAUDE_SUFFIX}"):
            if path.is_file():
                yield path


def _iter_codex_records() -> typing.Iterator[pathlib.Path]:
    """Codexのロールアウト記録を返す。

    保存先は`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`とする。
    """
    sessions = _codex_home() / "sessions"
    if not sessions.is_dir():
        return
    for path in sessions.glob(f"*/*/*/{_CODEX_PREFIX}*{_CLAUDE_SUFFIX}"):
        if path.is_file():
            yield path


def _codex_session_id(path: pathlib.Path) -> str:
    """ロールアウトのファイル名からthread IDを取り出す。"""
    stem = path.name[len(_CODEX_PREFIX) : -len(_CLAUDE_SUFFIX)]
    # ファイル名は`rollout-<日時>-<thread-id>`の形であり、thread IDはUUIDの5区画で末尾に置かれる。
    parts = stem.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else stem


def _entry(path: pathlib.Path, engine: str, session_id: str) -> dict[str, typing.Any]:
    """一覧の1件を組み立てる。読み取れない情報は`None`のままとする。

    `has_user_message`は一覧からの除外の判定材料であり、`_list_payload`が応答から取り除く。
    """
    cwd, first_user_message, started_at, has_user = session_watch.summary_fields(path, engine)
    try:
        st = path.stat()
        size = st.st_size
        updated_at = st.st_mtime
    except OSError as error:
        return {
            "engine": engine,
            "session_id": session_id,
            "cwd": cwd,
            "first_user_message": first_user_message,
            "path": None,
            "size": None,
            "started_at": started_at,
            "updated_at": None,
            "warning": f"記録の情報を取得できません: {error}",
            "has_user_message": has_user,
        }
    return {
        "engine": engine,
        "session_id": session_id,
        "cwd": cwd,
        "first_user_message": first_user_message,
        "path": str(path),
        "size": size,
        "started_at": started_at,
        "updated_at": updated_at,
        "warning": None,
        "has_user_message": has_user,
    }


def _list_payload(tracker: session_watch.RecordChangeTracker | None = None) -> dict[str, typing.Any]:
    """ローカルの保存済みセッション一覧を返す。

    ユーザー発話の記録行を持たない記録は、件数上限による切り詰めより前に除外する。
    `tracker`を渡した場合は、判定した発話の有無を変更監視の判定へ引き継ぐ。
    """
    entries: list[dict[str, typing.Any]] = []
    for path in _iter_claude_records():
        entries.append(_entry(path, "claude", path.stem))
        subagents = _subagents(path)
        agent_paths = {item["agent_id"]: item["path"] for item in subagents if item["path"]}
        for item in subagents:
            child_path = item["path"]
            if not child_path:
                continue
            parent_id = item.get("parent_agent_id")
            parent_path = (
                agent_paths.get(parent_id) or agent_paths.get(f"agent-{parent_id}") if isinstance(parent_id, str) else None
            )
            if parent_path is None and item.get("spawn_depth") == 1:
                parent_path = str(path)
            if parent_path is None:
                continue
            child = _entry(pathlib.Path(child_path), "claude", item["agent_id"])
            child["parent_path"] = parent_path
            entries.append(child)
    for path in _iter_codex_records():
        entries.append(_entry(path, "codex", _codex_session_id(path)))
    kept: list[dict[str, typing.Any]] = []
    for entry in entries:
        has_user = entry.pop("has_user_message")
        if tracker is not None and has_user is not None and isinstance(entry.get("path"), str):
            tracker.prime(entry["path"], has_user)
        # 読めずに判定できなかった記録（`None`）は警告とともに一覧へ残す。
        if has_user is not False:
            kept.append(entry)
    entries = kept
    by_id: dict[str, list[dict[str, typing.Any]]] = {}
    for entry in entries:
        by_id.setdefault(entry["session_id"], []).append(entry)
    for parent in entries:
        if parent.get("parent_path") or not isinstance(parent.get("path"), str):
            continue
        for child_id in session_delegations.delegated_session_ids(pathlib.Path(parent["path"]), parent["engine"]):
            matches = [entry for entry in by_id.get(child_id, []) if entry.get("path") != parent["path"]]
            if len(matches) == 1:
                matches[0]["parent_path"] = parent["path"]
    entries.sort(key=lambda item: item["started_at"] or "", reverse=True)
    return {"host": socket.gethostname(), "entries": entries[:MAX_LIST_ENTRIES]}


def _is_safe_record_path(raw: str) -> bool:
    """読み取り要求のパスが保存先配下の記録を指すかを検証する。

    上位ディレクトリへの参照と対象外の接尾辞を拒否し、いずれかの保存先の配下だけを受理する。
    """
    if not raw:
        return False
    pure_path = pathlib.PureWindowsPath(raw) if "\\" in raw else pathlib.PurePosixPath(raw)
    if not pure_path.is_absolute() or ".." in pure_path.parts:
        return False
    if not raw.endswith(_CLAUDE_SUFFIX):
        return False
    try:
        target = pathlib.Path(raw).resolve()
    except OSError:
        return False
    for root in (_claude_home() / "projects", _codex_home() / "sessions"):
        try:
            target.relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        return True
    return False


def _subagents(record_path: pathlib.Path) -> list[dict[str, typing.Any]]:
    """記録本体に属するサブエージェント記録の親子関係を返す。

    サーバー側`_atk/serve/sessions.py`の`_claude_subagents`と同じ規約で解決する。
    サブエージェント記録が無い場合は空のリストを返す。応答が本欄を持つこと自体を、
    本欄を返さない旧版のヘルパーとサーバー側が区別する根拠とするためである。
    """
    directory = record_path.with_suffix("") / "subagents"
    if not directory.is_dir():
        return []
    found: list[dict[str, typing.Any]] = []
    for meta_path in sorted(directory.glob("*.meta.json")):
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        # `agent_id`は`agent-<16進数>`の形であり接頭辞を含むため、記録本体の名前へ重ねて付けない。
        agent_id = meta_path.name.removesuffix(".meta.json")
        agent_record = meta_path.with_name(f"{agent_id}{_CLAUDE_SUFFIX}")
        found.append(
            {
                "agent_id": agent_id,
                "agent_type": metadata.get("agentType"),
                "description": metadata.get("description"),
                "spawn_depth": metadata.get("spawnDepth"),
                "parent_agent_id": metadata.get("parentAgentId"),
                "model": metadata.get("model"),
                "path": str(agent_record) if agent_record.is_file() else None,
            }
        )
    return found


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
        "subagents": _subagents(path),
    }


_STDOUT_LOCK = threading.Lock()


def _emit(payload: dict[str, typing.Any]) -> None:
    """1行JSONとして出力し、SSH切断時のSIGPIPEを即時に拾えるよう毎回フラッシュする。"""
    line = json.dumps(payload, ensure_ascii=False)
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _handle_request(
    req: dict[str, typing.Any], tracker: session_watch.RecordChangeTracker | None = None
) -> dict[str, typing.Any]:
    """RPCリクエストを処理して応答辞書を返す。"""
    req_id = req.get("id")
    op = req.get("op")
    if not isinstance(req_id, int):
        return {"type": "response", "id": -1, "ok": False, "error": "invalid id"}
    try:
        if op == "list":
            return {"type": "response", "id": req_id, "ok": True, **_list_payload(tracker)}
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
        [(_claude_home() / "projects", "claude"), (_codex_home() / "sessions", "codex")],
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
                _emit(_handle_request(req, tracker))
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
