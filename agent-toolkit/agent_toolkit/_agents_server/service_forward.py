"""MCPのstdioを同一アカウントの管理サービスへ中継する軽量な起動側。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

from agent_toolkit._agents_server import service_rpc, service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import file_lock, plugin_environment, runtime_environment, windows_job
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.diagnostic_redaction import read_diagnostic_tail
from agent_toolkit._common.next_action import report, with_next_action


def _add_service_diagnostic(error: Exception, directory: pathlib.Path, generation: str) -> None:
    """起動・ready・接続の失敗に、同じ世代の診断末尾を一度だけ添える。"""
    path = directory / f"{generation}.stderr.log"
    label = f"管理サービスの診断: {path}"
    if any(note.startswith(label) for note in getattr(error, "__notes__", ())):
        return
    try:
        with path.open("rb") as stream:
            excerpt = read_diagnostic_tail(stream, limit=65536)
        limit = "（末尾65536バイト以内。先頭を省略）" if excerpt.omitted else ""
        if excerpt.text:
            detail = excerpt.text
        elif excerpt.source_bytes:
            detail = (
                "転載できる行がありません（途中から始まる行と秘匿値を除いたため）。ログ全体は上記のパスで確認してください。"
            )
        else:
            detail = "診断本文は空です。"
        error.add_note(f"{label}{limit}\n{detail}")
    except OSError as failure:
        error.add_note(f"{label}（取得失敗: {failure}）")


# 起動した世代を受付先として公開せず、候補のまま接続する指定。update-dotfilesの起動確認が、
# 確認の終了後も候補が生存することを確かめてから`publish_service`で公開するために使う。
DEFER_PUBLISH_ENV = "AGENT_TOOLKIT_DEFER_SERVICE_PUBLISH"

INHERITED_JOB_WARNING = with_next_action(
    "起動元のWindows Jobは離脱を許可していません。起動元のJobの制限を継承して管理サービスを起動しました。"
    "そのJob終了後のサービス生存は保証しません。",
    next_action=(
        "Claude/Codexを起動した端末・IDE・サービスのJob離脱可否を、その起動元の管理者へ確認してください。"
        "確認できない場合は、起動元とこの診断を添えてagent-toolkitの管理者へ問い合わせてください。"
    ),
)

# 応答の無い要求がこの秒数を超えたら、接続先世代の生存を短い照会で確かめる。
# 正当に長いtool処理は照会に応答するため打ち切らず、停止・凍結した世代だけを配送未確定として返す。
_STALL_SECONDS = 10.0
_PROBE_SECONDS = 10.0


# 管理領域の直下の`*.json`は世代ごとのready記録と世代の索引に限り、識別の索引は下位のディレクトリへ置く。
_IDENTITY_INDEX = pathlib.PurePath("code-identity", "index.json")
_IDENTITY_INDEX_VERSION = 1
_IDENTITY_INDEX_ENTRIES = 32
_RECENT_NS = 2_000_000_000
"""これより新しい更新時刻のモジュールがある間は、世代を索引へ保存しない（ナノ秒）。"""


def code_identity(root: pathlib.Path, directory: pathlib.Path) -> tuple[str, str]:
    """同じ配布版を合流し、開発checkoutの同版編集も別世代として識別する。

    世代は版と、試験を除く全モジュールの相対パス・内容から決める。旧版の転送側も受付先のrootへ同じ計算をするため、
    この定義は変えない。全モジュール（約600件・約7MB）の読込とハッシュは、各モジュールの識別（inode・更新時刻・大きさ）が
    前回と同じ間、`directory`の索引に保存した世代で省く。書き換えから間もないモジュールは、更新時刻の刻みの中で
    識別が変わらずに内容だけ変わり得るため、その間は保存しない。
    """
    version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    modules = sorted(_modules(str(root / "agent_toolkit")))
    fingerprint = hashlib.sha256(json.dumps([str(root), version, modules]).encode("utf-8")).hexdigest()
    index = directory / _IDENTITY_INDEX
    saved = _saved_identities(index)
    if (generation := saved.get(fingerprint)) is not None:
        return generation, version
    # 定義の連結順はPathの順序であり、文字列の順序と異なり得る（区切り文字の前後で順序が変わる）。
    generation = _code_digest(root, version, sorted(pathlib.Path(path) for path, _identity in modules))
    recent = time.time_ns() - _RECENT_NS
    if all(identity[1] < recent for _path, identity in modules):
        saved.pop(fingerprint, None)
        saved[fingerprint] = generation
        entries = dict(list(saved.items())[-_IDENTITY_INDEX_ENTRIES:])
        # 索引は再計算できる値であり、書けない場合も世代は変わらない。
        with contextlib.suppress(OSError):
            atomic_write(index, json.dumps({"version": _IDENTITY_INDEX_VERSION, "entries": entries}))
    return generation, version


def _modules(directory: str) -> Iterator[tuple[str, list[int]]]:
    """`Path(directory).rglob("*.py")`と同じ範囲の試験以外のモジュールを、識別（inode・更新時刻・大きさ）とともに返す。"""
    with os.scandir(directory) as listed:
        entries = list(listed)
    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            yield from _modules(entry.path)
        elif os.path.normcase(entry.name).endswith(".py") and not entry.name.endswith("_test.py"):
            stat = entry.stat()
            yield entry.path, [stat.st_ino, stat.st_mtime_ns, stat.st_size]


def _code_digest(root: pathlib.Path, version: str, paths: list[pathlib.Path]) -> str:
    digest = hashlib.sha256(version.encode("utf-8"))
    for path in paths:
        name = str(path.relative_to(root)).encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def _saved_identities(path: pathlib.Path) -> dict[str, str]:
    """保存した世代を識別の指紋ごとに返す。読めない索引と形の合わない索引は、保存されていなかったものとして扱う。"""
    try:
        payload = json.loads(path.read_bytes())
    except OSError, ValueError:
        return {}
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _IDENTITY_INDEX_VERSION
        or not isinstance(payload.get("entries"), dict)
    ):
        return {}
    return {key: value for key, value in payload["entries"].items() if isinstance(value, str)}


def _launch_service(
    command: list[str], attempts: list[dict[str, Any]], *, stderr: Any, cwd: pathlib.Path, env: dict[str, str]
) -> subprocess.Popen[Any]:
    """起動元からの分離を強い指定から順に試し、起動元に拒否された指定だけを次の指定へ替えて管理サービスを起動する。

    離脱を許さないJobは、離脱を指定した起動をアクセス拒否で拒む。実行ファイルへの権限不足も同じ拒否になるため、
    指定を替えた起動が成功した場合だけ、Jobによる拒否として診断へ残す。
    """
    refused = False
    for index, flags in enumerate(attempts):
        try:
            process = subprocess.Popen(  # pylint: disable=consider-using-with
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr, cwd=cwd, env=env, **flags
            )
        except OSError as error:
            if isinstance(error, PermissionError) and index + 1 < len(attempts):
                refused = True
                continue
            error.add_note(f"管理サービス起動: executable={command[0]} cwd={cwd} flags={flags}")
            raise
        if refused:
            print(INHERITED_JOB_WARNING, file=sys.stderr)
            stderr.write((INHERITED_JOB_WARNING + "\n").encode("utf-8"))
            stderr.flush()
        return process
    raise ValueError("管理サービスの起動指定がありません")


def ensure_service(
    caller: runtime_environment.CallerContext, root: pathlib.Path, *, python: pathlib.Path | None = None
) -> dict[str, Any]:
    """起動を世代ごとに合流し、MCPの通常導入経路だけでreadyへ到達させる。

    呼出元の環境が`DEFER_PUBLISH_ENV`を指定していれば、readyの世代を受付先として公開せずに返す。
    """
    publish = caller.environment.get(DEFER_PUBLISH_ENV) != "1"
    directory = service_transport.namespace(caller)
    generation, version = code_identity(root, directory)
    registry = GenerationRegistry(directory)
    target = registry.reserve(generation, root, version)
    generation = target["generation"]
    root = pathlib.Path(target["root"])
    ready = directory / f"{generation}.json"
    with (directory / "startup.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            current = registry.current()
            if current is not None and current["epoch"] > target["epoch"]:
                return current
            exited = None
            if ready.exists():
                record = json.loads(ready.read_text(encoding="utf-8"))
                if service_transport.process_alive(record["process"]):
                    return service_rpc.request_sync(record, "publish", {}) if publish else record
                exited = record["process"]
            python = plugin_environment.prepared_python(root, caller.environment) or python or pathlib.Path(sys.executable)
            env = {
                key: value
                for key, value in caller.environment.items()
                if key
                in {
                    "PATH",
                    "PATHEXT",
                    "HOME",
                    "USERPROFILE",
                    "LOCALAPPDATA",
                    "APPDATA",
                    "SYSTEMROOT",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "XDG_STATE_HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_DATA_HOME",
                    plugin_environment.RUNTIME_DATA_ENV,
                    "CLAUDE_PLUGIN_DATA",
                    "PLUGIN_DATA",
                }
            }
            command = [
                str(python or sys.executable),
                str(root / "scripts" / "launch.py"),
                "--project",
                str(root),
                "--agents-service",
                "agent_toolkit/_agents_server/service.py",
                str(directory),
                generation,
            ]
            attempts: list[dict[str, Any]] = [{"start_new_session": True}]
            with contextlib.ExitStack() as startup:
                job = None
                if os.name == "nt":
                    # 成功後のserviceは転送側から独立する。準備失敗時だけ全所属を回収する。
                    job = startup.enter_context(windows_job.WindowsJob(kill_on_close=False))
                    detached = windows_job.HIDDEN_DETACHED_CREATIONFLAGS | windows_job.CREATE_SUSPENDED
                    # 起動元のJobからの離脱を指定する。子を自動で離脱させるJob（venvのpython.exeが作るJobなど）の中でも、
                    # 指定の無い子は外側のJob（SSHのセッションなど）に残り、そのJobの終了とともに止まる。
                    # 離脱を許さないJobの中では離脱を指定した起動が拒否されるため、所属を継承して起動する。
                    attempts = [
                        {"creationflags": detached | windows_job.CREATE_BREAKAWAY_FROM_JOB},
                        {"creationflags": detached},
                    ]
                stderr = startup.enter_context((directory / f"{generation}.stderr.log").open("wb"))
                # サービスの寿命を転送側へ結び付けない。終了診断はサービスの起動単位が保存する。
                process = _launch_service(command, attempts, stderr=stderr, cwd=root, env=env)
                try:
                    if job is not None:
                        native_process: Any = process
                        # Windows版Popenが生成時に保持する元handleを使う。POSIX側の型推論には存在しない。
                        job.assign_suspended(
                            int(native_process._handle),  # pylint: disable=no-member,protected-access
                            process.pid,
                        )
                    deadline = time.monotonic() + 30.0
                    while True:
                        record = json.loads(ready.read_text(encoding="utf-8")) if ready.exists() else None
                        # サービスは受付を始めてから自身の記録を置く。その間に前回の記録で接続すると、
                        # 新しいサービスへ前回の起動単位を期待して認証が失敗する。
                        if record is not None and record["process"] != exited:
                            try:
                                # Windowsのvenv launcherのPIDと実行本体のPIDは別になる。
                                # readyは自己申告だけで受理せず、OSのpeer identityで確かめる。
                                with service_transport.connect(record["endpoint"], record["process"]) as peer:
                                    service_transport.send(peer, {"kind": "rpc", "operation": "ping", "arguments": {}})
                                    if not peer.poll(1):
                                        raise TimeoutError("管理サービスのpingが未完了")
                                    response = service_transport.receive(peer)
                                if response != {"result": {"generation": generation}}:
                                    raise ValueError("管理サービスの世代が不正")
                            except FileNotFoundError, ConnectionRefusedError, TimeoutError:
                                # 受付を始めた直後のサービスは最初の応答が遅れることがある。readyの期限まで確かめ直す。
                                pass
                            else:
                                published = service_rpc.request_sync(record, "publish", {}) if publish else record
                                # stdio終了から独立した子を保持し、退出した旧supervisorは親の生存中にもreapする。
                                threading.Thread(target=process.wait, name="agents-service-reaper", daemon=True).start()
                                return published
                        if process.poll() is not None:
                            raise RuntimeError(f"管理サービスの起動に失敗: exit={process.returncode}")
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"管理サービスのreadyを確認できません。診断: {stderr.name}")
                        time.sleep(0.05)
                except BaseException:
                    if job is not None:
                        try:
                            job.terminate()
                        finally:
                            # Job所属の前に失敗した停止中のrootも、保持している元handleで回収する。
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=5)
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    raise
        except Exception as error:
            _add_service_diagnostic(error, directory, generation)
            raise
        finally:
            file_lock.release_lock(handle)


def publish_service(caller: runtime_environment.CallerContext, root: pathlib.Path, *, timeout: float) -> dict[str, Any]:
    """公開を保留して起動した候補の世代が生存していれば受付先として公開し、公開後の受付先を返す。

    候補の起動と接続確認に使った子プロセスの終了後に呼び出す。候補が終了していれば公開せず、受付先を変更しない。
    公開の要求の途中か直後に候補が終了していれば、生存する世代へ受付を戻してから失敗を送出する。
    """
    directory = service_transport.namespace(caller)
    generation, version = code_identity(root, directory)
    registry = GenerationRegistry(directory)
    generation = registry.reserve(generation, root, version)["generation"]
    try:
        try:
            record = service_rpc.discovery(directory, generation)
        except FileNotFoundError as error:
            raise RuntimeError(f"候補の世代が起動していないため公開しません: generation={generation}") from error
        if not service_transport.process_alive(record["process"]):
            raise RuntimeError(
                f"候補の世代が起動確認の終了後に生存していないため公開しません: "
                f"generation={generation} process={record['process']}"
            )
        try:
            accepted = service_rpc.request_sync(record, "publish", {}, timeout=timeout)
        except Exception:
            # 候補が受付先を公開した後、応答を返す前に終了すると、受付先は終了した世代を指す。生存する世代へ戻す。
            registry.restore()
            raise
        if not service_transport.process_alive(accepted["process"]):
            restored = registry.restore()
            raise RuntimeError(
                f"公開した受付先の世代が終了しました: generation={accepted['generation']} process={accepted['process']} "
                f"現在の受付先={None if restored is None else restored['generation']}"
            )
        return accepted
    except Exception as error:
        _add_service_diagnostic(error, directory, generation)
        raise


def _probe(target: dict) -> str | None:
    """接続先世代が短時間で応答するかを別接続で確かめ、応答しなければ理由を返す。"""
    outcome: list[str | None] = []

    def run() -> None:
        try:
            service_rpc.request_sync(target, "ping", {}, timeout=_PROBE_SECONDS)
        except RuntimeError, ValueError:
            # 応答は届いた（pingを持たない旧世代のエラー応答を含む）。生存している。
            outcome.append(None)
        except Exception as error:
            outcome.append(f"{type(error).__name__}: {error}")
        else:
            outcome.append(None)

    # 接続の確立自体が止まるホストでも監視を止めないよう、別threadで上限付きに待つ。
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(_PROBE_SECONDS + 5)
    return outcome[0] if outcome else f"TimeoutError: {_PROBE_SECONDS + 5}秒以内に接続できません"


def _stalled_response(identifier: str | int, method: str, target: dict, error: str) -> dict:
    message = (
        f"{method}の応答が{_STALL_SECONDS:g}秒以上無く、接続先の管理サービス（generation={target.get('generation')}; "
        f"process={target.get('process')}）が生存確認に応答しません（{error}）。配送と実行は未確定です。"
    )
    next_action = (
        "同じ要求を再送しない。上記processの接続先世代が停止・無応答かをOSのプロセス一覧"
        "（Linuxは`ps -o pid,stat,etime -p <pid>`）で確かめ、応答が再開すれば`atk agents show <session_id>`で"
        "対象sessionの状態を確認する。応答しないままなら当該プロセスを処置する。"
        "このプロセスは自身のMCP接続の中継先である。"
        "旧世代（`list`の`server_generations`で`current: false`）なら、処置後は現行世代へ自動で接続し直す。"
        "現行世代なら処置するとこのMCP接続も終了し、ホストでの再接続（Claude Codeでは`/mcp`）が必要になる"
    )
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": -32000, "message": with_next_action(message, next_action), "data": {"next_action": next_action}},
    }


class _Forwarder:
    """送信を静止してから接続を移し、未確定の要求は再送しない。"""

    def __init__(self, caller: runtime_environment.CallerContext) -> None:
        self.caller = caller
        self.condition = threading.Condition()
        self.peer: Any = None
        self.pending: dict[str | int, str] = {}
        self.initialize: dict | None = None
        self.initialized = False
        self.input_closed = False
        self.closed = False
        self.failure: Exception | None = None
        self.progress = 0
        self.target: dict | None = None
        self.sent_at: dict[str | int, float] = {}
        self.abandoned: set[str | int] = set()
        self._output_lock = threading.Lock()
        self._stdin = os.dup(0)
        self.reader = threading.Thread(target=self._read_input, daemon=True)
        self.watchdog = threading.Thread(target=self._watch, daemon=True)

    def output(self, message: dict) -> None:
        # 受信threadと監視threadが同じstdoutへ書くため、1行単位で直列化する。
        with self._output_lock:
            sys.stdout.buffer.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()

    def _watch(self) -> None:
        """接続先世代が停止・凍結した間の要求を、無期限に待たせず配送未確定として返す。

        2026-11-17、接続先の旧世代が停止した間のshow・killがMCP応答を返さず、呼出側が待ち続けた。
        """
        while True:
            with self.condition:
                self.condition.wait(_STALL_SECONDS / 2)
                if self.closed or self.input_closed:
                    return
                now = time.monotonic()
                stale = [identifier for identifier, sent in self.sent_at.items() if now - sent >= _STALL_SECONDS]
                target = self.target
            if not stale or target is None:
                continue
            error = _probe(target)
            if error is None:
                continue
            with self.condition:
                abandoned = [(identifier, self.pending.pop(identifier)) for identifier in stale if identifier in self.pending]
                for identifier, _method in abandoned:
                    self.sent_at.pop(identifier, None)
                    self.abandoned.add(identifier)
            for identifier, method in abandoned:
                self.output(_stalled_response(identifier, method, target, error))

    def _read_input(self) -> None:
        buffered = b""
        try:
            while chunk := os.read(self._stdin, 65536):
                buffered += chunk
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("MCPメッセージはJSON objectで指定する")
                    with self.condition:
                        self.condition.wait_for(lambda: self.peer is not None or self.closed)
                        if self.closed:
                            return
                        method = message.get("method")
                        if method == "initialize":
                            self.initialize = message
                        elif method == "notifications/initialized":
                            self.initialized = True
                        if isinstance(method, str) and "id" in message:
                            self.pending[message["id"]] = method
                            self.sent_at[message["id"]] = time.monotonic()
                        peer = self.peer
                        assert peer is not None  # wait_forで接続済みを確かめた
                        try:
                            service_transport.send(peer, message)
                        except OSError:
                            # 接続先の世代が終了した。入力の終了とは扱わず、送れなかった要求は未確定として
                            # 接続の切替後に応答し、以後の入力は切替先へ送る。
                            self.peer = None
            if buffered.strip():
                raise ValueError("MCPの最終メッセージが改行で終了していません")
        except Exception as error:
            self.failure = error
        finally:
            with self.condition:
                self.input_closed = True
                peer = self.peer
                self.peer = None
                self.condition.notify_all()
            if peer is not None:
                service_transport.disconnect(peer)

    def attach(self, peer: Any) -> bool:
        service_transport.send(
            peer,
            {
                "environment": dict(self.caller.environment),
                "cwd": str(self.caller.cwd),
                "connection_id": self.caller.connection_id,
                "parent_settings": self.caller.parent_settings,
                "forwarder_protocol": 1,
            },
        )
        if not peer.poll(30) or service_transport.receive(peer) != {"ready": True, "protocol": 1}:
            raise RuntimeError("管理サービスの接続準備が不正です")
        reinitialized = self.initialize is not None
        if self.initialize is not None:
            # initializeだけを新しいwireへ再構成する。tools/callやqueryは再送しない。
            message = {**self.initialize, "id": "reconnect-" + uuid.uuid4().hex}
            service_transport.send(peer, message)
            if not peer.poll(30):
                raise TimeoutError("接続切替先のMCP初期化が未完了です")
            response = service_transport.receive(peer)
            if response.get("id") != message["id"] or "error" in response:
                raise RuntimeError("接続切替先のMCP初期化に失敗しました")
            if self.initialized:
                service_transport.send(peer, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        with self.condition:
            if self.input_closed:
                return False
            self.peer = peer
            self.condition.notify_all()
        if reinitialized:
            self.output({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        return True

    def receive(self, peer: Any) -> None:
        while True:
            message = service_transport.receive(peer)
            if message.get("kind") == "redirect":
                with self.condition:
                    # ACKより後の入力は新しいwireがreadyになるまで未送信のまま保持する。
                    self.peer = None
                    service_transport.send(peer, {"kind": "redirect_ack"})
                continue
            if "method" not in message and "id" in message:
                with self.condition:
                    self.pending.pop(message["id"], None)
                    self.sent_at.pop(message["id"], None)
                    self.progress += 1
                    if message["id"] in self.abandoned:
                        # 配送未確定として応答済みの要求。同じidへ2つ目の応答を返さない。
                        self.abandoned.discard(message["id"])
                        continue
            self.output(message)

    def detach(self, peer: Any) -> None:
        service_transport.disconnect(peer)
        with self.condition:
            self.peer = None

    def close(self) -> None:
        with self.condition:
            if self.closed:
                return
            self.closed = True
            peer = self.peer
            self.peer = None
            self.condition.notify_all()
        if peer is not None:
            service_transport.disconnect(peer)
        os.close(self._stdin)
        if self.input_closed:
            self.reader.join(timeout=5)


def forward(
    *,
    root: pathlib.Path | None = None,
    python: pathlib.Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    """schemaを定義せず、現行受付への切替と認証済みJSONの中継だけを行う。"""
    caller = runtime_environment.CallerContext(
        dict(os.environ if environment is None else environment),
        pathlib.Path.cwd(),
        connection_id="mcp-" + uuid.uuid4().hex,
        parent_settings=_parent_settings(),
    )
    root = root or pathlib.Path(__file__).resolve().parents[2]
    bridge = _Forwarder(caller)
    registry = GenerationRegistry(service_transport.namespace(caller))
    previous_reconnect = None
    previous_progress = -1
    received_signal = 0
    target = None

    def stop_forwarder(signum: int, _frame: Any) -> None:
        nonlocal received_signal
        received_signal = signum
        raise KeyboardInterrupt

    signals = (signal.SIGINT,) if os.name == "nt" else (signal.SIGINT, signal.SIGTERM)
    previous_handlers = {signum: signal.signal(signum, stop_forwarder) for signum in signals}
    try:
        target = ensure_service(caller, root, python=python)
        bridge.reader.start()
        bridge.watchdog.start()
        while True:
            with bridge.condition:
                bridge.target = target
            try:
                with service_transport.connect(target["endpoint"], target["process"]) as peer:
                    try:
                        if not bridge.attach(peer):
                            return 0 if bridge.failure is None else 1
                        with contextlib.suppress(EOFError, OSError):
                            bridge.receive(peer)
                    finally:
                        bridge.detach(peer)
            except (EOFError, ConnectionError, FileNotFoundError) as error:
                # publishと初回readyの間にも旧世代は正常退出する。送信済み操作の判定は共通処理へ渡す。
                current = registry.current()
                if current is None or current["process"] == target["process"]:
                    error.add_note(
                        f"接続準備失敗時の世代: target={target['generation']} process={target['process']} "
                        f"current={None if current is None else current['generation']}"
                    )
                    raise
            if bridge.input_closed:
                if bridge.failure is not None:
                    raise bridge.failure
                return 0
            # 監視threadが同じidを配送未確定として返すのと競合しないよう、取り出しと消去をロック下で行う。
            with bridge.condition:
                unresolved = tuple(bridge.pending.items())
                bridge.pending.clear()
                bridge.sent_at.clear()
                bridge.abandoned.clear()
            # 未確定の要求は再送せずエラーで返し、接続自体は現行世代へ切り替えて続ける。
            # ここで終了するとMCP接続が失われ、委譲元が以後の照会・回収をできなくなる（2026-11-17）。
            if unresolved:
                for identifier, method in unresolved:
                    bridge.output(
                        {
                            "jsonrpc": "2.0",
                            "id": identifier,
                            "error": {
                                "code": -32000,
                                "message": (
                                    f"{method}の成否を確定できません。再送せず、showと既存記録で状態を確認してください。"
                                ),
                            },
                        }
                    )
            current = registry.current()
            if current is None or (previous_reconnect == current["process"] and previous_progress == bridge.progress):
                raise RuntimeError("管理サービスの接続が応答の無いまま繰り返し終了しました")
            previous_reconnect = current["process"]
            previous_progress = bridge.progress
            target = ensure_service(caller, pathlib.Path(current.get("root", root)), python=python)
    except KeyboardInterrupt:
        return 128 + (received_signal or signal.SIGINT)
    except (EOFError, OSError, ValueError, RuntimeError) as error:
        if target is not None:
            _add_service_diagnostic(error, registry.directory, target["generation"])
        traceback.print_exc()
        report(
            str(error),
            next_action="状態と起動診断を確認し、未確定の操作は再送せず既存登録簿・物理記録で確認してください。",
        )
        return 1
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        bridge.close()


def _parent_settings() -> str | None:
    """転送側の親CLIのsettings層だけを取得し、共有serviceの親から継承しない。"""
    try:
        arguments = pathlib.Path(f"/proc/{os.getppid()}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    settings = None
    for index, argument in enumerate(arguments):
        if argument.startswith(b"--settings="):
            settings = os.fsdecode(argument.removeprefix(b"--settings=")) or None
        elif argument == b"--settings":
            settings = os.fsdecode(arguments[index + 1]) if index + 1 < len(arguments) else None
    return settings
