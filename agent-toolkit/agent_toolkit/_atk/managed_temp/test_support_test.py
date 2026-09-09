# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
"""_managed_tempの管理対象一時ディレクトリ境界を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime
import json
import os
import pathlib
import stat
import subprocess
import sys
import typing

import pytest

from agent_toolkit._atk import managed_temp as subject

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


class _WindowsSecurityCalls(typing.NamedTuple):
    opens: list[int]
    security_reads: list[int]
    updates: list[tuple[int, int, bytes | None]]


def _install_windows_security_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    directory: bool,
    existing_owner: bytes,
    full_open_error: int | None,
) -> _WindowsSecurityCalls:
    """Windows security更新のAPI境界を決定論的な記録関数へ置換する。"""
    opens: list[int] = []
    security_reads: list[int] = []
    updates: list[tuple[int, int, bytes | None]] = []
    full_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC | subject._WINDOWS_WRITE_OWNER

    @contextlib.contextmanager
    def fake_path_handle(
        path: pathlib.Path,
        access: int,
        **_kwargs: object,
    ) -> typing.Iterator[tuple[int, subject._ByHandleFileInformation]]:
        opens.append(access)
        if access == full_access and full_open_error is not None:
            raise subject._WindowsHandleOpenError("handle open failed", path, full_open_error)
        information = subject._ByHandleFileInformation()
        information.attributes = subject._WINDOWS_FILE_ATTRIBUTE_DIRECTORY if directory else 0
        handle = 202 if access == subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC else 101
        yield handle, information

    def fake_current_sid(**_kwargs: object) -> str:
        return "S-1-current"

    def fake_sid_bytes(_sid_text: str, **_kwargs: object) -> bytes:
        return b"current-owner"

    def fake_acl_buffer(
        _path: pathlib.Path,
        _aces: tuple[subject._WindowsAce, ...],
        **_kwargs: object,
    ) -> object:
        return object()

    def fake_security_from_handle(
        handle: int,
        _information: subject._ByHandleFileInformation,
        _path: pathlib.Path,
        **_kwargs: object,
    ) -> subject._WindowsSecurity:
        security_reads.append(handle)
        return subject._WindowsSecurity(existing_owner, True, True, directory, ())

    def fake_equal_sids(first: bytes, second: bytes, **_kwargs: object) -> bool:
        return first == second

    def fake_set_security(
        handle: int,
        _path: pathlib.Path,
        security_information: int,
        owner_sid: bytes | None,
        _acl_buffer: object,
        **_kwargs: object,
    ) -> None:
        updates.append((handle, security_information, owner_sid))

    monkeypatch.setattr(subject, "_windows_path_handle", fake_path_handle)
    monkeypatch.setattr(subject, "_windows_current_sid", fake_current_sid)
    monkeypatch.setattr(subject, "_windows_sid_bytes", fake_sid_bytes)
    monkeypatch.setattr(subject, "_windows_acl_buffer", fake_acl_buffer)
    monkeypatch.setattr(subject, "_windows_security_from_handle", fake_security_from_handle)
    monkeypatch.setattr(subject, "_windows_equal_sids", fake_equal_sids)
    monkeypatch.setattr(subject, "_windows_set_security", fake_set_security)
    return _WindowsSecurityCalls(opens, security_reads, updates)


@pytest.fixture(autouse=True)
def isolated_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部真正性状態を各テストの専用領域へ分離する。"""
    monkeypatch.setattr(subject, "_state_root_path", lambda: tmp_path / "external-state")


def _path_state(path: pathlib.Path) -> tuple[object, ...]:
    """pathの種別、内容、所有・権限状態を比較可能な値で返す。"""
    metadata = path.lstat()
    content = path.read_bytes() if path.is_file() else None
    if os.name == "nt":
        security: object = subject._windows_security_descriptor(path)
    else:
        security = (metadata.st_uid, stat.S_IMODE(metadata.st_mode))
    return stat.S_IFMT(metadata.st_mode), content, security


