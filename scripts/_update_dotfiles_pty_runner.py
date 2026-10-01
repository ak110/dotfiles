# /// script
# requires-python = ">=3.13"
# dependencies = ["filelock", "psutil", "platformdirs"]
# ///
"""pytestの他スレッドを継承せず、端末テストを実行する専用プロセス。"""

import contextlib
import json
import os
import pathlib
import pty
import select
import signal
import sys
import time
import traceback

import psutil
import update_dotfiles


def main() -> int:
    """要求を読み、実物のgit pullの終了コードと端末出力を返す。"""
    request = json.load(sys.stdin)
    returncode, output, descendant_running = _run_in_terminal(request["timeout"], request["input_text"])
    print(json.dumps({"returncode": returncode, "output": output, "descendant_running": descendant_running}))
    return 0


def _run_in_terminal(timeout: int | None, input_text: str | None) -> tuple[int, str, bool]:
    # xdistワーカーには受信スレッドがあるため、新しい単一スレッドプロセスだけがforkする。
    pid, terminal_fd = pty.fork()
    if pid == 0:
        returncode = 1
        try:
            returncode = update_dotfiles._run_git_pull(1, 4, timeout=timeout)  # noqa: SLF001  # pylint: disable=protected-access
        except Exception:  # pylint: disable=broad-exception-caught
            # 子の例外を端末出力へ残し、親のJSON出力へ混ぜない。
            traceback.print_exc()
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(returncode)

    output = bytearray()
    input_sent = False
    deadline = time.monotonic() + 20
    status: int | None = None
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([terminal_fd], [], [], 0.1)
            if readable:
                with contextlib.suppress(OSError):
                    output.extend(os.read(terminal_fd, 4096))
            if input_text is not None and not input_sent and b"passphrase:" in output:
                os.write(terminal_fd, f"{input_text}\n".encode())
                input_sent = True
            waited_pid, candidate_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = candidate_status
                break
        if status is None:
            raise TimeoutError("疑似端末内のgit pullテストが20秒以内に終了しなかった。端末テストの診断を確認してください。")
        # 救済のkillより前の状態で製品の回収結果を判定する。
        descendant_running = False
        pid_path = pathlib.Path(os.environ["UPDATE_DOTFILES_TEST_PID_PATH"])
        if pid_path.exists():
            with contextlib.suppress(psutil.NoSuchProcess):
                descendant_running = psutil.Process(int(pid_path.read_text(encoding="utf-8"))).status() != psutil.STATUS_ZOMBIE
    finally:
        # forkptyが作成した専用セッションの子孫も回収する。pytestや他のテストはこの群に属さない。
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
        if status is None:
            os.waitpid(pid, 0)
        os.close(terminal_fd)
    assert status is not None
    return os.waitstatus_to_exitcode(status), output.decode("utf-8", errors="replace"), descendant_running


if __name__ == "__main__":
    sys.exit(main())
