from __future__ import annotations

import contextlib
import json
import os
import pathlib
import time
from typing import Any

from agent_toolkit._common import atomic_file

EFFORT_REQUEST_SUFFIX = ".effort-request"
RELEASE_REQUEST_SUFFIX = ".release-request"
_SUFFIXES = (EFFORT_REQUEST_SUFFIX, RELEASE_REQUEST_SUFFIX)

# 時刻の刻みが粗いファイルシステム（ext3は1秒、Windowsは最大15.6ミリ秒）では、列挙の直後に同じ刻みの中で置かれた要求が
# 更新時刻を変えない。刻みより十分古い更新時刻の一覧だけを再利用し、それより新しい間は毎回列挙し直す。
_TRUSTED_AGE_NS = 2_000_000_000

_LISTINGS: dict[str, tuple[tuple[int, int], tuple[str, ...]]] = {}


def request_paths(directory: pathlib.Path, suffix: str) -> list[pathlib.Path]:
    started = time.time_ns()
    try:
        status = os.stat(directory)
    except OSError:
        return []
    signature = (status.st_ino, status.st_mtime_ns)
    key = os.fspath(directory)
    cached = _LISTINGS.get(key)
    if cached is not None and cached[0] == signature:
        names = cached[1]
    else:
        try:
            with os.scandir(directory) as entries:
                names = tuple(sorted(entry.name for entry in entries if entry.name.endswith(_SUFFIXES)))
        except OSError:
            return []
        if status.st_mtime_ns < started - _TRUSTED_AGE_NS:
            _LISTINGS[key] = (signature, names)
        else:
            _LISTINGS.pop(key, None)
    return [directory / name for name in names if name.endswith(suffix)]


def respond(request: pathlib.Path, response_path: pathlib.Path, response: dict[str, Any]) -> None:
    try:
        packet = json.loads(request.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    if isinstance(packet, dict) and "request_id" in packet:
        if packet["request_id"] != request.stem:
            raise ValueError("要求IDがファイル名と一致しない")
        if "result" not in packet:
            atomic_file.atomic_write(request, json.dumps({**packet, "result": response}, ensure_ascii=False) + "\n")
        with contextlib.suppress(OSError):
            request.replace(_result_path(request))
        return

    # 旧要求側は待機終了で要求を先に消す。遅れて書いた応答だけを残さない。  # pyfltr: allow-comment
    atomic_file.atomic_write(response_path, json.dumps(response, ensure_ascii=False) + "\n")
    if not request.exists():
        response_path.unlink(missing_ok=True)


def write_request(path: pathlib.Path, packet: dict[str, Any]) -> None:
    atomic_file.atomic_write(path, json.dumps({**packet, "request_id": path.stem}, ensure_ascii=False) + "\n")


def _result_path(request: pathlib.Path) -> pathlib.Path:
    return request.with_suffix(f"{request.suffix}-result")


def pending_payload(path: pathlib.Path, response: pathlib.Path) -> dict[str, Any] | None:
    if response.exists():
        return None
    packet = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(packet, dict) or packet.get("version") != 1:
        return None
    if packet.get("request_id", path.stem) != path.stem:
        return None
    if "result" in packet:
        # 一時共有拒否の後は操作を繰り返さず、名前の更新だけを試す。  # pyfltr: allow-comment
        if packet.get("request_id") == path.stem:
            with contextlib.suppress(OSError):
                path.replace(_result_path(path))
        return None
    deadline = packet.get("deadline")
    if not isinstance(deadline, (float, int)) or deadline <= time.monotonic():
        return None
    return packet


def read_result(request: pathlib.Path, legacy_response: pathlib.Path) -> tuple[str | None, PermissionError | None]:
    denial = None
    for path in (request, _result_path(request)):
        text, current_denial = read_response(path)
        denial = current_denial or denial
        if text is not None:
            packet = json.loads(text)
            if isinstance(packet, dict) and packet.get("request_id") == request.stem and "result" in packet:
                return json.dumps(packet["result"], ensure_ascii=False), None
    response, legacy_denial = read_response(legacy_response)
    return response, legacy_denial or denial


def read_response(response_path: pathlib.Path) -> tuple[str | None, PermissionError | None]:
    """公開済みの応答を読み、本文と読取拒否を返す。

    応答が無い間と読取拒否の間は本文が`None`になり、呼出側が要求の期限内で次の巡回に読み直す。
    一時的な共有拒否はこの再読で吸収される。期限まで拒否が続いた場合に原因を報告できるよう、拒否は呼出側へ返す。
    """
    try:
        return response_path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, None
    except PermissionError as error:
        if error.filename is None:
            error.filename = str(response_path)
        return None, error


def denial_detail(response_path: pathlib.Path, denial: PermissionError | None) -> str:
    """期限まで応答を読めなかった理由の説明を返す。読取拒否が無ければ空文字列を返す。"""
    if denial is None:
        return ""
    return f"（応答ファイル{denial.filename or response_path}を読めなかった。最後のOSエラー: {denial}）"
