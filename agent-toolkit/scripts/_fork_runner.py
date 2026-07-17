"""常駐fork-serverプロセス経由でスクリプトを同一プロセス内実行し、フルPythonインタプリタ起動を省くテストヘルパー。

本モジュールは「クライアントAPI」（`run_script`）と「サーバー本体」（`_serve`以降、
`if __name__ == "__main__":`配下）を兼ねる。処理の詳細は各関数のdocstringを参照する。
"""

from __future__ import annotations

import atexit
import json
import os
import runpy
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

_HAS_FORK = hasattr(os, "fork")
_LOCK = threading.Lock()
_SERVER: subprocess.Popen[bytes] | None = None


class _ServerUnavailable(Exception):
    """fork-serverの起動・応答に失敗したことを示す内部例外。呼び出し元でsubprocess.runへフォールバックする。"""


def run_script(
    script_path: Path,
    *,
    argv: tuple[str, ...] = (),
    input: str = "",  # noqa: A002 -- subprocess.run引数名との対称性を優先する  # pylint: disable=redefined-builtin
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """スクリプトを実行しsubprocess.CompletedProcess互換の結果を返す。

    fork-serverが利用できる環境ではサーバーへリクエストを送って実行し、
    利用できない場合・サーバー起動や応答に失敗した場合はsubprocess.runへ委譲する。
    """
    if _HAS_FORK:
        try:
            return _run_via_server(script_path, argv=argv, input=input, env=env, cwd=cwd, timeout=timeout)
        except _ServerUnavailable:
            pass
    return subprocess.run(
        [sys.executable, str(script_path), *argv],
        input=input,
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else os.environ.copy(),
        cwd=cwd,
        timeout=timeout,
    )


def _run_via_server(
    script_path: Path,
    *,
    argv: tuple[str, ...],
    input: str,  # noqa: A002  # pylint: disable=redefined-builtin
    env: dict[str, str] | None,
    cwd: Path | None,
    timeout: float | None,
) -> subprocess.CompletedProcess[str]:
    with _LOCK:
        server = _ensure_server()
        with tempfile.TemporaryDirectory(prefix="fork_runner_") as tmpdir:
            input_path = Path(tmpdir) / "stdin.txt"
            stdout_path = Path(tmpdir) / "stdout.txt"
            stderr_path = Path(tmpdir) / "stderr.txt"
            input_path.write_text(input, encoding="utf-8")
            request = {
                "script": str(script_path),
                "argv": list(argv),
                "input_path": str(input_path),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                # env・cwd未指定時も現在の環境・作業ディレクトリを必ず格納する
                # （fork子がサーバー起動時の環境を継承するとsubprocess.runとの等価性が崩れるため）
                "env": env if env is not None else os.environ.copy(),
                "cwd": str(cwd) if cwd is not None else os.getcwd(),
                "timeout": timeout,
            }
            assert server.stdin is not None  # noqa: S101 -- _ensure_server()が保証する不変条件の表明
            assert server.stdout is not None  # noqa: S101
            try:
                server.stdin.write((json.dumps(request) + "\n").encode())
                server.stdin.flush()
                response_line = server.stdout.readline()
            except OSError as exc:
                _terminate_server()
                raise _ServerUnavailable(f"fork-serverとの通信に失敗した: {exc}") from exc
            if not response_line:
                _terminate_server()
                raise _ServerUnavailable("fork-serverが応答を返さなかった")
            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as exc:
                _terminate_server()
                raise _ServerUnavailable(f"fork-serverの応答が不正だった: {exc}") from exc
            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    if response.get("timeout"):
        # timeout応答はサーバー側が timeout is not None の場合にのみ返す（_wait_with_timeout参照）。
        assert timeout is not None  # noqa: S101 -- 上記不変条件の表明
        # subprocess.runの契約と揃え、タイムアウト時点までの部分出力をoutput/stderrへ格納する。
        raise subprocess.TimeoutExpired(
            [sys.executable, str(script_path), *argv], timeout, output=stdout_text, stderr=stderr_text
        )
    return subprocess.CompletedProcess(
        args=[sys.executable, str(script_path), *argv],
        returncode=response["returncode"],
        stdout=stdout_text,
        stderr=stderr_text,
    )


def _ensure_server() -> subprocess.Popen[bytes]:
    global _SERVER  # noqa: PLW0603 -- テストプロセスごとの遅延シングルトン  # pylint: disable=global-statement
    if _SERVER is not None and _SERVER.poll() is None:
        return _SERVER
    try:
        server = subprocess.Popen(  # noqa: S603 -- 自モジュールの再起動でありユーザー入力を経由しない  # pylint: disable=consider-using-with
            [sys.executable, str(Path(__file__).resolve())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
    except OSError as exc:
        raise _ServerUnavailable(f"fork-server起動に失敗した: {exc}") from exc
    _SERVER = server
    atexit.register(_terminate_server)
    return server


def _terminate_server() -> None:
    global _SERVER  # noqa: PLW0603  # pylint: disable=global-statement
    if _SERVER is not None and _SERVER.poll() is None:
        _SERVER.terminate()
    _SERVER = None


# --- サーバー本体（`subprocess.Popen`で子プロセスとして起動される） ---


def _serve() -> None:
    """標準入力から1行1リクエストのJSONを読み、forkした子でスクリプトを実行し応答を返す。"""
    # サーバー起動時に重い共通依存を事前importし、以降のfork子でimportコストを省く
    # （対象スクリプト自体はimportしない。対象は都度runpyで実行するため）。
    import pyfltr.colloquial  # noqa: F401,PLC0415 -- 事前importが目的でありモジュール自体は未使用  # pylint: disable=import-outside-toplevel
    import yaml  # noqa: F401,PLC0415 -- 同上  # pylint: disable=import-outside-toplevel

    del pyfltr, yaml
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        request = json.loads(line)
        response = _handle_request(request)
        sys.stdout.buffer.write((json.dumps(response) + "\n").encode())
        sys.stdout.buffer.flush()


def _handle_request(request: dict) -> dict:
    pid = os.fork()
    if pid == 0:
        _run_child(request)
    returncode, timed_out = _wait_with_timeout(pid, request.get("timeout"))
    return {"returncode": returncode, "timeout": timed_out}


def _wait_with_timeout(pid: int, timeout: float | None) -> tuple[int, bool]:
    if timeout is None:
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), False
    deadline = time.monotonic() + timeout
    # 簡略化: タイムアウト監視をos.waitpid(WNOHANG)+0.01秒間隔ポーリングで実装している。
    # 既知の限界: 高頻度にtimeout指定するテストが大量発生するとCPUを消費する。
    # 見直し契機: timeout付きrun_script呼び出しが多数を占め体感の遅延が観測された時点。
    while True:
        finished_pid, status = os.waitpid(pid, os.WNOHANG)
        if finished_pid != 0:
            return os.waitstatus_to_exitcode(status), False
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            return -signal.SIGKILL, True
        time.sleep(0.01)


def _run_child(request: dict) -> None:
    stdin_fd = os.open(request["input_path"], os.O_RDONLY)
    stdout_fd = os.open(request["stdout_path"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    stderr_fd = os.open(request["stderr_path"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(stdin_fd, 0)
    os.dup2(stdout_fd, 1)
    os.dup2(stderr_fd, 2)
    # サーバーが差し替えたsys.stdin/stdout/stderrオブジェクトを再接続後のfdへ再束縛する
    # （fd差し替えのみでは対象スクリプトのsys.stdin.read()・printが一時ファイルへ到達しない）
    sys.stdin = open(0, encoding="utf-8", closefd=False)  # noqa: SIM115  # pylint: disable=consider-using-with
    sys.stdout = open(1, "w", encoding="utf-8", closefd=False)  # noqa: SIM115  # pylint: disable=consider-using-with
    sys.stderr = open(2, "w", encoding="utf-8", closefd=False)  # noqa: SIM115  # pylint: disable=consider-using-with
    # env・cwdはクライアント側で常に格納されるため無条件に適用する
    os.environ.clear()
    os.environ.update(request["env"])
    os.chdir(request["cwd"])
    script = request["script"]
    sys.argv = [script, *request["argv"]]
    exit_code = 0
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as exc:
        # pylintの型推論器がSystemExit.codeを誤って定数と解釈する既知の限界に対する回避。
        # pylint: disable-next=using-constant-test
        exit_code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
    except BaseException:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        exit_code = 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)  # pylint: disable=protected-access


if __name__ == "__main__":
    _serve()
