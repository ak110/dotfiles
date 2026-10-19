"""agents_serverの共通診断ログと、サーバー起動単位の終了診断を扱う。

終了診断は起動単位ごとのJSON Linesファイル（`<state_dir>/agents-server-instances/<識別子>.jsonl`）へ追記する。
サーバー自身と、終了を待つ起動ランチャー（`scripts/launch.py`）の双方が書く。
ランチャーは依存導入前の環境から本モジュールを読むため、本モジュールは標準ライブラリと`_common/state_paths`だけに依存する。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import logging.handlers
import os
import pathlib
import queue
import secrets
import signal
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.next_action import next_action_line

LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3

INSTANCE_DIRECTORY_NAME = "agents-server-instances"
INSTANCE_FILE_MAX_BYTES = 64 * 1024
"""1起動単位の診断ファイルの上限。超える追記は行わず`truncated`を1行だけ書く。"""
INSTANCE_DIRECTORY_MAX_BYTES = 64 * 1024 * 1024
"""診断ディレクトリ全体の上限。掃引は合計がこの値以下になるまで、参照元の無いファイルを古い順に削除する。"""
SIGNAL_EVENT_LIMIT = 8
LAUNCH_RECORD_ENV = "AGENT_TOOLKIT_AGENTS_SERVER_LAUNCH_RECORD"
"""ランチャーが作成した合流ファイルの絶対パス。サーバーは開始時に起動単位と診断ファイルの所在を書く。"""
TERMINATION_EVENTS = frozenset({"exited", "signal_received", "process_exited"})
MESSAGE_LIMIT = 2000
"""`exited`の例外メッセージを保存する最大文字数。"""
_INSTANCE_LOCK = threading.Lock()


def _warning_text(reason: str, impact: str, action: str) -> str:
    """警告の理由・影響・次の操作を1行のログ本文へそろえる。`%`の書式指定は理由に含めたものをそのまま使う。"""
    return f"{reason}。{impact}。{next_action_line(action)}"


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """agents_serverが追加した標準エラー向けhandler。

    同じloggerへ二重に追加しないための識別を型で表す。
    """


class _LogFileHandler(logging.Handler):
    """agents_serverが追加した永続ファイル向けhandler。

    出力先の比較で`baseFilename`を読むため、その属性を持つ型として扱えるようにする。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._sink = logging.handlers.RotatingFileHandler(*args, **kwargs)
        self.baseFilename = self._sink.baseFilename
        self._records: queue.Queue[logging.LogRecord | None] = queue.Queue()
        self._accepting = True
        self._worker = threading.Thread(target=self._write_records, name="agents-server-log", daemon=True)
        self._worker.start()

    def _write_records(self) -> None:
        while (record := self._records.get()) is not None:
            try:
                self._sink.handle(record)
            finally:
                self._records.task_done()
        self._records.task_done()

    def emit(self, record: logging.LogRecord) -> None:
        """ログの順序を保つ専用workerへ記録を渡す。"""
        if not self._accepting:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._records.join()
            self._sink.handle(record)
        else:
            self._records.put(record)

    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        """出力を所有するhandlerへ書式を渡す。"""
        super().setFormatter(fmt)
        self._sink.setFormatter(fmt)

    def flush(self) -> None:
        """受理済みの全記録を保存する。"""
        self._records.join()
        self._sink.flush()

    def close(self) -> None:
        """受理した全記録の出力とworkerの終了を待つ。"""
        self.acquire()
        try:
            if self._accepting:
                self._accepting = False
                self._records.put(None)
        finally:
            self.release()
        self._worker.join()
        self._sink.close()
        super().close()


def close_logging() -> None:
    """所有するログworkerを全て終了し、保存を完了する。"""
    logger = logging.getLogger("agent-toolkit.agents-server")
    for handler in tuple(logger.handlers):
        if isinstance(handler, _LogFileHandler):
            logger.removeHandler(handler)
            handler.close()


def flush_logging() -> None:
    """所有するログworkerの受理済み出力を完了させる。"""
    for handler in logging.getLogger("agent-toolkit.agents-server").handlers:
        if isinstance(handler, _LogFileHandler):
            handler.flush()


