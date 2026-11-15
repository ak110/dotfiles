"""MCPと会話内mod間の期限付きリクエスト。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import pathlib
import re
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from agent_toolkit._common import host_homes
from agent_toolkit._common.atomic_file import atomic_write


def state_path(session_id: str, kind: str) -> pathlib.Path:
    """既存exitStatePathと同じ会話別ディレクトリを使う。"""
    if re.fullmatch(r"[A-Za-z0-9_-]+", session_id) is None:
        raise ValueError("会話IDが不正")
    if kind not in {"ui-request", "ui-response"}:
        raise ValueError("リクエスト種別が不正")
    # modと同じASCII和で固定64分割し、他会話の終了目印まで毎回列挙する処理を避ける。
    shard = f"{sum(map(ord, session_id)) % 64:02x}"
    return host_homes.claude_config_dir() / "agent-toolkit-function-hooks" / "ui-requests" / shard / f"{kind}-{session_id}.txt"


def request_path(session_id: str, kind: str, identifier: str) -> pathlib.Path:
    """各生成主体が固有のファイルを所有し、プロセスを越えた上書きをなくす。"""
    if re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
        raise ValueError("要求IDが不正")
    base = state_path(session_id, kind)
    return base.with_name(f"{base.stem}-{identifier}.txt")


def cleanup(session_id: str) -> None:
    """期限切れと、応答済みの非同期観測だけを回収する。"""
    # UI要求を作れない識別子には回収対象も無い。通常hookの後続観測は維持する。
    if re.fullmatch(r"[A-Za-z0-9_-]+", session_id) is None:
        return
    base = state_path(session_id, "ui-request")
    for path in base.parent.glob(f"{base.stem}-*.txt"):
        identifier = path.name[len(base.stem) + 1 : -4]
        if re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
            continue
        try:
            pending = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(pending, dict) or pending.get("id") != identifier:
                continue
            response = request_path(session_id, "ui-response", pending["id"])
            if pending["expires"] > time.time() and not (pending.get("detached") and response.exists()):
                continue
            path.unlink(missing_ok=True)
            response.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
    response_base = state_path(session_id, "ui-response")
    for path in response_base.parent.glob(f"{response_base.stem}-*.txt"):
        identifier = path.name[len(response_base.stem) + 1 : -4]
        if re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
            continue
        try:
            response = json.loads(path.read_text(encoding="utf-8"))
            if response.get("expires", path.stat().st_mtime + 120) <= time.time():
                path.unlink(missing_ok=True)
        except FileNotFoundError, json.JSONDecodeError:
            continue


def enqueue(
    session_id: str,
    operation: str,
    inputs: dict[str, Any],
    *,
    timeout: float = 120,
    detached: bool = True,
) -> tuple[str, float]:
    """確認を要するMCP呼出しと、設定hookの観測を同じ要求形式で配送する。"""
    cleanup(session_id)
    identifier = uuid.uuid4().hex
    expires = time.time() + timeout
    atomic_write(
        request_path(session_id, "ui-request", identifier),
        json.dumps(
            {
                "id": identifier,
                "operation": operation,
                "input": inputs,
                "expires": expires,
                "detached": detached,
                "created": str(time.monotonic_ns()),
            }
        ),
    )
    return identifier, expires


async def request(session_id: str, operation: str, inputs: dict[str, Any], *, timeout: float = 10) -> dict[str, Any]:
    """modの確認応答を待つ。未応答や不一致を成功へ変換しない。"""
    identifier, expires = enqueue(session_id, operation, inputs, timeout=timeout, detached=False)
    source = request_path(session_id, "ui-request", identifier)
    response = request_path(session_id, "ui-response", identifier)
    try:
        while time.time() < expires:
            try:
                result = json.loads(response.read_text(encoding="utf-8"))
            except FileNotFoundError, json.JSONDecodeError:
                result = None
            if isinstance(result, dict) and result.get("id") == identifier:
                if result.get("ok") is not True:
                    raise ValueError(str(result.get("reason", "modがリクエストを拒否した")))
                return result
            await asyncio.sleep(0.1)
        raise TimeoutError("会話内modの確認応答が期限内にない。表示・予約は成功として扱わない。")
    finally:
        for path in (source, response):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


def interactive_available(
    session_id: str,
    *,
    expected_version: str | None = None,
    context_check: Callable[[dict], bool] | None = None,
) -> bool:
    """実画面を確認し、要求された場合は稼働中の版の完全一致も必須にする。"""
    try:
        identifier, expires = enqueue(session_id, "interactive_available", {}, timeout=3, detached=False)
        source = request_path(session_id, "ui-request", identifier)
        response = request_path(session_id, "ui-response", identifier)
        try:
            while time.time() < expires:
                try:
                    result = json.loads(response.read_text(encoding="utf-8"))
                except FileNotFoundError, json.JSONDecodeError:
                    result = None
                if isinstance(result, dict) and result.get("id") == identifier:
                    return (
                        result.get("ok") is True
                        and result.get("interactive") is True
                        and (expected_version is None or result.get("version") == expected_version)
                        and (context_check is None or context_check(result))
                    )
                time.sleep(0.05)
            return False
        finally:
            source.unlink(missing_ok=True)
            response.unlink(missing_ok=True)
    except OSError, ValueError:
        return False


def main() -> int:
    """modが処理済み・期限切れと確認した一時要求を単発で回収する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup-session", required=True)
    args = parser.parse_args()
    cleanup(args.cleanup_session)
    print(json.dumps({"complete": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
