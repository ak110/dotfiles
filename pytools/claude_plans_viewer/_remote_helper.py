# /// script
# requires-python = ">=3.10"
# dependencies = ["watchdog>=6.0.0"]
# ///
"""claude_plans_viewerのリモートホスト側ヘルパー。

操作種別はargvで受け取る:
  - list           : ~/.claude/plans配下の.mdファイル一覧をJSON文字列でstdoutへ出力する
  - read <b64>     : 指定相対パスのファイル本文と`mtime_epoch`をJSON文字列でstdoutへ出力する
  - watch          : ~/.claude/plans配下をwatchdogで監視し、行区切りJSONをstdoutへ出力する
  - serve          : watchの行ストリームに加え、stdinのRPCリクエストへ応答する常駐モード

read 応答（fallback経路用、単発SSH呼び出し）:
  {"data":"<base64本文>", "mtime_epoch":<float>}

watch / serve サブコマンドの行プロトコル（行区切りJSON）:
  - {"type":"snapshot","entries":[{"path":..., "name":..., "mtime_epoch":...}, ...]}
  - {"type":"upsert","path":..., "name":..., "mtime_epoch":...}
  - {"type":"deleted","path":...}
  - {"type":"ping"}  ※30秒間隔。SSH切断時のSIGPIPE誘発で生存確認とする

serve サブコマンドのRPC追加（行区切りJSON）:
  リクエスト（stdin）: {"id":<int>, "op":"read", "path":"<base64>"}
  応答（stdout）:
    成功: {"type":"response", "id":<int>, "ok":true, "data":"<base64本文>", "mtime_epoch":<float>}
    失敗: {"type":"response", "id":<int>, "ok":false, "error":"<msg>"}

本ファイルはリモートホストの`$HOME/dotfiles/pytools/claude_plans_viewer/_remote_helper.py`に
チェックアウトされている前提で、SSH経由の短いPython bootstrapから
`pathlib.Path(...).read_text(encoding="utf-8")`で読み込まれて`exec`される。
PEP 723ヘッダーは`uv run --no-project --script <path>`で直接実行する場合のために残してあるが、
通常経路ではbootstrap側で`uv run --no-project --with "watchdog>=6.0.0" python -c ...`が使う。
"""

import base64
import json
import pathlib
import sys
import threading
import time
import typing

ROOT = pathlib.Path.home() / ".claude" / "plans"

# 生存確認pingの送信間隔（秒）。短すぎるとトラフィックが増え、長すぎると切断検知が遅れる。
_PING_INTERVAL_SEC = 30.0

# stdoutへの書き込みは観測スレッドとRPC応答スレッドの双方から発生し得る。
# print内のwrite/flushが分割されると行JSONが破損するため、emit側で排他する。
_STDOUT_LOCK = threading.Lock()


def _is_target_path(path: pathlib.Path) -> bool:
    if path.suffix != ".md":
        return False
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False
    return not any(p.startswith(".") for p in rel.parts)


def _scan_entries() -> list[dict[str, typing.Any]]:
    entries: list[dict[str, typing.Any]] = []
    if not ROOT.is_dir():
        return entries
    for path in ROOT.rglob("*.md"):
        if not path.is_file():
            continue
        if not _is_target_path(path):
            continue
        st = path.stat()
        entries.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "name": path.name,
                "mtime_epoch": st.st_mtime,
            }
        )
    return entries


