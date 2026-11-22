# /// script
# requires-python = ">=3.15,<4.0"
# dependencies = [
#     "argcomplete>=3.6.3",
#     "claude-agent-sdk>=0.2.144,<0.3",
#     "filelock>=3.30",
#     "markdown-it-py[linkify]>=4.0.0",
#     "mcp>=2.0.0,<3",
#     "mdit-py-plugins>=0.6",
#     "platformdirs>=4.0",
#     "psutil>=6",
#     "pydantic>=2",
#     "pyfltr>=3.30.1",
#     "pygments",
#     "pytilpack[quart]>=1.47.0",
#     "pyyaml",
#     # 再帰inotify監視がKeyErrorで停止する版を除外する（PYTHONDEVMODE=1のbrowser-e2eで再現）。
#     "watchdog>=6.0.0,!=7.0.0",
# ]
#
# [tool.uv]
# # サプライチェーン攻撃対策: 公開直後の新バージョンは1日待ってから取り込む
# exclude-newer = "1 day"
# exclude-newer-package = { pyfltr = false, pytilpack = false }
# ///
"""標準ライブラリだけで起動し、配布root外の依存環境から指定スクリプトを実行する。

冒頭のインラインメタデータ（PEP 723）がagent-toolkitの実行依存の宣言であり、隣の`launch.py.lock`がその固定内容である。
`--prepare`はこの宣言を共有依存環境へ同期する。配布rootにはuvのプロジェクトを置かず、仮想環境を作らない。

agents_serverのMCPサーバーだけは子プロセスとして起動して終了を待ち、終了状態と標準エラー末尾を
サーバーが報告した起動単位の終了診断へ追記する。サーバー自身が記録できない終了も観測するためである。
通常hookは起動準備と本体を有限時間で監視し、終了時に同じ所有回収を使う。
"""

# stdlib bootstrapを保ち、依存環境内の実装とWindows専用APIは該当分岐だけで読み込む。
# pylint: disable=import-outside-toplevel

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, BinaryIO
lazy import ctypes
lazy import ctypes.wintypes
lazy import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
lazy from agent_toolkit._agents_server import logging_config  # noqa: E402  # pylint: disable=wrong-import-position,import-error
lazy from agent_toolkit._common import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    diagnostic_redaction,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    dotfiles_mode,
    environment_usage,
    inherited_venv,
    next_action,
    plugin_environment,
)

_AGENTS_SERVER_TARGET = Path("agent_toolkit") / "agents_server_mcp.py"
_AGENTS_SERVICE_TARGET = Path("agent_toolkit") / "_agents_server" / "service.py"
_STDERR_TAIL_BYTES = 16 * 1024
_FORWARDED_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP")
_STOP_TIMEOUT = 5.0
_PREPARE_TIMEOUT = 600.0
_PARENT_PID = os.getppid()
_SERVER_SHUTDOWN_TIMEOUT = 30.0
_HOOK_TIMEOUT = 60.0


