"""コマンドライン引数の解析、sshの実行、接続先が返した結果の解釈。

接続先では`remote.ps1`がsshのセッションのまま予定タスクを登録し、タスクの中で`runner.ps1`がスクリプトを実行する。
`remote.ps1`は標準出力へスクリプトの出力を書いた後、`SSH_UNELEVATED_RESULT:<実行ID>`で始まる行へ結果のJSONを書く。
"""

import argparse
import base64
import codecs
import contextlib
import dataclasses
import io
import json
import os
import pathlib
import secrets
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from importlib import resources

import psutil

from pytools._internal.cli import enable_completion
from pytools._internal.common import process_scope

_START_PREFIX = b"SSH_UNELEVATED_STARTED:"
_RESULT_PREFIX = b"\nSSH_UNELEVATED_RESULT:"
# 接続先の起動部が標準入力から本文を読み終えるまでの上限。
_STDIN_TIMEOUT_SECONDS = 60
# 接続、タスクの登録、後始末に要する時間の見込み。上限にこれを足しても終わらないsshは打ち切る。
_SSH_MARGIN_SECONDS = 120
_DEFAULT_TIMEOUT_SECONDS = 1800
_EXIT_TIMEOUT = 124
_EXIT_FAILURE = 125
_EXIT_SSH_FAILURE = 255


class SshReclaimError(RuntimeError):
    pass


def _communicate_posix(process: subprocess.Popen[bytes], payload: bytes, timeout: float) -> tuple[bytes, bytes]:
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
    deadline = time.monotonic() + timeout
    stdout_fd, stderr_fd = stdout.fileno(), stderr.fileno()
    output = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    remaining = memoryview(payload)
    stop_attempted = False

    def group_has_live_member() -> bool:
        for member in psutil.process_iter():
            try:
                if os.getpgid(member.pid) == process.pid and member.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    return True
            except ProcessLookupError, psutil.NoSuchProcess:
                continue
            except (OSError, psutil.Error) as error:
                raise SshReclaimError("sshのプロセスグループの停止を確認できない") from error
        return False

    def stop_group() -> None:
        nonlocal stop_attempted
        stop_attempted = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise SshReclaimError("sshのプロセスグループを停止できない") from error
        stop_deadline = time.monotonic() + 1.0
        while group_has_live_member():
            if time.monotonic() >= stop_deadline:
                raise SshReclaimError("sshのプロセスグループに停止できなかったプロセスが残った")
            time.sleep(0.02)

    def exited() -> bool:
        try:
            state = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except (AttributeError, OSError) as error:
            raise SshReclaimError("sshの終了を回収前に確認できない") from error
        return state is not None and state.si_pid != 0

    def close_input(selector: selectors.BaseSelector) -> None:
        if not stdin.closed:
            selector.unregister(stdin)
            stdin.close()

    try:
        with selectors.DefaultSelector() as selector:
            for stream in (stdin, stdout, stderr):
                os.set_blocking(stream.fileno(), False)
            selector.register(stdin, selectors.EVENT_WRITE)
            selector.register(stdout, selectors.EVENT_READ)
            selector.register(stderr, selectors.EVENT_READ)
            while not stop_attempted or selector.get_map():
                if not stop_attempted and exited():
                    # 回収前のPIDを保持し、別のグループへのシグナル送信を防ぐ。
                    stop_group()
                    close_input(selector)
                delay = deadline - time.monotonic()
                if delay <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout, bytes(output[stdout_fd]), bytes(output[stderr_fd]))
                if not selector.get_map() and stop_attempted:
                    break
                for key, _ in selector.select(min(delay, 0.05)):
                    if key.fileobj is stdin:
                        try:
                            remaining = remaining[os.write(key.fd, remaining[:4096]) :]
                        except BrokenPipeError:
                            remaining = remaining[:0]
                        except BlockingIOError:
                            continue
                        if not remaining:
                            close_input(selector)
                        continue
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if chunk:
                        output[key.fd].extend(chunk)
                    else:
                        selector.unregister(key.fileobj)
    finally:
        if not stop_attempted:
            exited()
            stop_group()
    process.wait()
    return bytes(output[stdout_fd]), bytes(output[stderr_fd])


