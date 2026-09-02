# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""`atk serve`のセッション画面が使うリモートホスト側ヘルパー。

操作種別はargvで受け取る（`list`・`read`・`serve`）。
`list`は保存済みセッションの一覧を、`read`は1件の記録本文をJSONで返す。
`serve`はstdinから行区切りJSONのRPCを受け取り、同じ内容をstdoutへ返す常駐モードとする。

保存先の規約は`agent-toolkit/skills/agent-standards/references/session-records.md`を正本とし、
サーバー側`_atk_serve_sessions.py`と同じ規約で解決する
（SSH越しに単独実行されるためモジュールを共有できず、意図的に重複させている）。
"""

import base64
import json
import os
import pathlib
import socket
import sys
import threading
import typing

# 1件の記録から取得する最大バイト数。過大な記録の全文転送により接続が占有される事態を避ける上限とする。
MAX_RECORD_BYTES = 64 * 1024 * 1024
# 一覧が返す最大件数。古い記録は調査対象になりにくいため、更新日時の新しい順で打ち切る。
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


def _iter_claude_records() -> typing.Iterator[tuple[pathlib.Path, str]]:
    """Claude Codeのセッション本体の記録を`(パス, プロジェクト表記)`として返す。

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
                yield path, project_dir.name


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


def _entry(path: pathlib.Path, engine: str, session_id: str, project: str | None) -> dict[str, typing.Any]:
    """一覧の1件を組み立てる。読み取れない情報は`None`のままとする。"""
    try:
        st = path.stat()
        size = st.st_size
        updated_at = st.st_mtime
    except OSError as error:
        return {
            "engine": engine,
            "session_id": session_id,
            "project": project,
            "path": None,
            "size": None,
            "updated_at": None,
            "warning": f"記録の情報を取得できません: {error}",
        }
    return {
        "engine": engine,
        "session_id": session_id,
        "project": project,
        "path": str(path),
        "size": size,
        "updated_at": updated_at,
        "warning": None,
    }


def _list_payload() -> dict[str, typing.Any]:
    """ローカルの保存済みセッション一覧を返す。"""
    entries: list[dict[str, typing.Any]] = []
    for path, project in _iter_claude_records():
        entries.append(_entry(path, "claude", path.stem, project))
    for path in _iter_codex_records():
        entries.append(_entry(path, "codex", _codex_session_id(path), None))
    entries.sort(key=lambda item: (item["updated_at"] is not None, item["updated_at"] or 0.0), reverse=True)
    return {"host": socket.gethostname(), "entries": entries[:MAX_LIST_ENTRIES]}


def _is_safe_record_path(raw: str) -> bool:
    """読み取り要求のパスが保存先配下の記録を指すかを検証する。

    上位ディレクトリへの参照と対象外の接尾辞を拒否し、いずれかの保存先の配下だけを受理する。
    """
    if not raw or ".." in pathlib.PurePosixPath(raw).parts:
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


def _read_payload(path_b64: str) -> dict[str, typing.Any]:
    """指定パスの記録本文をbase64で返す。"""
    raw = base64.b64decode(path_b64).decode("utf-8")
    if not _is_safe_record_path(raw):
        raise ValueError("invalid record path")
    path = pathlib.Path(raw)
    data = path.read_bytes()
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError(f"record too large: {len(data)} bytes")
    return {"data": base64.b64encode(data).decode("ascii"), "mtime_epoch": path.stat().st_mtime}


_STDOUT_LOCK = threading.Lock()


def _emit(payload: dict[str, typing.Any]) -> None:
    """1行JSONとして出力し、SSH切断時のSIGPIPEを即時に拾えるよう毎回フラッシュする。"""
    line = json.dumps(payload, ensure_ascii=False)
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _handle_request(req: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """RPCリクエストを処理して応答辞書を返す。"""
    req_id = req.get("id")
    op = req.get("op")
    if not isinstance(req_id, int):
        return {"type": "response", "id": -1, "ok": False, "error": "invalid id"}
    try:
        if op == "list":
            return {"type": "response", "id": req_id, "ok": True, **_list_payload()}
        if op == "read":
            return {"type": "response", "id": req_id, "ok": True, **_read_payload(str(req.get("path", "")))}
        return {"type": "response", "id": req_id, "ok": False, "error": f"unknown op: {op}"}
    except Exception as error:  # noqa: BLE001  pylint: disable=broad-exception-caught
        return {"type": "response", "id": req_id, "ok": False, "error": f"{type(error).__name__}: {error}"}


def _serve() -> int:
    """stdinの行区切りJSONリクエストへ応答する常駐モード。

    RPCプロトコル（行区切りJSON）:
        リクエスト（stdin）: {"id":<int>, "op":"list"}
                            または{"id":<int>, "op":"read", "path":"<base64絶対パス>"}
        応答（stdout）:
            成功: {"type":"response", "id":<int>, "ok":true, ...}
            失敗: {"type":"response", "id":<int>, "ok":false, "error":"<msg>"}
    起動直後に`{"type":"ready","host":...}`を1行出力し、呼び出し側の接続確立の契機とする。
    """
    _emit({"type": "ready", "host": socket.gethostname()})
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
            _emit(_handle_request(req))
        except BrokenPipeError:
            return 0
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
