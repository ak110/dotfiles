"""コマンドライン引数の解析、sshの実行、接続先が返した結果の解釈。

接続先では`remote.ps1`がsshのセッションのまま予定タスクを登録し、タスクの中で`runner.ps1`がスクリプトを実行する。
`remote.ps1`は標準出力へスクリプトの出力を書いた後、`SSH_UNELEVATED_RESULT:<実行ID>`で始まる行へ結果のJSONを書く。
"""

import argparse
import base64
import codecs
import dataclasses
import io
import json
import pathlib
import secrets
import subprocess
import sys
from collections.abc import Sequence
from importlib import resources

from pytools._internal.cli import enable_completion

# 接続先のPowerShellが、標準入力のbase64をUTF-8の本文へ戻して実行する起動部。
# sshの既定シェルがcmd.exeの場合の1行の上限（8191文字）に掛からないよう、本文は標準入力で渡す。
_BOOTSTRAP = (
    "$ProgressPreference='SilentlyContinue';"
    "Invoke-Expression ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())))"
)
_RESULT_PREFIX = b"\nSSH_UNELEVATED_RESULT:"
# 接続、タスクの登録、後始末に要する時間の見込み。上限にこれを足しても終わらないsshは打ち切る。
_SSH_MARGIN_SECONDS = 120
_DEFAULT_TIMEOUT_SECONDS = 1800
_EXIT_TIMEOUT = 124
_EXIT_FAILURE = 125
_EXIT_SSH_FAILURE = 255

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
        "スクリプトがPowerShellのプロセスごと終了させていないかを確かめる。",
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
        completed = subprocess.run(
            build_ssh_command(args.host),
            input=build_payload(script, run_id, args.timeout),
            capture_output=True,
            timeout=limit,
            check=False,
        )
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
            _decode(e.stdout or b""),
            f"sshが{limit}秒以内に終わらなかったため打ち切った。{args.host}に残った予定タスクと一時ファイルは次回の実行が片付ける。",
        )
    else:
        stderr = completed.stderr
        outcome = interpret(completed.stdout, completed.returncode, run_id=run_id, host=args.host, timeout=args.timeout)
    sys.stdout.write(outcome.output)
    sys.stdout.flush()
    sys.stderr.write(_decode(stderr))
    if outcome.diagnostic is not None:
        print(f"ssh-unelevated: {outcome.diagnostic}", file=sys.stderr)
    sys.exit(outcome.exit_code)


def build_ssh_command(host: str) -> list[str]:
    """接続先で起動部を実行するsshのコマンド行を返す。"""
    encoded = base64.b64encode(_BOOTSTRAP.encode("utf-16-le")).decode("ascii")
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


def interpret(stdout: bytes, returncode: int, *, run_id: str, host: str, timeout: int) -> Outcome:
    """sshの標準出力と終了コードから、このコマンドの結果を決める。"""
    marker = _RESULT_PREFIX + run_id.encode("ascii") + b" "
    index = stdout.rfind(marker)
    if index < 0:
        if returncode == _EXIT_SSH_FAILURE:
            return Outcome(
                _EXIT_SSH_FAILURE,
                _decode(stdout),
                f"sshで{host}へ接続できなかった（終了コード255）。sshの設定と{host}の起動状態を確かめる。",
            )
        return Outcome(_EXIT_FAILURE, _decode(stdout), f"{host}から結果が返らなかった（sshの終了コード{returncode}）。")
    output = _decode(stdout[:index])
    result = RemoteResult(**json.loads(stdout[index + len(marker) :].split(b"\n", 1)[0]))
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
