# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
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
from agent_toolkit._atk.managed_temp import registry as registry_subject

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


from agent_toolkit._atk.managed_temp.test_support_test import *  # noqa: F403


def test_default_root_path_uses_platform_cache_on_posix(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """POSIXの既定rootをユーザーキャッシュ取得経路の配下へ置く。"""
    cache = tmp_path / "cache"
    monkeypatch.setattr(registry_subject.sys, "platform", "linux")
    monkeypatch.setattr(registry_subject.platformdirs, "user_cache_dir", lambda *_args, **_kwargs: str(cache))

    assert registry_subject._temp_root_path() == cache / "managed-temp"


def test_default_root_path_uses_local_app_data_without_cache_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Windowsの既定rootへplatformdirs固有のCache階層を加えない。"""
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setattr(registry_subject.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(
        registry_subject.platformdirs,
        "user_cache_dir",
        lambda *_args, **_kwargs: pytest.fail("Windowsではplatformdirsを呼ばない"),
    )

    assert registry_subject._temp_root_path() == local_app_data / "agent-toolkit" / "managed-temp"


def test_list_managed_temp_returns_validated_jsonl_record(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`list`は登録簿ではなく真正性検証済みの領域だけを返す。"""
    monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
    target = subject.create_managed_temp("publish-group")

    created_at = subject._load_private_json(subject._registry_path(target))["created_at"]
    assert subject.list_managed_temp("publish-group") == [
        {
            "path": str(target),
            "prefix": "publish-group",
            "created_at": created_at,
            "awis": [],
            "session_id": None,
            "session_owner": None,
        }
    ]


def test_schema_4_record_remains_valid(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """schema 5の追加後も既存のschema 4レコードを検証できる。"""
    monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
    target = subject.create_managed_temp("schema-four")

    def downgrade(record: dict[str, object]) -> None:
        record["schema_version"] = 4
        record.pop("session_id")
        record.pop("session_owner")

    _replace_records(target, downgrade)

    assert subject.validate_managed_temp(target) == target
    assert subject.list_managed_temp("schema-four")[0]["session_id"] is None


def test_session_owner_uses_pid_and_start_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """セッション所有者はPIDだけでなく開始トークンを組にして記録する。"""
    monkeypatch.setattr(subject, "_process_start_token", lambda pid: f"started-{pid}")

    target = subject.create_managed_temp("session", session_id="session-1", owner_pid=321)

    entry = subject.list_managed_temp(session_id="session-1")[0]
    assert entry["session_owner"] == {"pid": 321, "start_token": "started-321"}
    assert subject._session_owner_status(entry["session_owner"]) == "alive"
    subject.cleanup_managed_temp(target)


def test_session_owner_detects_reused_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じPIDの別プロセスを元のセッションが生存中とは判定しない。"""
    monkeypatch.setattr(subject, "_process_start_token", lambda _pid: "new-start")

    assert subject._session_owner_status({"pid": 321, "start_token": "old-start"}) == "dead"


def test_secure_path_does_not_fallback_for_other_open_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """ACCESS_DENIED以外のfull access取得失敗をminimal accessへ変換しない。"""
    calls = _install_windows_security_doubles(
        monkeypatch,
        directory=False,
        existing_owner=b"current-owner",
        full_open_error=32,
    )

    with pytest.raises(subject._WindowsHandleOpenError) as captured:
        subject._windows_secure_path(tmp_path / "target", directory=False)

    full_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC | subject._WINDOWS_WRITE_OWNER
    assert captured.value.error_code == 32
    assert calls.opens == [full_access]
    assert not calls.security_reads
    assert not calls.updates


def test_cli_round_trip_uses_exit_codes(tmp_path: pathlib.Path) -> None:
    """CLI正常系と修正可能エラーの終了コードを確認する。"""
    env, _ = _isolated_cli_environment(tmp_path)
    created = subprocess.run(
        [sys.executable, str(_SCRIPT), "create", "--prefix", "cli-test"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    target = pathlib.Path(created.stdout.strip())
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    rejected = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert created.returncode == 0
    assert cleaned.returncode == 0
    assert rejected.returncode == 2
    assert not target.exists()
