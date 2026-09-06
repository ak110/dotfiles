"""外部CLIのJSON応答を取得する共有実装。"""

import dataclasses
import json
import subprocess
from collections.abc import Callable
from typing import Any, Literal

FailureKind = Literal["timeout", "not-found", "decode", "exit", "json"]


@dataclasses.dataclass(frozen=True)
class Failure:
    """JSONコマンド失敗を呼び出し側の例外へ変換するための構造化情報。"""

    kind: FailureKind
    command: tuple[str, ...]
    timeout: float
    detail: str = ""
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


def run(
    command: list[str],
    timeout: float,
    *,
    error_factory: Callable[[Failure], Exception],
    strict_stderr: bool = True,
) -> Any:
    """コマンドを実行してJSONを返し、失敗は呼び出し側指定の例外へ変換する。"""
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise error_factory(Failure("timeout", tuple(command), timeout)) from exc
    except FileNotFoundError as exc:
        raise error_factory(Failure("not-found", tuple(command), timeout)) from exc
    try:
        stdout = result.stdout.decode("utf-8") if isinstance(result.stdout, bytes) else result.stdout
        stderr = (
            result.stderr.decode("utf-8", errors="strict" if strict_stderr else "backslashreplace")
            if isinstance(result.stderr, bytes)
            else result.stderr
        )
    except UnicodeDecodeError as exc:
        raise error_factory(Failure("decode", tuple(command), timeout, detail=str(exc))) from exc
    assert isinstance(stdout, str)
    assert isinstance(stderr, str)
    if result.returncode != 0:
        raise error_factory(
            Failure(
                "exit",
                tuple(command),
                timeout,
                returncode=result.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise error_factory(Failure("json", tuple(command), timeout, detail=str(exc), stdout=stdout)) from exc