def _path_sort_key(path: pathlib.Path) -> str:
    """pathの安定した並べ替えキーを返す。"""
    return str(path)


def _managed_state(target: pathlib.Path, registry: pathlib.Path) -> tuple[object, ...]:
    """対象ツリー、マーカーファイル、外部の登録簿の実在・内容・権限を取得する。"""
    tree = tuple(
        (str(path.relative_to(target)), _path_state(path)) for path in sorted((target, *target.rglob("*")), key=_path_sort_key)
    )
    return tree, _path_state(target / _MARKER_NAME), _path_state(registry)


def _replace_registry(target: pathlib.Path, transform: typing.Callable[[dict[str, object]], None]) -> None:
    """登録簿だけへ改変を保存し、登録ファイル名と`path`の対応を検証可能にする。"""
    registry = subject._registry_path(target)
    record = json.loads(registry.read_text(encoding="utf-8"))
    transform(record)
    registry.write_text(json.dumps(record), encoding="utf-8")
    if os.name == "posix":
        registry.chmod(0o600)


def _replace_records(target: pathlib.Path, transform: typing.Callable[[dict[str, object]], None]) -> None:
    """マーカーファイルと登録簿へ同じ改変を保存してデータ契約を検証可能にする。"""
    marker = target / _MARKER_NAME
    registry = subject._registry_path(target)
    for path in (marker, registry):
        record = json.loads(path.read_text(encoding="utf-8"))
        transform(record)
        path.write_text(json.dumps(record), encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)


def _interrupt_cleanup(target: pathlib.Path, *, quarantine: bool = False) -> tuple[pathlib.Path, pathlib.Path]:
    """登録を消費途中へ移し、必要なら実体も隔離した状態を再現する。"""
    registry = subject._registry_path(target)
    record = subject._load_private_json(registry)
    nonce = typing.cast(str, record["nonce"])
    consuming = registry.with_name(f"{registry.name}.consuming-{nonce}")
    registry.replace(consuming)
    quarantine_path = target.parent / f".agent-toolkit-cleanup-{nonce}"
    if quarantine:
        target.replace(quarantine_path)
    return consuming, quarantine_path


def _registry_recovery_is_accepted(target: pathlib.Path) -> bool:
    """`--recover-registry`が当該管理対象の後始末を受理したかを返す。"""
    try:
        subject.cleanup_managed_temp(target, recover_registry=True)
    except subject.ManagedTempError:
        return False
    return True


def _set_tree_mtime(path: pathlib.Path, timestamp_ns: int) -> None:
    """対象ツリー全体の最終更新日時を同じ値へ固定する。"""
    for entry in path.rglob("*"):
        os.utime(entry, ns=(timestamp_ns, timestamp_ns), follow_symlinks=False)
    os.utime(path, ns=(timestamp_ns, timestamp_ns), follow_symlinks=False)


def _isolated_cli_environment(tmp_path: pathlib.Path) -> tuple[dict[str, str], pathlib.Path]:
    """CLI subprocessの一時領域と外部状態をOS別の専用領域へ分離する。"""
    env = os.environ.copy()
    for name in ("TMPDIR", "TEMP", "TMP"):
        env[name] = str(tmp_path)
    if os.name == "nt":
        env["LOCALAPPDATA"] = str(tmp_path / "local-app-data")
        state_root = tmp_path / "local-app-data" / "agent-toolkit" / "managed-temp"
    else:
        env["XDG_STATE_HOME"] = str(tmp_path / "state")
        state_root = tmp_path / "state" / "agent-toolkit" / "managed-temp"
    return env, state_root


__all__ = [
    "_MARKER_NAME",
    "_SCRIPT",
    "_WindowsSecurityCalls",
    "_install_windows_security_doubles",
    "_interrupt_cleanup",
    "_isolated_cli_environment",
    "_managed_state",
    "_path_sort_key",
    "_path_state",
    "_registry_recovery_is_accepted",
    "_replace_records",
    "_replace_registry",
    "_set_tree_mtime",
    "isolated_state_root",
]
