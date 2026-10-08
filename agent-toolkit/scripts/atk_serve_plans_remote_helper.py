# /// script
# requires-python = ">=3.10"
# dependencies = ["platformdirs>=4.0", "watchdog>=6.0.0"]
# ///
"""`atk serve`の計画ファイル画面が使うリモートホスト側ヘルパー。

操作種別はargvで受け取る（`list`・`read`・`search`・`watch`・`serve`）。
各サブコマンドの入出力プロトコルは対応する関数のdocstringを参照。
計画rootの定義、対象判定、走査、検索と作成日時インデックスは、同じcheckoutの`agent_toolkit`のうち
リモートの実行環境（`platformdirs`と`watchdog`だけを与える）でimportできる`_plan`配下の共有モジュールを読み込む。
本ファイルは入出力のプロトコルと変更監視だけを持つ。
"""

import base64
import json
import pathlib
import socket
import sys
import threading
import time
import typing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agent_toolkit._plan import (  # noqa: E402  # pylint: disable=wrong-import-position
    creation_times,
    locations,
    viewer_files,
)

# 走査の対象とするroot定義。`main`が起動時に1度だけ解決し、各操作が同じ定義を使う。
ROOTS: tuple[viewer_files.RootSpec, ...] | None = None


def _root_specs() -> tuple[viewer_files.RootSpec, ...]:
    """現在のroot定義を返す。"""
    if ROOTS is not None:
        return ROOTS
    return viewer_files.default_root_specs()


# 生存確認pingの送信間隔（秒）。短すぎるとトラフィックが増え、長すぎると切断検知が遅れる。
_PING_INTERVAL_SEC = 30.0

# stdoutへの書き込みは観測スレッドとRPC応答スレッドの双方から発生し得る。
# print内のwrite/flushが分割されると行JSONが破損するため、emit側で排他する。
_STDOUT_LOCK = threading.Lock()


def _host_info() -> dict[str, str]:
    """このリモートホストの`host_info`エントリ（`root`・`home`・`os_type`・`os_name`）を組み立てる。

    `root`は作業中の計画root（`~/.claude/plans`）とし、正規化は`viewer_files.host_info`が定める
    （クライアント側`copySelectedPath`が`root`・`home`を`/`区切り前提で解析するため）。
    """
    return viewer_files.host_info(locations.working_plans_root().resolve())


def _scan_snapshot() -> tuple[list[dict[str, typing.Any]], dict[str, dict[str, typing.Any]], dict[str, dict[str, str]]]:
    """全rootを独立して走査し、一覧・root情報・root状態を返す。

    root単位の警告と不在のrootの扱いは`viewer_files.scan_root`が定める。
    """
    entries: list[dict[str, typing.Any]] = []
    root_info: dict[str, dict[str, typing.Any]] = {}
    root_status: dict[str, dict[str, str]] = {}
    host = socket.gethostname()
    for spec in _root_specs():
        root_info[spec.source_id] = viewer_files.root_info(spec)
        warning: str | None = spec.warning
        if warning is None:
            items, warning = viewer_files.scan_root(
                spec.path, host, spec.source_id, migrate_legacy_ctime=spec.migrate_legacy_ctime
            )
            for item in items:
                if spec.source_id:
                    item["source_id"] = spec.source_id
                entries.append(item)
        root_status[spec.source_id] = viewer_files.root_status(warning)
    return entries, root_info, root_status


def _scan_entries() -> list[dict[str, typing.Any]]:
    """一覧用のエントリを走査する。付属ファイルは`viewer_files.is_listed_path`で除外する。"""
    entries, _, _ = _scan_snapshot()
    return entries


