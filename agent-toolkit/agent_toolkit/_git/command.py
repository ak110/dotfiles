"""`git`の子プロセスを起動する共通関数。

agent-toolkitの非テストのコードは`git`を直接起動せず、本モジュールの関数を通す。
`scripts/check_script_imports.py`が`_git/`配下の外での`["git", ...]`の起動を失敗にする。
テキストで受け取る出力は常にUTF-8として復号し、復号できないバイトは置換文字へ置き換える。
"""

import pathlib
import subprocess
import sys
import typing
from collections.abc import Mapping, Sequence

type _Cwd = str | pathlib.Path | None
type _Stream = int | typing.IO[typing.Any] | None


def command_line(args: Sequence[str]) -> list[str]:
    """`git`へ渡す引数列から、例外と記録へ載せるコマンドの全体を返す。"""
    return ["git", *args]


@typing.overload
def run(
    args: Sequence[str],
    cwd: _Cwd = None,
    *,
    check: bool = False,
    capture_output: bool = False,
    text: typing.Literal[True],
    timeout: float | None = None,
    input: str | None = None,  # noqa: A002  # pylint: disable=redefined-builtin
    env: Mapping[str, str] | None = None,
    stdout: _Stream = None,
    stderr: _Stream = None,
) -> subprocess.CompletedProcess[str]: ...


@typing.overload
def run(
    args: Sequence[str],
    cwd: _Cwd = None,
    *,
    check: bool = False,
    capture_output: bool = False,
    text: typing.Literal[False] = False,
    timeout: float | None = None,
    input: bytes | None = None,  # noqa: A002  # pylint: disable=redefined-builtin
    env: Mapping[str, str] | None = None,
    stdout: _Stream = None,
    stderr: _Stream = None,
) -> subprocess.CompletedProcess[bytes]: ...


def run(
    args: Sequence[str],
    cwd: _Cwd = None,
    *,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = None,
    input: str | bytes | None = None,  # noqa: A002  # pylint: disable=redefined-builtin
    env: Mapping[str, str] | None = None,
    stdout: _Stream = None,
    stderr: _Stream = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """`git`へ引数列を渡して起動し、完了した結果を返す。

    `cwd`を省略すると呼び出し元の作業ディレクトリで起動する。
    `check`・`timeout`・`input`・`env`・`stdout`・`stderr`は`subprocess.run`へそのまま渡し、
    終了コードの扱いと時間切れの例外は呼び出し側が決める。
    """
    return subprocess.run(
        command_line(args),
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        timeout=timeout,
        input=input,
        env=env,
        stdout=stdout,
        stderr=stderr,
    )


def run_quiet(
    args: list[str],
    cwd: str | pathlib.Path,
    *,
    forward_error_output: bool = True,
) -> None:
    """gitの出力を成功時は抑止し、失敗時は指定に従って標準エラーへ転送する。"""
    result = run(args, cwd, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return
    if forward_error_output and result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    if forward_error_output and result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    raise subprocess.CalledProcessError(
        result.returncode,
        command_line(args),
        output=result.stdout,
        stderr=result.stderr,
    )


def output(args: list[str], cwd: str | pathlib.Path) -> str:
    """git標準出力を文字列として返し、失敗時は例外を送出する。"""
    result = run(args, cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def optional_stdout(args: Sequence[str], cwd: _Cwd = None, *, timeout: float | None = None) -> str | None:
    """git標準出力を加工せずに返し、起動の失敗・時間切れ・非0終了では`None`を返す。"""
    try:
        result = run(args, cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def optional_output(args: Sequence[str], cwd: _Cwd, *, timeout: float) -> str | None:
    """git標準出力の前後の空白を除いて返し、時間切れと非0終了では`None`を返す。起動の失敗は例外のまま送出する。"""
    try:
        result = run(args, cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def lines(args: Sequence[str], cwd: _Cwd = None) -> list[str] | None:
    """git標準出力を行配列で返し、実行不能または非0終了時は`None`を返す。"""
    try:
        result = run(args, cwd, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()
