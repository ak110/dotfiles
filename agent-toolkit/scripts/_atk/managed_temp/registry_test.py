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

from _atk import managed_temp as subject
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


from _atk.managed_temp.test_support_test import *  # noqa: F403


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
        }
    ]


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
