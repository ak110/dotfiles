"""gitサブプロセス実行の共有ラッパー。"""

import pathlib
import subprocess
import sys


def run(
    args: list[str],
    cwd: str | pathlib.Path,
    *,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """`git`へ引数列を渡し、共通のcwd指定で実行する。"""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
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
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    if forward_error_output and result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    if forward_error_output and result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    raise subprocess.CalledProcessError(
        result.returncode,
        ["git", *args],
        output=result.stdout,
        stderr=result.stderr,
    )


def output(args: list[str], cwd: str | pathlib.Path) -> str:
    """git標準出力を文字列として返し、失敗時は例外を送出する。"""
    result = run(args, cwd, check=True, capture_output=True, text=True)
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def optional_output(args: list[str], cwd: str | pathlib.Path, *, timeout: float) -> str | None:
    """git標準出力を返し、timeout又は非0終了時は`None`を返す。"""
    try:
        result = run(args, cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def lines(args: list[str], cwd: str | pathlib.Path) -> list[str] | None:
    """git標準出力を行配列で返し、実行不能又は非0終了時は`None`を返す。"""
    try:
        result = run(args, cwd, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not isinstance(result.stdout, str):
        return None
    return result.stdout.splitlines()