# 接続先が返す状態のうちスクリプトの終了コードを持たないものの、このコマンドの終了コードと診断。
_FAILURES: dict[str, tuple[int, str]] = {
    "timeout": (
        _EXIT_TIMEOUT,
        "上限の{timeout}秒に達したため停止した。出力は停止までの分である。必要なら--timeoutを延ばして再実行する。",
    ),
    "not_logged_on": (
        _EXIT_FAILURE,
        "{host}に{user}が対話ログオンしていないため、非昇格のセッションで実行できない。"
        "{host}へのログオンをユーザーに依頼してから再実行する。",
    ),
    "not_started": (
        _EXIT_FAILURE,
        "予定タスクが30秒以内に開始しなかった（LastTaskResult=0x{task_result:08X}）。"
        "{host}のタスクスケジューラーの状態を確かめてから再実行する。",
    ),
    "no_result": (
        _EXIT_FAILURE,
        "予定タスクが終了コードを残さずに終わった（LastTaskResult=0x{task_result:08X}）。"
        "出力にssh-unelevatedの診断があればその原因を、無ければスクリプトがPowerShellのプロセスごと終了させていないかを確かめる。",
    ),
    "error": (_EXIT_FAILURE, "{host}での準備か後始末に失敗した: {message}"),
}


@dataclasses.dataclass(frozen=True)
class RemoteResult:
    """`remote.ps1`が結果の行へ書くJSON。"""

    status: str
    exit_code: int | None = None
    task_result: int | None = None
    user: str | None = None
    message: str | None = None


@dataclasses.dataclass(frozen=True)
class Outcome:
    """このコマンドの終了コード、標準出力へ書くスクリプトの出力、標準エラーへ書く診断。"""

    exit_code: int
    output: str
    diagnostic: str | None = None


