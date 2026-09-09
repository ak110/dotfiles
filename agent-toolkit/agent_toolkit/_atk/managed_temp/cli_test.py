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

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


from agent_toolkit._atk.managed_temp.test_support_test import *  # noqa: F403


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], "error: --pathを指定してください。現在の管理対象はありません。\n"),
        (
            [{"path": "/tmp/first"}],
            "error: --pathを指定してください。現在の管理対象は1件です。"
            "atk managed-temp cleanup --path /tmp/first を実行してください。\n",
        ),
        (
            [{"path": "/tmp/first"}, {"path": "/tmp/second"}],
            "error: --pathを指定してください。現在の管理対象の絶対パスを作成時刻の昇順で示します。\n/tmp/first\n/tmp/second\n",
        ),
    ],
)
def test_cleanup_without_path_reports_managed_targets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entries: list[subject._ManagedTempEntry],
    expected: str,
) -> None:
    """path欠落時は管理対象の件数に応じた再実行情報を示す。"""
    monkeypatch.setattr(subject, "list_managed_temp", lambda: entries)
    monkeypatch.setattr(subject, "cleanup_managed_temp", lambda *_args, **_kwargs: pytest.fail("cleanupを呼んだ"))

    assert subject.main(["cleanup"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == expected


@pytest.mark.parametrize(
    ("directory", "existing_owner", "full_open_error", "expected_handle", "owner_changed"),
    [
        (False, b"current-owner", None, 101, False),
        (True, b"administrator-owner", None, 101, True),
        (False, b"current-owner", subject._WINDOWS_ERROR_ACCESS_DENIED, 202, False),
        (True, b"current-owner", subject._WINDOWS_ERROR_ACCESS_DENIED, 202, False),
    ],
)
def test_secure_path_uses_adopted_handle_for_owner_check_and_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    directory: bool,
    existing_owner: bytes,
    full_open_error: int | None,
    expected_handle: int,
    owner_changed: bool,
) -> None:
    """採用ハンドルを所有者判定からセキュリティ更新まで再利用する契約を確認する。"""
    calls = _install_windows_security_doubles(
        monkeypatch,
        directory=directory,
        existing_owner=existing_owner,
        full_open_error=full_open_error,
    )

    subject._windows_secure_path(tmp_path / "target", directory=directory)

    full_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC | subject._WINDOWS_WRITE_OWNER
    minimal_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC
    expected_opens = [full_access, minimal_access] if full_open_error is not None else [full_access]
    expected_information = subject._WINDOWS_DACL_SECURITY_INFORMATION | subject._WINDOWS_PROTECTED_DACL_SECURITY_INFORMATION
    expected_owner = None
    if owner_changed:
        expected_information |= subject._WINDOWS_OWNER_SECURITY_INFORMATION
        expected_owner = b"current-owner"
    assert calls.opens == expected_opens
    assert calls.security_reads == [expected_handle]
    assert calls.updates == [(expected_handle, expected_information, expected_owner)]


@pytest.mark.parametrize("quarantine", [False, True])
def test_cli_resumes_an_interrupted_cleanup(tmp_path: pathlib.Path, quarantine: bool) -> None:
    """公開CLIは消費直後と隔離直後の中断を同じcleanupで回収する。"""
    env, state_root = _isolated_cli_environment(tmp_path)
    created = subprocess.run(
        [sys.executable, str(_SCRIPT), "create", "--prefix", "cli-resume"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    target = pathlib.Path(created.stdout.strip())
    registry = state_root / subject._registry_name(target)
    record = json.loads(registry.read_text(encoding="utf-8"))
    nonce = record["nonce"]
    consuming = registry.with_name(f"{registry.name}.consuming-{nonce}")
    registry.replace(consuming)
    quarantine_path = target.parent / f".agent-toolkit-cleanup-{nonce}"
    (target / "content.txt").write_text("remove", encoding="utf-8")
    if quarantine:
        target.replace(quarantine_path)

    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert created.returncode == 0
    assert cleaned.returncode == 0, cleaned.stderr
    assert not target.exists()
    assert not quarantine_path.exists()
    assert not registry.exists()
    assert not consuming.exists()