def configure_logging() -> pathlib.Path:
    """標準エラーと永続ファイルへagents_serverの診断ログを出力する。"""
    log_level = os.environ.get("AGENT_TOOLKIT_AGENTS_LOG_LEVEL", "WARNING")
    server_logger = logging.getLogger("agent-toolkit.agents-server")
    server_logger.setLevel(logging.INFO)
    server_logger.propagate = False
    if not any(isinstance(handler, _StderrHandler) for handler in server_logger.handlers):
        stderr_handler = _StderrHandler()
        stderr_handler.setLevel(log_level)
        stderr_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        server_logger.addHandler(stderr_handler)

    log_path = _state_paths.state_dir() / "agents-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for handler in tuple(server_logger.handlers):
        if not isinstance(handler, _LogFileHandler):
            continue
        if pathlib.Path(handler.baseFilename) == log_path:
            break
        server_logger.removeHandler(handler)
        handler.close()
    if not any(isinstance(handler, _LogFileHandler) for handler in server_logger.handlers):
        file_handler = _LogFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        server_logger.addHandler(file_handler)
    return log_path


@dataclasses.dataclass(frozen=True)
class ServerInstance:
    """MCPサーバーの起動単位の識別子と、その終了診断ファイルの絶対パス。"""

    instance_id: str
    path: pathlib.Path


_CURRENT_INSTANCE: ServerInstance | None = None


def instance_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """起動単位の終了診断を置くディレクトリを返す。"""
    return (_state_paths.state_dir() if state_root is None else state_root) / INSTANCE_DIRECTORY_NAME


def instance_path(instance_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """起動単位の終了診断ファイルのパスを返す。"""
    return instance_directory(state_root) / f"{instance_id}.jsonl"


def end_server_instance() -> None:
    """このプロセスの起動単位を終える。以降の`record_instance_event`は何も書かない。"""
    global _CURRENT_INSTANCE  # pylint: disable=global-statement
    _CURRENT_INSTANCE = None


def current_server_instance() -> ServerInstance | None:
    """このプロセスが開始した起動単位を返す。stdio起動を開始していなければ`None`を返す。"""
    return _CURRENT_INSTANCE


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def append_instance_event(path: pathlib.Path, *, writer: str, event: str, **fields: Any) -> bool:
    """診断ファイルへ1イベントを追記し、成否を返す。

    上限を超える追記は行わず、上限に達した時点で`truncated`を1行だけ書く。書込失敗は例外を送出しない。
    """
    line = json.dumps({"at": _now(), "event": event, "writer": writer, **fields}, ensure_ascii=False) + "\n"
    encoded = line.encode("utf-8")
    try:
        with _INSTANCE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            size = path.stat().st_size if path.exists() else 0
            if size + len(encoded) > INSTANCE_FILE_MAX_BYTES:
                if not _ends_with_truncated(path):
                    marker = json.dumps({"at": _now(), "event": "truncated", "writer": writer}, ensure_ascii=False) + "\n"
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(marker)
                return False
            with path.open("ab") as stream:
                stream.write(encoded)
    except OSError as exc:
        logging.getLogger("agent-toolkit.agents-server").warning(
            _warning_text(
                "終了診断を書き込めませんでした: path=%s error=%s",
                "この起動単位の終了診断の記録が欠けるだけで、サーバーの処理は続く",
                "対象パスの書込権限と状態ディレクトリの空き容量を確認してください。",
            ),
            path,
            exc,
        )
        return False
    return True


def _ends_with_truncated(path: pathlib.Path) -> bool:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 512))
            tail = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return bool(tail) and '"event": "truncated"' in tail[-1]


def record_instance_event(event: str, **fields: Any) -> None:
    """現在の起動単位の診断ファイルへサーバー自身のイベントを追記する。起動単位が無ければ何もしない。"""
    instance = _CURRENT_INSTANCE
    if instance is not None:
        append_instance_event(instance.path, writer="server", event=event, **fields)


def start_server_instance(
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
    cwd: pathlib.Path | None = None,
) -> ServerInstance:
    """起動単位を生成して`started`を記録し、ランチャーの合流ファイルへ所在を書く。"""
    global _CURRENT_INSTANCE  # pylint: disable=global-statement
    env = os.environ if environment is None else environment
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    instance_id = f"{stamp}-{os.getpid()}-{secrets.token_hex(4)}"
    instance = ServerInstance(instance_id, instance_path(instance_id, state_root))
    _CURRENT_INSTANCE = instance
    working_directory = pathlib.Path.cwd() if cwd is None else cwd
    launch_record = env.get(LAUNCH_RECORD_ENV)
    append_instance_event(
        instance.path,
        writer="server",
        event="started",
        pid=os.getpid(),
        create_time=_process_create_time(),
        parent_pid=os.getppid(),
        cwd=str(working_directory),
        launcher=bool(launch_record),
        parent_connection_log=parent_connection_log(env, working_directory),
    )
    if launch_record:
        _write_launch_record(pathlib.Path(launch_record), instance)
    return instance