def _resolve_target(rel_b64: str) -> pathlib.Path:
    rel = base64.b64decode(rel_b64).decode("utf-8")
    rel_path = pathlib.PurePosixPath(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError("invalid relative path")
    target = (ROOT / rel).resolve()
    target.relative_to(ROOT.resolve())
    if target.suffix != ".md" or not target.is_file():
        raise FileNotFoundError(rel)
    return target


def _read_payload(rel_b64: str) -> dict[str, typing.Any]:
    """指定相対パスのファイル本文と`mtime_epoch`をRPC応答用辞書として返す。

    `read_bytes`と`stat`を続けて呼ぶことで、本文と取得時点のmtimeをペアで提供する。
    呼び出し側はこの`mtime_epoch`をMarkdownキャッシュキーへ使い、watch通知の遅延と
    無関係に正確性を担保する。
    """
    target = _resolve_target(rel_b64)
    data = target.read_bytes()
    st = target.stat()
    return {
        "data": base64.b64encode(data).decode("ascii"),
        "mtime_epoch": st.st_mtime,
    }


def list_files() -> None:
    json.dump(_scan_entries(), sys.stdout, ensure_ascii=False)


def read_file(rel_b64: str) -> None:
    json.dump(_read_payload(rel_b64), sys.stdout, ensure_ascii=False)


def _emit(payload: dict[str, typing.Any]) -> None:
    # 1行JSONとして出力し、SSH切断時のSIGPIPEを即時に拾えるよう毎回フラッシュする。
    line = json.dumps(payload, ensure_ascii=False)
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _start_observer(stop_event: threading.Event) -> typing.Any:
    """Watchdog Observerとping送信スレッドを起動し、Observerを返す。

    `serve`/`watch`の両サブコマンドで共通利用する。
    `~/.claude/plans`未作成のホストでも起動できるよう、無ければ作成する。
    作成失敗時はsnapshot空・ping待機のみで継続する。
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

    try:
        ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"warn: cannot create {ROOT}: {e}\n")

    class Handler(watchdog.events.FileSystemEventHandler):
        """`~/.claude/plans`配下の変更を行区切りJSONとしてstdoutへ通知するイベントハンドラ。"""

        def on_any_event(self, event: typing.Any) -> None:
            if not isinstance(event, watched_types):
                return
            if event.is_directory:
                return
            src = pathlib.Path(str(event.src_path))
            if isinstance(event, watchdog.events.FileMovedEvent):
                dest = pathlib.Path(str(event.dest_path))
                src_ok = _is_target_path(src)
                dest_ok = _is_target_path(dest)
                if not (src_ok or dest_ok):
                    return
                # rename経路でsrcのみ`.md`の場合は元パス側を削除扱い、
                # destが`.md`なら新パス側をupsertする。
                if src_ok and not dest_ok:
                    _emit({"type": "deleted", "path": src.relative_to(ROOT).as_posix()})
                    return
                target = dest if dest_ok else src
                self._emit_upsert(target)
                return
            if not _is_target_path(src):
                return
            if isinstance(event, watchdog.events.FileDeletedEvent):
                _emit({"type": "deleted", "path": src.relative_to(ROOT).as_posix()})
                return
            self._emit_upsert(src)

        @staticmethod
        def _emit_upsert(path: pathlib.Path) -> None:
            try:
                st = path.stat()
            except OSError as e:
                sys.stderr.write(f"warn: stat failed for {path}: {e}\n")
                return
            _emit(
                {
                    "type": "upsert",
                    "path": path.relative_to(ROOT).as_posix(),
                    "name": path.name,
                    "mtime_epoch": st.st_mtime,
                }
            )

    def ping_loop() -> None:
        while not stop_event.wait(_PING_INTERVAL_SEC):
            try:
                _emit({"type": "ping"})
            except BrokenPipeError:
                stop_event.set()
                return

    observer = watchdog.observers.Observer()
    if ROOT.is_dir():
        observer.schedule(Handler(), str(ROOT), recursive=True)
        observer.start()
    # observer起動後にsnapshotを発行することで、起動以前の変更取りこぼしを排除する。
    _emit({"type": "snapshot", "entries": _scan_entries()})

    ping_thread = threading.Thread(target=ping_loop, daemon=True)
    ping_thread.start()
    return observer


def watch_files() -> int:
    stop_event = threading.Event()
    observer = _start_observer(stop_event)
    # SIGPIPEはping_loopが捕捉してstop_eventを通じて停止経路に乗せる。
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


def _handle_request(req: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """RPCリクエストを処理して応答辞書を返す。"""
    req_id = req.get("id")
    op = req.get("op")
    if not isinstance(req_id, int):
        return {"type": "response", "id": -1, "ok": False, "error": "invalid id"}
    try:
        if op == "read":
            payload = _read_payload(str(req.get("path", "")))
            return {"type": "response", "id": req_id, "ok": True, **payload}
        return {"type": "response", "id": req_id, "ok": False, "error": f"unknown op: {op}"}
    except Exception as e:  # noqa: BLE001  pylint: disable=broad-exception-caught
        return {"type": "response", "id": req_id, "ok": False, "error": f"{type(e).__name__}: {e}"}


def serve() -> int:
    """watch通知とstdin RPCの両方を1プロセスで処理する常駐モード。"""
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


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("missing operation\n")
        return 2
    op = sys.argv[1]
    if op == "list":
        list_files()
        return 0
    if op == "read":
        if len(sys.argv) < 3:
            sys.stderr.write("missing path\n")
            return 2
        read_file(sys.argv[2])
        return 0
    if op == "watch":
        return watch_files()
    if op == "serve":
        return serve()
    sys.stderr.write(f"unknown operation: {op}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
