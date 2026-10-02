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
from agent_toolkit._atk.managed_temp import cli as cli_subject
from agent_toolkit._atk.managed_temp import inventory as inventory_subject

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


from agent_toolkit._atk.managed_temp.test_support_test import *  # noqa: F403


@pytest.mark.parametrize(
    ("entries", "expected_listed", "expected_operation"),
    [
        ([], [], None),
        ([{"path": "/tmp/first"}], [], "atk managed-temp cleanup --path /tmp/first"),
        (
            [{"path": "/tmp/first"}, {"path": "/tmp/second"}],
            ["/tmp/first", "/tmp/second"],
            "atk managed-temp cleanup --path",
        ),
    ],
)
def test_cleanup_without_path_reports_managed_targets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entries: list[subject._ManagedTempEntry],
    expected_listed: list[str],
    expected_operation: str | None,
) -> None:
    """path欠落時は管理対象の件数に応じた再実行情報を次の操作の行で示す。"""
    monkeypatch.setattr(subject, "list_managed_temp", lambda: entries)
    monkeypatch.setattr(subject, "cleanup_managed_temp", lambda *_args, **_kwargs: pytest.fail("cleanupを呼んだ"))

    assert subject.main(["cleanup"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert lines[0].startswith("失敗: --pathを指定してください。")
    assert lines[1 : 1 + len(expected_listed)] == expected_listed
    next_action = lines[1 + len(expected_listed)]
    assert next_action.startswith("次の操作: ")
    if expected_operation is not None:
        assert expected_operation in next_action


def test_cleanup_passes_force_remove_to_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開CLIの`--force-remove`を後始末処理へ渡す。"""
    calls: list[tuple[pathlib.Path, bool, bool]] = []

    def cleanup(path: pathlib.Path, *, recover_registry: bool, force_remove: bool) -> None:
        calls.append((path, recover_registry, force_remove))

    monkeypatch.setattr(cli_subject, "cleanup_managed_temp", cleanup)

    assert subject.main(["cleanup", "--path", str(tmp_path / "target"), "--force-remove"]) == 0
    assert calls == [(tmp_path / "target", False, True)]


def test_session_id_create_is_idempotent_and_cleanup_resolves_target(tmp_path: pathlib.Path) -> None:
    """同じsession_idの作成は既存pathを返し、session_id指定で回収できる。"""
    env, _ = _isolated_cli_environment(tmp_path)
    command = [sys.executable, str(_SCRIPT), "create", "--prefix", "session", "--session-id", "session-1"]

    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False, env=env)
    second = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False, env=env)
    target = pathlib.Path(first.stdout.strip())
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--session-id", "session-1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert second.stdout == first.stdout
    assert cleaned.returncode == 0, cleaned.stderr
    assert not target.exists()


def test_cli_writes_result_lines_under_a_non_utf8_stdio_encoding(tmp_path: pathlib.Path) -> None:
    """標準入出力に使う符号化を指定せず、日本語を扱えない環境でも結果行を送出する。"""
    env, _ = _isolated_cli_environment(tmp_path)
    env["PYTHONIOENCODING"] = "cp1252"

    created = subprocess.run(
        [sys.executable, str(_SCRIPT), "create", "--prefix", "session", "--session-id", "session-1"],
        capture_output=True,
        check=False,
        env=env,
    )
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--session-id", "session-1"],
        capture_output=True,
        check=False,
        env=env,
    )

    assert created.returncode == 0, created.stderr
    assert cleaned.returncode == 0, cleaned.stderr
    assert "成功: セッションのmanaged-tempを回収した" in cleaned.stdout.decode("utf-8")


def test_cleanup_rejects_path_with_session_id(tmp_path: pathlib.Path) -> None:
    """cleanupの対象指定はpathとsession_idのいずれか一方に限る。"""
    with pytest.raises(SystemExit) as captured:
        subject.main(
            [
                "cleanup",
                "--path",
                str(tmp_path / "target"),
                "--session-id",
                "session-1",
            ]
        )

    assert captured.value.code == 2


def test_create_with_session_root_returns_unregistered_child() -> None:
    """`--session-root`は親の配下へ個別登録を持たないmanaged-temp直下の作業ディレクトリを作成する。"""
    session_root = subject.create_managed_temp("session", session_id="session-1")

    assert subject.main(["create", "--prefix", "work", "--session-root", str(session_root)]) == 0

    entries = subject.list_managed_temp()
    assert len(entries) == 1
    assert entries[0]["path"] == str(session_root)
    subject.cleanup_managed_temp(session_root)


def test_cleanup_removes_child_of_registered_temp(capsys: pytest.CaptureFixture[str]) -> None:
    """`--session-root`で作成したmanaged-temp直下の作業ディレクトリは、個別の管理情報が無くても回収できる。"""
    session_root = subject.create_managed_temp("session", session_id="session-1")
    assert subject.main(["create", "--prefix", "work", "--session-root", str(session_root)]) == 0
    child = pathlib.Path(capsys.readouterr().out.splitlines()[-1])
    assert child.is_dir()
    assert not (child / _MARKER_NAME).exists()

    assert subject.main(["cleanup", "--path", str(child)]) == 0

    assert not child.exists()
    assert session_root.is_dir()
    assert str(session_root) in capsys.readouterr().err
    subject.cleanup_managed_temp(session_root)


def test_cleanup_rejects_path_outside_registered_temp(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """登録済み領域の配下に無い管理情報なしのパスは、現行どおり終了コード2で拒否する。"""
    outside = tmp_path / "outside"
    outside.mkdir()

    assert subject.main(["cleanup", "--path", str(outside)]) == 2

    assert outside.is_dir()
    assert "失敗: " in capsys.readouterr().err


@pytest.mark.parametrize("conflict", ["--awi=20260913-221409-001.md", "--session-id=session-2"])
def test_create_with_session_root_rejects_registration_options(conflict: str) -> None:
    """セッションのmanaged-temp直下の作業ディレクトリへ個別登録用の引数を併用しない。"""
    session_root = subject.create_managed_temp("session", session_id="session-1")

    assert subject.main(["create", "--prefix", "work", "--session-root", str(session_root), conflict]) == 2

    subject.cleanup_managed_temp(session_root)


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
        encoding="utf-8",
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
        encoding="utf-8",
        check=False,
        env=env,
    )

    assert created.returncode == 0
    assert cleaned.returncode == 0, cleaned.stderr
    assert not target.exists()
    assert not quarantine_path.exists()
    assert not registry.exists()
    assert not consuming.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIXの権限検証を再現する")
def test_create_with_unsafe_root_names_owner_and_mode_check(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """明示したrootの権限が不安全な場合は、所有者と0700の確認を次の操作として示す。"""
    root = tmp_path / "unsafe-root"
    root.mkdir()
    root.chmod(0o777)

    assert subject.main(["create", "--prefix", "work", "--root", str(root)]) == 2

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("失敗: ")
    assert lines[1].startswith("次の操作: ")
    assert "所有者" in lines[1]
    assert "0700" in lines[1]


def test_replacement_detected_during_cleanup_names_rerun_and_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """後始末中に置換を検出した失敗は、再実行と繰り返す場合の報告を次の操作として示す。"""
    target = subject.create_managed_temp("replaced")
    snapshots = iter([{}, {"changed": ("file", 0, 0)}])
    monkeypatch.setattr(inventory_subject, "_tree_snapshot", lambda _path: next(snapshots))

    assert subject.main(["cleanup", "--path", str(target)]) == 2

    next_action = next(line for line in capsys.readouterr().err.splitlines() if line.startswith("次の操作: "))
    assert "再実行" in next_action
    assert "ユーザーへ報告" in next_action