def main() -> int:
    """CLI・hook・MCP・スキルスクリプトのargvと終了状態をuvへそのまま渡す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1], help="配布rootの絶対パス")
    parser.add_argument("--persist-claude-env", action="store_true", help="SessionStartのBash環境ファイルへCLIのPATHを追記する")
    parser.add_argument("--prepare", action="store_true", help="共有依存環境を準備して終了する")
    parser.add_argument("--agents-service", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--prepare-if-missing",
        action="store_true",
        help="共有依存環境が未準備なら準備してから起動する。呼び出し元が結果を待つCLIの起動部分だけが指定する",
    )
    parser.add_argument("target", type=Path, help="root内のスクリプトの相対パスまたは絶対パス")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="対象スクリプトへそのまま渡す引数")
    options = parser.parse_args()
    root = options.project.resolve()
    target = (root / options.target).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        parser.error(
            next_action.with_next_action(
                "targetは指定された配布root内の実在するスクリプトである必要がある",
                "配布root内のスクリプトのパスをtargetへ指定して再実行してください。",
            )
        )
    try:
        if options.persist_claude_env:
            plugin_environment.persist_claude_environment(root, os.environ)
        caller_environment = dict(os.environ)
        path = caller_environment.get("PATH", "").split(os.pathsep)
        if path and Path(path[0]).resolve() == Path(sys.executable).resolve().parent:
            caller_environment["PATH"] = os.pathsep.join(path[1:])
        selected = plugin_environment.project_environment(root, caller_environment)
        python = plugin_environment.prepared_environment_python(selected)
    except OSError as exc:
        next_action.report(
            f"起動環境を準備できないため、agent-toolkitを起動できません: {exc}",
            next_action="配布rootの読取権限と、CLAUDE_ENV_FILEが指定されていればその書込権限を確かめてください。",
        )
        return 1
    uv = shutil.which("uv")
    if uv is None:
        next_action.report(
            "uvが見つからないため、agent-toolkitを起動できません。",
            next_action="uvを導入し、同じコマンドを再実行してください。",
        )
        return 127
    if target == root / "agent_toolkit" / "atk.py":
        dispatched = dotfiles_mode.dispatch_arguments(options.arguments)
        if dispatched is not None:
            return dispatched
    if options.prepare:
        return _prepare(root, uv, caller_environment)
    if python is None and options.prepare_if_missing:
        # dotfilesの更新直後は依存の固定内容が変わり、後段の準備まで共有依存環境が無い。
        # CLIは呼び出し元が結果を待つため、準備してから起動する。hook・MCPと関数hookから起動する`atk.py`は
        # ホストの短い時間上限で起動されるため待たせず、CLIの起動部分だけがこの指定を渡す。
        print(
            "agent-toolkitの共有依存環境を準備してから起動します（通常は数十秒。並行する準備があれば完了を待ちます）。",
            file=sys.stderr,
            flush=True,
        )
        prepared = _prepare(root, uv, caller_environment)
        if prepared != 0:
            return prepared
        python = plugin_environment.prepared_environment_python(selected)
    if python is None:
        reason = f"共有依存環境が未準備のため、agent-toolkitを起動できません。Python: {plugin_environment.base_python()}"
        action = "`update-dotfiles`またはagent-toolkitの公開インストーラーを再実行して依存環境を準備してください。"
        next_action.report(reason, next_action=action)
        if target == root / "agent_toolkit/hook.py":
            _report_unprepared_hook(f"{reason}。共有環境の指定と準備状態、直近の環境変更を確認してください。{action}")
        return 1
    # 共有の管理サービスを持たない旧版のroot（update-dotfilesの起動確認が新しいlauncherで確かめる、
    # ホストがまだ参照している旧版のplugin cacheなど）は、旧版のlauncherが`--agents-service`を受け付けないため、
    # 管理サービスへ転送せずにMCPサーバーを直接起動する。
    if target == root / _AGENTS_SERVER_TARGET and not options.arguments and (root / _AGENTS_SERVICE_TARGET).is_file():
        # 転送側はstdlibだけで動き、接続の寿命では依存環境の使用ロックを保持しない。
        from agent_toolkit._agents_server.service_forward import forward

        return forward(root=root, python=python, environment=caller_environment)
    try:
        with environment_usage.lock(selected) as usage_lock:
            if plugin_environment.project_environment(root, caller_environment) != selected:
                raise OSError("共有環境を選択した後に依存の固定内容が変わったため、起動を中止しました")
            # 準備確認と使用開始の間に回収されていないことを、回収と同じ排他下で確かめる。
            python = plugin_environment.prepared_environment_python(selected)
            if python is None:
                raise FileNotFoundError("共有依存環境が回収されたため、update-dotfilesで再準備してください")
            arguments = [str(python), str(target), *options.arguments]
            environment = plugin_environment.direct_environment(root, caller_environment, selected=selected)
            if target == root / "agent_toolkit/hook.py":
                # 親の強制終了時も、POSIXの子が依存環境を使う間は回収を防ぐ。
                pass_fds = () if os.name == "nt" else (usage_lock.fileno(),)
                return _run_hook(arguments, environment, pass_fds=pass_fds)
            if options.agents_service:
                if target != root / _AGENTS_SERVICE_TARGET:
                    parser.error("--agents-serviceは管理サービスの内部起動だけに使用する")
                from agent_toolkit._agents_server import plugin_root

                stable = plugin_root.SERVER_PLUGIN_ROOT
                if stable.resolve() == root:
                    stable = plugin_root.resolve_stable_plugin_root(root, independent=True)
                arguments = [str(python), str(stable / _AGENTS_SERVICE_TARGET), *options.arguments]
                environment["PYTHONPATH"] = str(stable)
                # 委譲先へ渡す前の復元で、管理サービスのコード配置を指すPYTHONPATHも外せるよう記録する。
                inherited_venv.record_launch_value(environment, "PYTHONPATH", caller_environment.get("PYTHONPATH"))
                return _run_agents_server(arguments, environment, independent=True)
            if target == root / _AGENTS_SERVER_TARGET:
                return _run_agents_server(arguments, environment)
            if target == root / "agent_toolkit/_common/pyfltr_entry.py" and options.arguments[:1] == ["mcp"]:
                return _run_agents_server(arguments, environment, record_diagnostics=False)
            if os.name == "nt":
                # Windowsでは親が子の終了まで共有ロックと終了状態を保持する。
                return subprocess.run(arguments, env=environment, check=False).returncode
            os.execve(str(python), arguments, environment)
            return 1
    except OSError as exc:
        next_action.report(str(exc), next_action="`update-dotfiles`を再実行して依存環境を準備してください。")
        return 1


def _report_unprepared_hook(message: str) -> None:
    """追加文脈を受理するhookへ、依存環境を必要としない診断を返す。"""
    try:
        payload = json.loads(sys.stdin.buffer.read())
    except ValueError, UnicodeError:
        return
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    if event in ("PreToolUse", "PostToolUse", "UserPromptSubmit"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}, ensure_ascii=False))


def _run_hook(arguments: list[str], environment: dict[str, str], *, pass_fds: tuple[int, ...]) -> int:
    """ホストの入力を保持し、hook本体を所有groupで監視する。"""
    payload = sys.stdin.buffer.read()
    try:
        decoded = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        next_action.report(str(exc), next_action="hookへ有効なJSON入力を渡して再実行してください。")
        return 1
    cwd = decoded.get("cwd") if isinstance(decoded, dict) else None
    if arguments[2:3] == ["antigravity"] and isinstance(decoded, dict):
        workspace = decoded.get("workspacePaths")
        cwd = next((value for value in workspace if isinstance(value, str)), None) if isinstance(workspace, list) else None
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        next_action.report(
            "hookの作業ディレクトリが絶対パスではありません。",
            next_action="ホスト入力のcwdまたはworkspacePathsの実在する絶対パスを確認してください。",
        )
        return 1
    # 元のバイト列を再シリアライズせず渡す。pipeへの書込待ちを監視側へ持ち込まない。
    with tempfile.TemporaryFile() as input_stream:
        input_stream.write(payload)
        input_stream.seek(0)
        received: list[dict[str, str]] = []
        try:
            with _server_lifetime(
                arguments, environment, received, None, stdin=input_stream, cwd=Path(cwd), pass_fds=pass_fds
            ) as process:
                deadline = time.monotonic() + _HOOK_TIMEOUT
                while True:
                    if received and os.name != "nt":
                        # ホストの短い取消猶予の内側で回収する。async hookは親の終了だけでは取り消さない。
                        _stop_server_process(process, signum=signal.SIGKILL, graceful_timeout=0)
                        return 128 + getattr(signal, received[0]["signal"])
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(arguments, _HOOK_TIMEOUT)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        return _relay_server_exit(process.wait(timeout=min(remaining, 0.1)))
        except subprocess.TimeoutExpired:
            next_action.report(
                f"hookが{_HOOK_TIMEOUT:g}秒以内に終了せず、所有する子孫を回収しました。",
                next_action="同じイベントの入力とhookの後始末処理を確認してください。",
            )
            return 124
        except OSError as exc:
            next_action.report(str(exc), next_action="hook入力のcwdと配布rootの実在・実行権限を確認してください。")
            return 1


def _prepare(root: Path, uv: str, environment: dict[str, str]) -> int:
    """依存だけを一時環境へ同期し、完了後に共有環境として公開する。"""
    started = time.monotonic()
    try:
        environment = plugin_environment.launch_environment(root, environment)
        target = Path(environment["UV_PROJECT_ENVIRONMENT"])
        with plugin_environment.prepare_lock(target):
            if plugin_environment.project_environment(root, environment) != target:
                raise OSError("排他の取得前に依存の固定内容が変更されたため、共有環境の準備を中止しました")
            if plugin_environment.prepared_environment_python(target) is not None:
                plugin_environment.record_source(root, target)
                return 0
            remaining = _PREPARE_TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError(f"共有依存環境のprepareが{_PREPARE_TIMEOUT:.0f}秒以内に完了しませんでした: {target}")
            # `--active`で同期先を`VIRTUAL_ENV`の共有環境にし、スクリプト用の環境をuvのキャッシュへ作らない。
            prepare_environment = {**environment, "VIRTUAL_ENV": str(target)}
            # prepareもbootstrapが所有する子として扱い、時間切れではuvの子孫まで回収する。
            # uvは`--script`の設定ファイルを作業ディレクトリから探すため、配布rootで起動して呼出元の設定を読まない。
            with _server_lifetime(
                [
                    uv,
                    "sync",
                    "--script",
                    str(root / plugin_environment.DEPENDENCY_SCRIPT),
                    "--active",
                    "--python",
                    str(plugin_environment.base_python()),
                    "--locked",
                ],
                environment=prepare_environment,
                received=[],
                tail=None,
                cwd=root,
            ) as process:
                returncode = process.wait(timeout=remaining)
            if returncode != 0:
                next_action.report(
                    f"共有依存環境の依存同期が失敗しました（終了コード: {returncode}）。",
                    next_action="`update-dotfiles`またはagent-toolkitの公開インストーラーを再実行してください。",
                )
                return returncode
            if plugin_environment.project_environment(root, environment) != target:
                raise OSError("依存同期中に依存の固定内容が変更されたため、共有環境の公開を中止しました")
            plugin_environment.publish_prepared_environment(root, environment, selected=target)
            return 0
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        next_action.report(
            f"共有依存環境を準備できません: {exc}",
            next_action="`update-dotfiles`またはagent-toolkitの公開インストーラーを再実行してください。",
        )
        return 1


def _run_agents_server(
    arguments: list[str],
    environment: dict[str, str],
    *,
    record_diagnostics: bool = True,
    independent: bool = False,
) -> int:
    """MCPサーバーを子として起動し、終了状態・受信シグナル・標準エラー末尾を終了診断へ追記する。

    標準入力と標準出力は継承してMCPのstdio通信を中継しない。呼出元へは従来のuv起動と同じ終了コードを返す。
    """
    if not record_diagnostics:
        received: list[dict[str, str]] = []
        with _server_lifetime(arguments, environment, received, None) as process:
            returncode = _wait_server(process, received)
        return _relay_server_exit(returncode)
    with contextlib.ExitStack() as lifetime:
        with tempfile.NamedTemporaryFile(prefix="agent-toolkit-agents-server-launch-", suffix=".json", delete=False) as record:
            record_path = Path(record.name)
            lifetime.callback(record_path.unlink, missing_ok=True)
        environment = {**environment, logging_config.LAUNCH_RECORD_ENV: str(record_path)}
        received = []
        tail = diagnostic_redaction.DiagnosticTail(_STDERR_TAIL_BYTES)
        # 停止シグナルの記録handlerは終了診断を書き終えるまで保つ。
        # 先に戻すと、書き終える前に届いたシグナルで記録せずに終了する。
        with _server_lifetime(
            arguments, environment, received, tail, inherit_stderr=independent, signal_scope=lifetime
        ) as process:
            returncode = _wait_owned_server(process, received, lambda: True) if independent else _wait_server(process, received)
        if independent:
            # 子が世代stderrへ直接flushしてからpeerを閉じる。tee threadの配送待ちを診断へ残さない。
            path = Path(arguments[-2]) / f"{arguments[-1]}.stderr.log"
            with path.open("rb") as stream:
                stderr_tail = diagnostic_redaction.read_diagnostic_tail(stream, limit=_STDERR_TAIL_BYTES).text
        else:
            stderr_tail = tail.excerpt().text
        diagnostics = logging_config.read_launch_record(record_path)
        terminated_by = signal.Signals(-returncode).name if returncode < 0 else None
        if diagnostics is None:
            # 起動単位を持たない正常終了（--check-dependenciesなど）は所在が無いのが通常であり、何も出力しない。
            if returncode != 0:
                next_action.report(
                    "診断ファイルの所在を受け取る前にagents_serverが異常終了したため、終了診断ファイルは未作成である:"
                    f" returncode={returncode} signal={terminated_by} stderr={stderr_tail}",
                    next_action="この行より前の標準エラーと、親CLIのMCP接続記録の同じ時刻の記録を確認してください。",
                )
        else:
            logging_config.append_instance_event(
                diagnostics,
                writer="launcher",
                event="process_exited",
                returncode=returncode,
                signal=terminated_by,
                launcher_signals=received,
                stderr_tail=stderr_tail,
            )
    return _relay_server_exit(returncode)


def _relay_server_exit(returncode: int) -> int:
    """POSIXのシグナル終了を、従来のuv起動と同じ128+signalへ変換する。"""
    if returncode < 0 and os.name != "nt":
        return 128 - returncode
    return returncode


def _wait_server(process: subprocess.Popen[bytes], received: list[dict[str, str]]) -> int:
    """親消滅と停止要求を監視し、自発終了しない子も有限な回収へ進める。"""
    with _parent_monitor() as parent_alive:
        return _wait_owned_server(process, received, parent_alive)


def _wait_owned_server(
    process: subprocess.Popen[bytes], received: list[dict[str, str]], parent_alive: Callable[[], bool]
) -> int:
    while process.poll() is None:
        if received or not parent_alive():
            # サーバー自身の独立した子孫handle回収（猶予15秒とTERM/KILL各5秒）を先に完走させる。
            _stop_server_process(
                process,
                signum=getattr(signal, received[0]["signal"]) if received else signal.SIGTERM,
                graceful_timeout=_SERVER_SHUTDOWN_TIMEOUT,
            )
            break
        with contextlib.suppress(subprocess.TimeoutExpired):
            return process.wait(timeout=0.1)
    assert process.returncode is not None
    return process.returncode


@contextlib.contextmanager
def _parent_monitor() -> Generator[Callable[[], bool]]:
    if sys.platform != "win32":
        yield lambda: 1 < _PARENT_PID == os.getppid()
        return
    # WindowsのPPIDは親の終了後も変わらないため、親のkernel handleを保持する。
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, _PARENT_PID)
    if not handle and ctypes.get_last_error() != 87:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield lambda: bool(handle) and kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        if handle:
            kernel.CloseHandle(handle)


@contextlib.contextmanager
def _server_lifetime(
    arguments: list[str],
    environment: dict[str, str],
    received: list[dict[str, str]],
    tail: Any,
    *,
    stdin: BinaryIO | None = None,
    cwd: Path | None = None,
    inherit_stderr: bool = False,
    signal_scope: contextlib.ExitStack | None = None,
    pass_fds: tuple[int, ...] = (),
) -> Generator[subprocess.Popen[bytes]]:
    """stdlibだけでbootstrapの子・reader・pipe・signal handlerを所有する。

    `signal_scope`を渡すと、signal handlerの復元をこの関数の終了ではなく`signal_scope`の終了まで延ばす。
    """
    # Popen.__exit__の無期限waitを避け、bootstrap自身の有限なprocess group回収を使う。
    # 時間上限なし: 起動元（hook、MCP、CLI）へ終了状態を渡す中継であり、上限は起動元が持つ。
    with contextlib.ExitStack() as cleanup:
        job = None
        if os.name == "nt":
            from agent_toolkit._common.windows_job import WindowsJob

            job = cleanup.enter_context(WindowsJob())
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            arguments,
            env=environment,
            stdin=stdin,
            cwd=cwd,
            stderr=None if inherit_stderr else subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=0x00000004 if job is not None else 0,
            pass_fds=pass_fds,
        )
        if process.stderr is not None:
            cleanup.callback(process.stderr.close)
        try:
            if job is not None:
                owned_process: Any = process
                owned_process._bootstrap_job = job  # pylint: disable=protected-access  # 所有Jobを停止処理へ引き継ぐ。
                # Windows版Popenが生成時に保持する元handleを使う。POSIX側の型推論には存在しない。
                job.assign_suspended(
                    int(owned_process._handle),  # pylint: disable=no-member,protected-access
                    process.pid,
                )
            if os.name != "nt":
                restore = cleanup if signal_scope is None else signal_scope
                for name in _FORWARDED_SIGNALS:
                    signum = getattr(signal, name)
                    previous = signal.signal(signum, _forwarding_handler(process, received))
                    restore.callback(signal.signal, signum, previous)
            if process.stderr is not None:
                reader = threading.Thread(target=_tee_stderr, args=(process.stderr, tail), daemon=True)
                cleanup.callback(_join_reader, reader)
                reader.start()
            yield process
        finally:
            _stop_server_process(process)


def _join_reader(reader: threading.Thread) -> None:
    if reader.ident is not None:
        reader.join(timeout=_STOP_TIMEOUT)
        if reader.is_alive():
            raise TimeoutError("agents_serverの標準エラーreaderを終了できませんでした")


def _stop_server_process(
    process: subprocess.Popen[bytes], *, signum: int | None = None, graceful_timeout: float | None = None
) -> None:
    """自ら起動したbootstrap子のprocess groupを有限時間で回収する。"""
    if signum is not None and process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signum)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_STOP_TIMEOUT if graceful_timeout is None else graceful_timeout)
    if os.name == "nt":
        job = getattr(process, "_bootstrap_job", None)
        if job is None:
            raise OSError("起動時に所有したWindows Jobがありません")
        try:
            job.terminate(timeout=_STOP_TIMEOUT)
        except BaseException:
            # 所属照会の失敗を伝播しつつ、所有handleを閉じてpipe writerを解放する。
            job.close()
            raise
        finally:
            # Jobへの所属前に失敗した停止状態の起点も、元Popenから回収する。
            if process.poll() is None:
                process.kill()
            process.wait(timeout=_STOP_TIMEOUT)
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=_STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # 親が先に終了しても同じ所有groupに残る子孫と、そのstderr writerを回収する。
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            process.wait(timeout=_STOP_TIMEOUT)


def _forwarding_handler(_process: subprocess.Popen[bytes], received: list[dict[str, str]]) -> Any:
    # シグナルが続けて届いても、ハンドラー内でlazy importを再入させない。
    clock, utc = datetime.datetime.now, datetime.UTC

    def handler(signum: int, _frame: Any) -> None:
        received.append({"signal": signal.Signals(signum).name, "at": clock(utc).isoformat()})

    return handler


def _tee_stderr(stream: Any, tail: Any) -> None:
    """子の標準エラーを受け取った順に自身の標準エラーへ出力し、診断抜粋の末尾（`DiagnosticTail`、不要なら`None`）へ渡す。"""
    while chunk := stream.read1(65536):
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()
        if tail is not None:
            tail.feed(chunk)


if __name__ == "__main__":
    sys.exit(main())