def _resolve_target(source_or_rel_b64: str, rel_b64: str | None = None) -> pathlib.Path:
    """Source IDと相対パスから安全な実体を解決する。旧1引数形式も受理する。"""
    if rel_b64 is None:
        source_id = ""
        rel_b64 = source_or_rel_b64
    else:
        source_id = base64.b64decode(source_or_rel_b64).decode("utf-8")
    rel = base64.b64decode(rel_b64).decode("utf-8")
    rel_path = pathlib.PurePosixPath(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError("invalid relative path")
    specs = _root_specs()
    candidates = [spec for spec in specs if spec.source_id == source_id] if source_id else specs
    matches = [
        target for spec in candidates if (target := viewer_files.resolve_under_root(spec.path, rel, spec.source_id)) is not None
    ]
    if len(matches) != 1:
        if len(matches) > 1:
            raise ValueError("source is required")
        raise FileNotFoundError(rel)
    return matches[0]


def _read_payload(source_or_rel_b64: str, rel_b64: str | None = None) -> dict[str, typing.Any]:
    """指定相対パスのファイル本文をRPC応答用辞書として返す。"""
    target = _resolve_target(source_or_rel_b64, rel_b64)
    return {"data": base64.b64encode(target.read_bytes()).decode("ascii")}


def _search_payload(query_b64: str, source_id: str | None = None) -> dict[str, typing.Any]:
    """本文へ検索語が部分一致する計画ファイルの相対パスを返す。"""
    query = base64.b64decode(query_b64).decode("utf-8")
    matched: list[str] = []
    matches: list[dict[str, str]] = []
    specs = [spec for spec in _root_specs() if source_id in (None, "") or spec.source_id == source_id]
    for spec in specs:
        for rel in viewer_files.search_root(spec.path, query, spec.source_id):
            matched.append(rel)
            matches.append({"source_id": spec.source_id, "path": rel})
    payload: dict[str, typing.Any] = {"paths": sorted(matched)}
    if len(specs) > 1 or any(item["source_id"] for item in matches):
        payload["matches"] = sorted(matches, key=lambda item: (item["source_id"], item["path"]))
    return payload


def _list_files() -> None:
    """`list`サブコマンド: 全root配下の`.md`一覧をJSON文字列でstdoutへ出力する。"""
    json.dump(_scan_entries(), sys.stdout, ensure_ascii=False)


def _read_file(source_or_rel_b64: str, rel_b64: str | None = None) -> None:
    """`read`サブコマンド: 指定相対パスのファイル本文をJSON文字列でstdoutへ出力する。

    応答形式（代替取得用、単発SSH呼び出し）:
        {"data":"<base64本文>"}
    """
    json.dump(_read_payload(source_or_rel_b64, rel_b64), sys.stdout, ensure_ascii=False)


def _emit(payload: dict[str, typing.Any]) -> None:
    # 1行JSONとして出力し、SSH切断時のSIGPIPEを即時に拾えるよう毎回フラッシュする。
    line = json.dumps(payload, ensure_ascii=False)
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _start_observer(stop_event: threading.Event) -> typing.Any:
    """Watchdog Observerとping送信スレッドを起動し、Observerを返す。

    `serve`/`watch`の両サブコマンドで共通利用する。rootは自動作成せず、
    不在・権限不足・監視失敗をroot単位のsnapshot状態へ記録する。
    """
    # watchdogはPEP 723の`dependencies`またはbootstrap側の`--with`指定で都度解決される。
    # `list`/`read`では不要のため遅延importでstartup時間を抑える。
    import watchdog.events  # pylint: disable=import-outside-toplevel
    import watchdog.observers  # pylint: disable=import-outside-toplevel

    # 読み取り由来の`FileOpenedEvent`/`FileClosedNoWriteEvent`は除外し、
    # `FileMovedEvent`はatomic-write rename対応のためdest側も判定対象に含める。
    watched_types = (
        watchdog.events.FileCreatedEvent,
        watchdog.events.FileModifiedEvent,
        watchdog.events.FileDeletedEvent,
        watchdog.events.FileMovedEvent,
        watchdog.events.FileClosedEvent,
    )

    specs = _root_specs()
    root_status: dict[str, dict[str, str]] = {}

    class Handler(watchdog.events.FileSystemEventHandler):
        """一つのroot配下の変更を行区切りJSONとして通知するイベントハンドラ。"""

        def __init__(self, spec: viewer_files.RootSpec) -> None:
            super().__init__()
            self.spec = spec

        def on_any_event(self, event: typing.Any) -> None:
            if not isinstance(event, watched_types):
                return
            if event.is_directory:
                return
            src = pathlib.Path(str(event.src_path))
            if isinstance(event, watchdog.events.FileMovedEvent):
                dest = pathlib.Path(str(event.dest_path))
                src_ok = viewer_files.is_target_path(src, self.spec.path, self.spec.source_id)
                dest_ok = viewer_files.is_target_path(dest, self.spec.path, self.spec.source_id)
                if not (src_ok or dest_ok):
                    return
                # rename処理でsrcのみ`.md`の場合は元パス側を削除扱い、
                # destが`.md`なら新パス側をupsertする。
                if src_ok and not dest_ok:
                    payload: dict[str, typing.Any] = {
                        "type": "deleted",
                        "path": src.relative_to(self.spec.path).as_posix(),
                    }
                    if self.spec.source_id:
                        payload["source_id"] = self.spec.source_id
                    _emit(payload)
                    return
                target = dest if dest_ok else src
                self._emit_upsert(target)
                return
            if not viewer_files.is_target_path(src, self.spec.path, self.spec.source_id):
                return
            if isinstance(event, watchdog.events.FileDeletedEvent):
                payload = {"type": "deleted", "path": src.relative_to(self.spec.path).as_posix()}
                if self.spec.source_id:
                    payload["source_id"] = self.spec.source_id
                _emit(payload)
                return
            self._emit_upsert(src)

        def _emit_upsert(self, path: pathlib.Path) -> None:
            try:
                st = path.stat()
            except OSError as e:
                sys.stderr.write(f"warn: stat failed for {path}: {e}\n")
                return
            rel = path.relative_to(self.spec.path).as_posix()
            # 単一ファイルの更新通知は走査結果ではないため、不在キーの回収は行わない。
            resolved = creation_times.update_creation_time_index(
                socket.gethostname(),
                self.spec.path,
                {rel: creation_times.observed_creation_epoch(st)},
                prune=False,
                migrate_legacy=viewer_files.migrates_legacy_ctime(self.spec.source_id, self.spec.migrate_legacy_ctime),
            )
            payload = {
                "type": "upsert",
                "path": rel,
                "name": path.name,
                "mtime_epoch": st.st_mtime,
                "ctime_epoch": resolved[rel],
            }
            if self.spec.source_id:
                payload["source_id"] = self.spec.source_id
            _emit(payload)

    def ping_loop() -> None:
        while not stop_event.wait(_PING_INTERVAL_SEC):
            try:
                _emit({"type": "ping"})
            except BrokenPipeError:
                stop_event.set()
                return

    observer = watchdog.observers.Observer()
    scheduled = False
    for spec in specs:
        if not spec.path.is_dir():
            continue
        try:
            observer.schedule(Handler(spec), str(spec.path), recursive=True)
            scheduled = True
        except OSError as error:
            root_status[spec.source_id] = viewer_files.root_status(f"rootの監視に失敗しました: {error}")
    if scheduled:
        try:
            observer.start()
        except OSError as error:
            for spec in specs:
                if spec.path.is_dir():
                    root_status[spec.source_id] = viewer_files.root_status(f"rootの監視に失敗しました: {error}")
    # observer起動後にsnapshotを発行することで、起動以前の変更取りこぼしを排除する。
    entries, root_info, scanned_status = _scan_snapshot()
    scanned_status.update(root_status)
    _emit(
        {
            "type": "snapshot",
            "entries": entries,
            "host_info": _host_info(),
            "root_info": root_info,
            "root_status": scanned_status,
        }
    )

    ping_thread = threading.Thread(target=ping_loop, daemon=True)
    ping_thread.start()
    return observer


def _wait_until_stopped(stop_event: threading.Event, observer: typing.Any) -> int:
    """停止の要求か割り込みまで待ち、変更監視を止めて終了コードを返す。"""
    try:
        while not stop_event.is_set():
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if observer.is_alive():
            observer.stop()
            observer.join()
    return 0


def _watch_files() -> int:
    """`watch`サブコマンド: `~/.claude/plans`配下をwatchdogで監視し、行区切りJSONをstdoutへ出力する。

    行プロトコル（行区切りJSON）:
        - {"type":"snapshot","entries":[{"path":..., "name":..., "mtime_epoch":..., "ctime_epoch":...}, ...],
           "host_info":{"root":..., "home":..., "os_type":..., "os_name":...}}
        - {"type":"upsert","path":..., "name":..., "mtime_epoch":..., "ctime_epoch":...}
        - {"type":"deleted","path":...}
        - {"type":"ping"}  ※30秒間隔。SSH切断時のSIGPIPE誘発で生存確認とする
    """
    stop_event = threading.Event()
    observer = _start_observer(stop_event)
    # SIGPIPEはping_loopが捕捉してstop_eventを通じて停止処理を実行する。
    return _wait_until_stopped(stop_event, observer)


def _handle_request(req: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """RPCリクエストを処理して応答辞書を返す。"""
    req_id = req.get("id")
    op = req.get("op")
    if not isinstance(req_id, int):
        return {"type": "response", "id": -1, "ok": False, "error": "invalid id"}
    try:
        if op == "read":
            source_id = str(req.get("source_id", ""))
            path_b64 = str(req.get("path", ""))
            if source_id:
                source_b64 = base64.b64encode(source_id.encode("utf-8")).decode("ascii")
                payload = _read_payload(source_b64, path_b64)
            else:
                payload = _read_payload(path_b64)
            return {"type": "response", "id": req_id, "ok": True, **payload}
        if op == "search":
            source_id = str(req.get("source_id", "")) or None
            payload = _search_payload(str(req.get("query", "")), source_id)
            return {"type": "response", "id": req_id, "ok": True, **payload}
        return {"type": "response", "id": req_id, "ok": False, "error": f"unknown op: {op}"}
    except FileNotFoundError as error:
        return {
            "type": "response",
            "id": req_id,
            "ok": False,
            "error_type": "not_found",
            "error": f"FileNotFoundError: {error}",
        }
    except Exception as e:  # noqa: BLE001  pylint: disable=broad-exception-caught
        return {"type": "response", "id": req_id, "ok": False, "error": f"{type(e).__name__}: {e}"}


def _serve() -> int:
    """`serve`サブコマンド: watchの行ストリームに加え、stdinのRPCリクエストへ応答する常駐モード。

    watch行プロトコルは`_watch_files`と共通。
    RPCプロトコル（行区切りJSON）:
        リクエスト（stdin）: {"id":<int>, "op":"read", "path":"<base64>"}
                            または{"id":<int>, "op":"search", "query":"<base64>"}
        応答（stdout）:
            成功: {"type":"response", "id":<int>, "ok":true, "data":"<base64本文>"}
            失敗: {"type":"response", "id":<int>, "ok":false, "error":"<msg>"}
            不在: 失敗応答に"error_type":"not_found"を持つ。通信障害と区別する。
    """
    stop_event = threading.Event()
    observer = _start_observer(stop_event)

    def reader_loop() -> None:
        # stdinはサーバー側からの行JSONリクエストを受け取る。
        # EOFまたは入力エラーで終了し、stop_eventを通じてメインループへ伝播する。
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                _emit({"type": "response", "id": -1, "ok": False, "error": f"json: {e}"})
                continue
            try:
                _emit(_handle_request(req))
            except BrokenPipeError:
                stop_event.set()
                return
        stop_event.set()

    reader_thread = threading.Thread(target=reader_loop, daemon=True)
    reader_thread.start()
    return _wait_until_stopped(stop_event, observer)


def main() -> int:
    """操作名を引数に取り、結果をJSONで標準出力へ出力する。"""
    if len(sys.argv) < 2:
        sys.stderr.write("missing operation\n")
        return 2
    # 前回の異常終了で残った作成日時インデックスの一時ファイルを操作分岐の前に除去する。
    creation_times.cleanup_creation_time_temporaries()
    globals()["ROOTS"] = _root_specs()
    op = sys.argv[1]
    if op == "list":
        _list_files()
        return 0
    if op == "read":
        if len(sys.argv) < 3:
            sys.stderr.write("missing path\n")
            return 2
        if len(sys.argv) >= 4:
            _read_file(sys.argv[2], sys.argv[3])
        else:
            _read_file(sys.argv[2])
        return 0
    if op == "search":
        if len(sys.argv) < 3:
            sys.stderr.write("missing query\n")
            return 2
        if len(sys.argv) >= 4:
            source_id = base64.b64decode(sys.argv[2]).decode("utf-8")
            query_b64 = sys.argv[3]
        else:
            source_id = None
            query_b64 = sys.argv[2]
        json.dump(_search_payload(query_b64, source_id), sys.stdout, ensure_ascii=False)
        return 0
    if op == "watch":
        return _watch_files()
    if op == "serve":
        return _serve()
    sys.stderr.write(f"unknown operation: {op}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