def main(argv: Sequence[str] | None = None) -> None:
    """引数のPowerShellスクリプトを接続先で非昇格で実行し、出力と終了コードを返すエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="sshの接続先のWindowsで、ログオン中のユーザーの非昇格の権限でPowerShellスクリプトを実行する。",
        epilog=(
            "例: ssh-unelevated stheno check.ps1 / "
            "echo 'update-dotfiles' | ssh-unelevated --timeout 3600 stheno。"
            f"終了コードはスクリプトの終了コード（上限到達は{_EXIT_TIMEOUT}、接続先での失敗は{_EXIT_FAILURE}、"
            f"sshの接続失敗は{_EXIT_SSH_FAILURE}）。"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"スクリプトの実行の上限秒（既定{_DEFAULT_TIMEOUT_SECONDS}）",
    )
    parser.add_argument("host", help="sshの接続先")
    parser.add_argument("script", nargs="?", default="-", help="PowerShellスクリプトのファイル（省略か-で標準入力）")
    enable_completion(parser)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        data = sys.stdin.buffer.read() if args.script == "-" else pathlib.Path(args.script).read_bytes()
        script = data.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        parser.error(f"スクリプトを読めない（UTF-8のファイルか標準入力を指定する）: {e}")

    run_id = secrets.token_hex(8)
    limit = args.timeout + _SSH_MARGIN_SECONDS
    stderr = b""
    try:
        payload = build_payload(script, run_id, args.timeout)
        posix = os.name == "posix"
        if posix and not all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "WEXITED", "WNOHANG", "P_PID")):
            raise SshReclaimError("この環境はsshの終了を回収前に確認できない")
        with process_scope(
            build_ssh_command(args.host),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **({"start_new_session": True} if posix else {}),
        ) as process:
            if posix:
                stdout, stderr = _communicate_posix(process, payload, limit)
            else:
                stdout, stderr = process.communicate(payload, timeout=limit)
            returncode = process.returncode
    except SshReclaimError as e:
        outcome = Outcome(_EXIT_FAILURE, "", str(e))
    except FileNotFoundError:
        outcome = Outcome(_EXIT_FAILURE, "", "sshが見つからない。OpenSSHのクライアントを導入してから再実行する。")
    except KeyboardInterrupt:
        outcome = Outcome(
            130,
            "",
            f"中断した。{args.host}のスクリプトは終了か上限まで動き続け、その後に接続先が予定タスクと一時ファイルを片付ける。",
        )
    except subprocess.TimeoutExpired as e:
        stderr = e.stderr or b""
        outcome = Outcome(
            _EXIT_TIMEOUT,
            _decode(_split_start(e.stdout or b"", run_id)[1]),
            f"sshが{limit}秒以内に終わらず打ち切った。{args.host}での実行結果は不明。再実行する前に接続先の結果を確かめる。",
        )
    else:
        outcome = interpret(stdout, returncode, run_id=run_id, host=args.host, timeout=args.timeout)
    sys.stdout.write(outcome.output)
    sys.stdout.flush()
    sys.stderr.write(_decode(stderr))
    if outcome.diagnostic is not None:
        print(f"ssh-unelevated: {outcome.diagnostic}", file=sys.stderr)
    sys.exit(outcome.exit_code)


def build_ssh_command(host: str) -> list[str]:
    """接続先で起動部を実行するsshのコマンド行を返す。

    起動部は、標準入力のbase64をUTF-8の本文へ戻して実行する。
    sshの既定シェルがcmd.exeの場合の1行の上限（8191文字）に掛からないよう、本文は標準入力で渡す。
    標準入力は`[Console]::In`で読まず、そのハンドルを直接読むFileStreamで読む。
    Windows PowerShell 5.1（.NET Framework）のコンソールのstreamは、空のまま開いているパイプを読むとパイプのハンドルを待ち、
    後から本文やEOFが届いても戻らない。sshdが本文かEOFを書き込む前に読み始めた実行が止まっていた（2026年11月19日、sthenoで観測）。
    """
    bootstrap = (
        "$ProgressPreference='SilentlyContinue';"
        "$s=[Console]::OpenStandardInput();"
        "$h=$s.GetType().GetField('_handle',[Reflection.BindingFlags]'NonPublic,Instance').GetValue($s);"
        "$f=New-Object IO.FileStream($h,[IO.FileAccess]::Read);"
        "$m=New-Object IO.MemoryStream;"
        f"if(-not $f.CopyToAsync($m).Wait({_STDIN_TIMEOUT_SECONDS * 1000})){{"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "[Console]::Error.WriteLine('ssh-unelevated: 接続先が標準入力から本文を"
        f"{_STDIN_TIMEOUT_SECONDS}秒以内に読み終えられなかった。sshの接続を確かめてから再実行する。');exit 1}}"
        "Invoke-Expression ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("
        "[Text.Encoding]::ASCII.GetString($m.ToArray()))))"
    )
    encoded = base64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")
    return ["ssh", "-o", "BatchMode=yes", "--", host, f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"]


def build_payload(script: str, run_id: str, timeout: int) -> bytes:
    """sshの標準入力へ渡す、`remote.ps1`の呼び出しのbase64を返す。

    引数の値は16進数、整数、base64だけにして、PowerShellの引用符の扱いに依存しない。
    スクリプトにはBOMを付け、Windows PowerShell 5.1がUTF-8として読むようにする。
    """
    package = resources.files("pytools.ssh_unelevated")
    remote = package.joinpath("remote.ps1").read_text(encoding="utf-8-sig")
    runner = package.joinpath("runner.ps1").read_bytes()
    script_bytes = codecs.BOM_UTF8 + script.encode("utf-8")
    invocation = (
        f"& {{\n{remote}\n}} -RunId '{run_id}' -TimeoutSeconds {timeout}"
        f" -ScriptBase64 '{_b64(script_bytes)}' -RunnerBase64 '{_b64(runner)}'"
    )
    return base64.b64encode(invocation.encode("utf-8"))


def _split_start(stdout: bytes, run_id: str) -> tuple[bool, bytes]:
    marker = _START_PREFIX + run_id.encode("ascii") + b"\n"
    if stdout.startswith(marker):
        return True, stdout[len(marker) :]
    return False, stdout


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def interpret(stdout: bytes, returncode: int, *, run_id: str, host: str, timeout: int) -> Outcome:
    """sshの標準出力と終了コードから、このコマンドの結果を決める。"""
    started, stdout = _split_start(stdout, run_id)
    marker = _RESULT_PREFIX + run_id.encode("ascii") + b" "
    index = stdout.rfind(marker)
    result = None
    if index >= 0:
        payload = stdout[index + len(marker) :]
        line, separator, remainder = payload.partition(b"\n")
        if separator and not remainder:
            with contextlib.suppress(TypeError, ValueError):
                result = RemoteResult(**json.loads(line))
    output = _decode(stdout if index < 0 else stdout[:index])
    if (
        result is None
        or not isinstance(result.status, str)
        or (result.status == "completed" and not _is_integer(result.exit_code))
        or (result.status in {"not_started", "no_result"} and not _is_integer(result.task_result))
        or (result.status != "completed" and result.status not in _FAILURES)
    ):
        if started:
            return Outcome(
                _EXIT_FAILURE,
                output,
                f"{host}で開始を確認したが、実行結果は不明。再実行する前に接続先の結果を確かめる。",
            )
        if returncode == _EXIT_SSH_FAILURE:
            return Outcome(
                _EXIT_SSH_FAILURE,
                _decode(stdout),
                f"sshで{host}へ接続できなかった（終了コード255）。sshの設定と{host}の起動状態を確かめる。",
            )
        return Outcome(_EXIT_FAILURE, _decode(stdout), f"{host}から結果が返らなかった（sshの終了コード{returncode}）。")
    if result.status == "completed":
        assert result.exit_code is not None
        if 0 <= result.exit_code <= 255:
            return Outcome(result.exit_code, output)
        return Outcome(1, output, f"スクリプトの終了コード{result.exit_code}は0から255の範囲外のため、1を返す。")
    exit_code, template = _FAILURES[result.status]
    return Outcome(exit_code, output, template.format(host=host, timeout=timeout, **dataclasses.asdict(result)))


def _positive_int(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"1以上の秒数を指定する: {value}")
    return seconds


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")
