"""標準ライブラリだけで起動し、配布root外の依存環境から指定スクリプトを実行する。

agents_serverのMCPサーバーだけは子プロセスとして起動して終了を待ち、終了状態と標準エラー末尾を
サーバーが報告した起動単位の終了診断へ追記する。サーバー自身が記録できない終了も観測するためである。
"""

import argparse
import contextlib
import datetime
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_toolkit._agents_server import logging_config  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._common import dotfiles_mode, next_action, plugin_environment  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_AGENTS_SERVER_TARGET = Path("agent_toolkit") / "agents_server_mcp.py"
_STDERR_TAIL_BYTES = 16 * 1024
_FORWARDED_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP")
_STOP_TIMEOUT = 5.0


def main() -> int:
    """CLI・hook・MCP・スキルスクリプトのargvと終了状態をuvへそのまま渡す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1], help="配布rootの絶対パス")
    parser.add_argument("--persist-claude-env", action="store_true", help="SessionStartのBash環境ファイルへCLIのPATHを追記する")
    parser.add_argument("--prepare", action="store_true", help="共有依存環境を準備して終了する")
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
        environment = plugin_environment.launch_environment(root, caller_environment)
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
        return _prepare(root, uv, environment)
    python = plugin_environment.prepared_python(root, caller_environment)
    if python is None:
        next_action.report(
            "共有依存環境が未準備のため、agent-toolkitを起動できません。",
            next_action="`update-dotfiles`またはagent-toolkitの公開インストーラーを再実行して依存環境を準備してください。",
        )
        return 1
    arguments = [str(python), str(target), *options.arguments]
    environment = plugin_environment.direct_environment(root, caller_environment)
    if target == root / _AGENTS_SERVER_TARGET:
        return _run_agents_server(arguments, environment)
    if target == root / "agent_toolkit/_common/pyfltr_entry.py" and options.arguments[:1] == ["mcp"]:
        return _run_agents_server(arguments, environment, record_diagnostics=False)
    if os.name == "nt":
        # Windowsのexecveは子の終了を待たず、呼出元へ終了状態を渡せない。
        return subprocess.run(arguments, env=environment, check=False).returncode
    os.execve(str(python), arguments, environment)
    return 1


def _prepare(root: Path, uv: str, environment: dict[str, str]) -> int:
    """依存だけを一時環境へ同期し、完了後に共有環境として公開する。"""
    target = plugin_environment.project_environment(root, environment)
    try:
        with plugin_environment.prepare_lock(target):
            if plugin_environment.prepared_python(root, environment) is not None:
                return 0
            prepare_environment = dict(environment)
            prepare_environment["UV_PROJECT_ENVIRONMENT"] = str(target)
            result = subprocess.run(
                [
                    uv,
                    "sync",
                    "--project",
                    str(root),
                    "--python",
                    sys.executable,
                    "--locked",
                    "--no-default-groups",
                    "--no-install-project",
                ],
                env=prepare_environment,
                check=False,
            )
            if result.returncode != 0:
                return result.returncode
            plugin_environment.publish_prepared_environment(root, environment)
            return 0
    except (OSError, TimeoutError) as exc:
        next_action.report(
            f"共有依存環境を準備できません: {exc}",
            next_action="`update-dotfiles`またはagent-toolkitの公開インストーラーを再実行してください。",
        )
        return 1


def _run_agents_server(arguments: list[str], environment: dict[str, str], *, record_diagnostics: bool = True) -> int:
    """MCPサーバーを子として起動し、終了状態・受信シグナル・標準エラー末尾を終了診断へ追記する。

    標準入力と標準出力は継承してMCPのstdio通信を中継しない。呼出元へは従来のuv起動と同じ終了コードを返す。
    """
    if not record_diagnostics:
        with _server_lifetime(arguments, environment, [], bytearray()) as process:
            returncode = process.wait()
        return _relay_server_exit(returncode)
    with contextlib.ExitStack() as lifetime:
        with tempfile.NamedTemporaryFile(prefix="agent-toolkit-agents-server-launch-", suffix=".json", delete=False) as record:
            record_path = Path(record.name)
            lifetime.callback(record_path.unlink, missing_ok=True)
        environment = {**environment, logging_config.LAUNCH_RECORD_ENV: str(record_path)}
        received: list[dict[str, str]] = []
        tail = bytearray()
        with _server_lifetime(arguments, environment, received, tail) as process:
            returncode = process.wait()
        diagnostics = logging_config.read_launch_record(record_path)
        terminated_by = signal.Signals(-returncode).name if returncode < 0 else None
        if diagnostics is None:
            # 起動単位を持たない正常終了（--check-dependenciesなど）は所在が無いのが通常であり、何も出力しない。
            if returncode != 0:
                next_action.report(
                    "診断ファイルの所在を受け取る前にagents_serverが異常終了したため、終了診断ファイルは未作成である:"
                    f" returncode={returncode} signal={terminated_by}",
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
                stderr_tail=bytes(tail).decode("utf-8", errors="replace"),
            )
    return _relay_server_exit(returncode)


def _relay_server_exit(returncode: int) -> int:
    """POSIXのシグナル終了を、従来のuv起動と同じ128+signalへ変換する。"""
    if returncode < 0 and os.name != "nt":
        return 128 - returncode
    return returncode


@contextlib.contextmanager
def _server_lifetime(
    arguments: list[str], environment: dict[str, str], received: list[dict[str, str]], tail: bytearray
) -> Generator[subprocess.Popen[bytes]]:
    """stdlibだけでbootstrapの子・reader・pipe・signal handlerを所有する。"""
    # Popen.__exit__の無期限waitを避け、bootstrap自身の有限なprocess group回収を使う。
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        arguments, env=environment, stderr=subprocess.PIPE, start_new_session=os.name != "nt"
    )
    with contextlib.ExitStack() as cleanup:
        assert process.stderr is not None
        cleanup.callback(process.stderr.close)
        try:
            if os.name != "nt":
                for name in _FORWARDED_SIGNALS:
                    signum = getattr(signal, name)
                    previous = signal.signal(signum, _forwarding_handler(process, received))
                    cleanup.callback(signal.signal, signum, previous)
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


def _stop_server_process(process: subprocess.Popen[bytes]) -> None:
    """自ら起動したbootstrap子のprocess groupを有限時間で回収する。"""
    if os.name == "nt":
        if process.poll() is not None:
            return
        # stdlib起動段階にはpsutilが無い。自ら起動したPIDの子孫だけをWindows標準のtaskkillへ渡す。
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_STOP_TIMEOUT,
            )
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=_STOP_TIMEOUT)
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=_STOP_TIMEOUT)
    finally:
        # 親が先に終了しても同じ所有groupに残る子孫と、そのstderr writerを回収する。
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            process.wait(timeout=_STOP_TIMEOUT)


def _forwarding_handler(process: subprocess.Popen[bytes], received: list[dict[str, str]]) -> Any:
    def handler(signum: int, _frame: Any) -> None:
        received.append({"signal": signal.Signals(signum).name, "at": datetime.datetime.now(datetime.UTC).isoformat()})
        if process.poll() is None:
            process.send_signal(signum)

    return handler


def _tee_stderr(stream: Any, tail: bytearray) -> None:
    """子の標準エラーを受け取った順に自身の標準エラーへ出力し、末尾16KiBを保持する。"""
    while chunk := stream.read1(65536):
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()
        tail.extend(chunk)
        del tail[:-_STDERR_TAIL_BYTES]


if __name__ == "__main__":
    sys.exit(main())
