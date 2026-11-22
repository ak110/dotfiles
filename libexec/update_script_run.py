# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///

import contextlib
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass

if sys.platform == "win32":
    import _winapi  # pylint: disable=import-error
    import ctypes
    import msvcrt  # pylint: disable=import-error

_STDERR = "_DOTFILES_UV_STDERR"
_SCRIPT = pathlib.Path(__file__).with_name("update_dotfiles.py")


@dataclass(frozen=True)
class _UvOutput:
    """uvが標準エラーへ書いた1行と、その保存先を運ぶ。"""

    raw: bytes
    log: pathlib.Path | None = None


def _event(line: bytes) -> _UvOutput:
    plain = re.sub(rb"\x1b\[[0-9;:]*m", b"", line)
    if re.fullmatch(rb"Installed [0-9]+ packages? in (?:[0-9.]+(?:ns|us|\xc2\xb5s|\xce\xbcs|ms|s|m|h) ?)+\r?\n", plain):
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData/Local"
        else:
            base = os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local/state"
        return _UvOutput(line, pathlib.Path(base) / "agent-toolkit/uv-更新本体の依存準備.log")
    return _UvOutput(line)


def _forward(event: _UvOutput) -> None:
    if (log := event.log) is not None:
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=log.parent, delete_on_close=False) as output:
                output.write(event.raw)
                output.close()
                os.replace(output.name, log)
            return
        except OSError as error:
            message = f"明細を保存できません: {log}: {error}。書込権限と空き容量を確認してください。\n"
            sys.stderr.buffer.write(message.encode("utf-8", errors="replace"))
    sys.stderr.buffer.write(event.raw)
    sys.stderr.buffer.flush()


def restore_stderr() -> None:
    value = os.environ.pop(_STDERR, None)
    if value is None:
        return
    if sys.platform == "win32":
        if value == "console":
            fd = os.open("CONOUT$", os.O_RDWR | os.O_BINARY)
        else:
            pid, handle = map(int, value.split(":"))
            source = _winapi.OpenProcess(_winapi.PROCESS_DUP_HANDLE, False, pid)
            try:
                copy = _winapi.DuplicateHandle(
                    source, handle, _winapi.GetCurrentProcess(), 0, False, _winapi.DUPLICATE_SAME_ACCESS
                )
            finally:
                _winapi.CloseHandle(source)
            fd = msvcrt.open_osfhandle(copy, os.O_WRONLY | os.O_BINARY)
    else:
        fd = int(value)
    try:
        os.dup2(fd, 2)
    finally:
        os.close(fd)
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.SetStdHandle(-12, ctypes.c_void_p(msvcrt.get_osfhandle(2))):
            raise ctypes.WinError(ctypes.get_last_error())
        # コンソールではWindowsConsoleIO、リダイレクト先ではFileIOを選び直す。
        # 標準ストリームとしてプロセスの終了まで使う。
        sys.stderr = sys.__stderr__ = open(  # noqa: SIM115  # pylint: disable=consider-using-with
            2, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False
        )


def _run(arguments: list[str]) -> int:
    uv = pathlib.Path.home() / ".local/bin" / ("uv.exe" if os.name == "nt" else "uv")
    with contextlib.ExitStack() as stack:
        pass_fds = ()
        if sys.platform == "win32":
            handle = msvcrt.get_osfhandle(2)

            is_console = ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(ctypes.c_ulong(0)))
            value = "console" if is_console else f"{os.getpid()}:{handle}"
            if os.environ.pop("DOTFILES_SHOW_CACHE_WAIT", "") == "1":
                notice = "更新本体の起動: uvキャッシュの整理中は待つ場合があります。Ctrl+Cで中断できます。"
                if is_console:
                    print(notice, file=sys.stderr, flush=True)
                else:
                    sys.stderr.buffer.write((notice + "\n").encode("utf-8"))
                    sys.stderr.buffer.flush()
        else:
            fd = os.dup(2)
            stack.callback(os.close, fd)
            pass_fds = (fd,)
            value = str(fd)
        process = stack.enter_context(
            subprocess.Popen(
                [uv, "run", "--no-project", "--script", _SCRIPT, *arguments],
                stderr=subprocess.PIPE,
                env={**os.environ, _STDERR: value},
                pass_fds=pass_fds,
            )
        )

        previous = signal.signal(signal.SIGINT, lambda _signal, _frame: None)
        stack.callback(signal.signal, signal.SIGINT, previous)
        assert process.stderr
        for line in process.stderr:
            _forward(_event(line))
        code = process.wait()
        if sys.platform == "win32":
            return ctypes.c_int32(code).value
        return code if code >= 0 else 128 - code


if __name__ == "__main__":
    try:
        raise SystemExit(_run(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
