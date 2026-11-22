import json
import os
import pathlib
import re
import time
import uuid
from collections.abc import Mapping, MutableMapping
from typing import Any

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import mcp_environment, session_registry
from agent_toolkit._common.atomic_file import atomic_write


def _request(environ: Mapping[str, str]) -> tuple[pathlib.Path, str] | None:
    raw = json.loads(environ.get(mcp_environment.SESSION_START_INPUT_ENV, "{}"))
    if not isinstance(raw, dict) or "identity" not in raw:
        return None
    request = raw["identity"]
    if not isinstance(request, dict) or set(request) != {"path", "nonce"}:
        raise ValueError("起動時識別の入力が不正")
    if not isinstance(request["nonce"], str) or not isinstance(request["path"], str):
        raise ValueError("起動時識別の保存先とnonceには文字列が必要")
    nonce = str(uuid.UUID(request["nonce"]))
    path = pathlib.Path(request["path"])
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.resolve().is_relative_to(managed_temp.temp_root_path().resolve())
    ):
        raise ValueError("起動時識別の保存先は管理対象の通常ファイルで指定する")
    return path, nonce


def prepare(environ: MutableMapping[str, str]) -> pathlib.Path:
    path = managed_temp.create_managed_temp("startup-identity") / "identity.json"
    nonce = str(uuid.uuid4())
    owner = session_registry.ProcessIdentity.of(os.getpid())
    if owner is None:
        raise ValueError("起動側のPID世代を取得できない")
    atomic_write(path, json.dumps({"nonce": nonce, "owner": owner.payload(), "ack": False}))
    startup = json.loads(environ.get(mcp_environment.SESSION_START_INPUT_ENV, "{}"))
    startup["identity"] = {"path": str(path), "nonce": nonce}
    environ[mcp_environment.SESSION_START_INPUT_ENV] = json.dumps(startup, ensure_ascii=False)
    return path


def read(environ: Mapping[str, str]) -> dict[str, Any] | None:
    request = _request(environ)
    if request is None:
        return None
    path, nonce = request
    response = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(response, dict) or response.get("nonce") != nonce:
        raise ValueError("起動時識別の応答nonceが一致しない")
    if not isinstance(response.get("ack"), bool):
        raise ValueError("起動時識別のackにはboolが必要")
    if identifier := response.get("session_id"):
        if not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9_-]+", identifier) is None:
            raise ValueError("起動時識別の会話IDが不正")
        if not isinstance(response.get("cwd"), str) or not pathlib.Path(response["cwd"]).is_absolute():
            raise ValueError("起動時識別のcwdが不正")
    return response


def acknowledge(environ: Mapping[str, str], *, resume_only: bool | None = None) -> None:
    request = _request(environ)
    response = read(environ)
    if request is None or response is None:
        return
    response["ack"] = True
    if resume_only is not None:
        response["resume_only"] = resume_only
    atomic_write(request[0], json.dumps(response, ensure_ascii=False))


def capture(payload: dict[str, Any], host: str, environ: MutableMapping[str, str]) -> None:
    """SessionStartの実ID/cwdを書き戻し、有限期限内に所有の確定を待つ。"""
    request = _request(environ)
    response = read(environ)
    if request is None or response is None:
        return
    identifier, cwd = payload.get("session_id"), payload.get("cwd")
    if not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9_-]+", identifier) is None:
        raise ValueError("SessionStartの会話IDを確認できない")
    if not isinstance(cwd, str) or not pathlib.Path(cwd).is_absolute():
        raise ValueError("SessionStartのcwdを確認できない")
    response.update(session_id=identifier, cwd=cwd, host=host)
    atomic_write(request[0], json.dumps(response, ensure_ascii=False))
    owner = session_registry.ProcessIdentity.from_payload(response["owner"])
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and owner.exists():
        acknowledged = read(environ)
        if acknowledged is not None and acknowledged.get("ack") is True:
            if acknowledged.get("resume_only"):
                startup = json.loads(environ[mcp_environment.SESSION_START_INPUT_ENV])
                startup["resume_only"] = True
                environ[mcp_environment.SESSION_START_INPUT_ENV] = json.dumps(startup)
            return
        time.sleep(0.1)
    raise ValueError("picker選択後の所有・manifest確定を期限内に確認できない")
