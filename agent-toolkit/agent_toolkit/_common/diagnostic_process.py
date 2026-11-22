from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import typing

from agent_toolkit._common.diagnostic_redaction import redact_secrets


def redact_stream_line(line: str, private_key: bool) -> tuple[str, bool]:
    begins = re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", line) is not None
    ends = re.search(r"-----END [A-Z0-9 ]*PRIVATE KEY-----", line) is not None
    return ("[REDACTED]" if private_key else redact_secrets(line), (private_key or begins) and not ends)


def is_error_event(line: str) -> bool:
    """モデルCLIの構造化出力の1行が、会話本文ではなく失敗の診断を表すかを返す。

    記録側は該当行だけを秘匿して残し、失敗報告側は該当行だけを転載する。両者が同じ判定を使う。
    """
    try:
        event = json.loads(line)
    except ValueError:
        return False
    return isinstance(event, dict) and (
        event.get("is_error") is True
        or event.get("type") in {"error", "turn.failed"}
        or isinstance(event.get("error"), (dict, str))
    )


def command(argv: list[str]) -> list[str]:
    return [
        argv[0],
        "-c",
        f"import sys; sys.path.insert(0, {str(pathlib.Path(__file__).resolve().parents[2])!r}); "
        "from agent_toolkit._common.diagnostic_process import relay_diagnostics; sys.exit(relay_diagnostics(sys.argv[1:]))",
        *argv,
    ]


def relay_diagnostics(argv: list[str]) -> int:
    """両ストリームを同時に排出し、未秘匿の本文をディスクへ渡さない。"""

    def copy(stream: typing.IO[bytes], destination: typing.TextIO) -> None:
        private_key = False
        with stream:
            for raw in iter(stream.readline, b""):
                saved, private_key = redact_stream_line(raw.decode("utf-8", errors="replace"), private_key)
                destination.write(saved)
                if not saved.endswith("\n"):
                    destination.write("\n")
                destination.flush()

    try:
        with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            assert child.stdout is not None and child.stderr is not None
            threads = [
                threading.Thread(target=copy, args=(stream, destination))
                for stream, destination in ((child.stdout, sys.stdout), (child.stderr, sys.stderr))
            ]
            for thread in threads:
                thread.start()
            code = child.wait()
            for thread in threads:
                thread.join()
        if code < 0 and os.name != "nt":
            os.kill(os.getpid(), -code)
        return code
    except OSError as error:
        print(
            redact_secrets(f"{type(error).__name__}: {error}")
            + "。起動したコマンドの存在と実行権限を確認してから、元の操作を再実行してください。",
            file=sys.stderr,
        )
        return 1