def _process_create_time() -> float | None:
    """psutilを使える環境では自身の起動時刻を返す。ランチャーと同じ標準ライブラリだけの環境では`None`を返す。"""
    try:
        import psutil  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        return psutil.Process(os.getpid()).create_time()
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _write_launch_record(path: pathlib.Path, instance: ServerInstance) -> None:
    payload = json.dumps({"server_instance": instance.instance_id, "diagnostics_path": str(instance.path)}) + "\n"
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        logging.getLogger("agent-toolkit.agents-server").warning(
            _warning_text(
                "ランチャーへ診断の所在を渡せませんでした: path=%s error=%s",
                "ランチャーが終了状態を診断へ追記できず、この起動単位の終了診断はサーバー自身の記録だけになる",
                "合流ファイルのあるディレクトリ（一時領域）の書込権限と空き容量を確認してください。",
            ),
            path,
            exc,
        )


def read_launch_record(path: pathlib.Path) -> pathlib.Path | None:
    """ランチャーの合流ファイルからサーバーが報告した診断ファイルの所在を返す。未報告なら`None`を返す。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    value = payload.get("diagnostics_path") if isinstance(payload, dict) else None
    return pathlib.Path(value) if isinstance(value, str) and pathlib.Path(value).is_absolute() else None


def claude_cache_root(environment: Mapping[str, str], *, os_name: str | None = None) -> pathlib.Path | None:
    """Claude CLIのキャッシュディレクトリを返す。解決できなければ`None`を返す。"""
    if (os.name if os_name is None else os_name) == "nt":
        local = environment.get("LOCALAPPDATA")
        return pathlib.Path(local) / "claude-cli-nodejs" / "Cache" if local else None
    home = environment.get("HOME")
    return (pathlib.Path(home) if home else pathlib.Path.home()) / ".cache" / "claude-cli-nodejs"


def cwd_slug(cwd: pathlib.Path) -> str:
    """Claude Codeが作業ディレクトリごとの記録に使うスラッグ（英数字以外をハイフンへ置換）を返す。"""
    return "".join(character if character.isascii() and character.isalnum() else "-" for character in str(cwd))


def parent_connection_log(
    environment: Mapping[str, str],
    cwd: pathlib.Path,
    *,
    os_name: str | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, str]:
    """親CLIがこの起動について残す接続記録の候補を返す。取得できなければ理由を返す。"""
    if environment.get("CLAUDECODE") != "1":
        return {"reason": "unsupported_parent"}
    root = claude_cache_root(environment, os_name=os_name)
    if root is None:
        return {"reason": "not_found"}
    limit = (datetime.datetime.now(datetime.UTC) if now is None else now).strftime("%Y-%m-%dT%H-%M-%S")
    candidates: list[pathlib.Path] = []
    try:
        for directory in (root / cwd_slug(cwd)).glob("mcp-logs-*agents*server*"):
            if directory.is_dir():
                candidates.extend(path for path in directory.glob("*.jsonl") if path.stem[:19] <= limit)
    except OSError:
        candidates = []
    if not candidates:
        return {"reason": "not_found"}
    # 記録の名前は起動時刻であり、名前の順が起動の順になる。
    latest = max(candidates, key=_record_name)
    return {"path": str(latest)}


def _record_name(path: pathlib.Path) -> str:
    return path.stem


def install_signal_recording(signals: Iterable[signal.Signals] | None = None) -> Callable[[], None]:
    """受信シグナルを診断へ記録してから、設置前と同じ処理へ渡すhandlerを設置する。

    設置前のhandler（asyncioのSIGINT処理など）は記録後にそのまま呼ぶ。設置前が`SIG_DFL`のシグナルは`SIG_DFL`へ戻して自身へ再送する。
    終了の経過と呼出元へ返す終了状態を変えないためである。設置前のhandlerへ戻す関数を返す。
    """
    if signals is None:
        names = ("SIGINT",) if os.name == "nt" else ("SIGINT", "SIGTERM", "SIGHUP")
        signals = tuple(getattr(signal, name) for name in names if hasattr(signal, name))
    recorded = [0]
    previous_handlers: dict[signal.Signals, Any] = {}
    for signum in signals:
        previous = signal.getsignal(signum)
        previous_handlers[signum] = previous
        signal.signal(signum, _recording_handler(signum, previous, recorded))

    def restore() -> None:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)

    return restore


def _recording_handler(signum: signal.Signals, previous: Any, recorded: list[int]) -> Callable[[int, Any], None]:
    def handler(received: int, frame: Any) -> None:
        if recorded[0] < SIGNAL_EVENT_LIMIT:
            recorded[0] += 1
            record_instance_event("signal_received", signal=signal.Signals(received).name)
        if callable(previous):
            previous(received, frame)
        elif previous != signal.SIG_IGN:
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)

    return handler


@dataclasses.dataclass(frozen=True)
class InstanceReference:
    """中断turnが参照する起動単位。`server_instance`が`None`の参照は起動単位を記録していない旧記録を表す。"""

    server_instance: str | None
    turn_seq: int


def diagnostics_reference(reference: InstanceReference, *, state_root: pathlib.Path | None = None) -> dict[str, Any]:
    """中断応答へ添える診断の参照を、診断ファイルを読んだ時点の状態で返す。"""
    result: dict[str, Any] = {"server_instance": reference.server_instance, "turn_seq": reference.turn_seq}
    if reference.server_instance is None:
        return {
            **result,
            "status": "not_collected",
            "path": None,
            "next_action": "この中断は終了診断の記録を始める前の記録であり、終了の経過は共通ログ`agents-server.log`だけが持つ",
        }
    return _evaluated_reference(result, instance_path(reference.server_instance, state_root))


def refresh_diagnostics_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    """保存済みの参照の`status`と`next_action`を、診断ファイルを読み直した結果へ更新する。"""
    path = value.get("path")
    if not isinstance(path, str) or not pathlib.Path(path).is_absolute():
        return dict(value)
    base = {key: value[key] for key in ("server_instance", "turn_seq") if key in value}
    return _evaluated_reference(base, pathlib.Path(path))


def _evaluated_reference(result: dict[str, Any], path: pathlib.Path) -> dict[str, Any]:
    result = {**result, "path": str(path)}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {
            **result,
            "status": "missing",
            "next_action": "診断ファイルが削除されている。終了の経過は共通ログ`agents-server.log`の同じ時刻の記録で確かめる",
        }
    except (OSError, UnicodeError) as exc:
        return {**result, "status": "unreadable", "next_action": f"診断ファイルを読めない原因（{exc}）を解消して再照会する"}
    events = set()
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(line)
            if isinstance(value, dict) and isinstance(value.get("event"), str):
                events.add(value["event"])
    read_action = f"`atk read-file -- {path}`で終了診断を読む"
    if events & TERMINATION_EVENTS:
        return {**result, "status": "collected", "next_action": read_action}
    return {
        **result,
        "status": "incomplete",
        "next_action": read_action
        + "。終了を示す記録が無く、サーバーと起動ランチャーが記録できずに終了したため原因を判断できない",
    }


def sweep_instance_diagnostics(
    *,
    state_root: pathlib.Path,
    cutoff: float,
    referenced: set[str],
    max_total_bytes: int = INSTANCE_DIRECTORY_MAX_BYTES,
) -> None:
    """参照元の無い期限切れの診断と、容量上限を超えた分の参照元の無い古い診断を削除する。

    参照元のある診断と、このプロセス自身の起動単位は削除しない。個別の失敗は警告を残して続行する。
    """
    logger = logging.getLogger("agent-toolkit.agents-server.status-file")
    directory = instance_directory(state_root)
    current = _CURRENT_INSTANCE.instance_id if _CURRENT_INSTANCE is not None else None
    keep = referenced | ({current} if current is not None else set())
    entries: list[tuple[float, int, pathlib.Path]] = []
    try:
        paths = tuple(directory.glob("*.jsonl"))
    except OSError:
        return
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.stem not in keep and stat.st_mtime < cutoff:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning(
                    _warning_text(
                        "期限切れの終了診断を回収できませんでした: path=%s error=%s",
                        "古い診断が残るだけで、サーバーの処理は続く",
                        "対象パスの削除権限を確認し、不要なら手動で削除してください。",
                    ),
                    path,
                    exc,
                )
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _mtime, size, _path in entries)
    for _mtime, size, path in sorted(entries, key=lambda item: item[0]):
        if total <= max_total_bytes:
            return
        if path.stem in keep:
            continue
        try:
            path.unlink()
            total -= size
        except OSError as exc:
            logger.warning(
                _warning_text(
                    "容量上限を超えた終了診断を回収できませんでした: path=%s error=%s",
                    "診断ディレクトリが上限を超えたままになるだけで、サーバーの処理は続く",
                    "対象パスの削除権限を確認し、不要なら手動で削除してください。",
                ),
                path,
                exc,
            )
    if total > max_total_bytes:
        logger.warning(
            _warning_text(
                "参照元のある終了診断だけで容量上限を超えています: directory=%s bytes=%d limit=%d",
                "参照元が残る間は回収されず、診断ディレクトリが上限を超えたままになるだけで、サーバーの処理は続く",
                "不要な診断ファイルを手動で削除するか、古い中断sessionの登録簿を回収してください。",
            ),
            directory,
            total,
            max_total_bytes,
        )
