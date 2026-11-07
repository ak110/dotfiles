"""回収失敗診断のAPI境界と、自身が起動するWindows保持者の識別を検証する。"""

import ctypes
import errno
import json
import os
import pathlib
import subprocess
import sys
import time
import types
from collections.abc import Callable
from typing import cast
from unittest import mock

import pytest

from agent_toolkit._common import process_tree
from agent_toolkit._testing import pytest_plugin, resource_diagnostics, slot_yield


@pytest.mark.parametrize(
    "outcome", ["holder", "empty", "open-denied", "query-denied", "overflow", "incomplete", "raise", "close-failed"]
)
def test_file_holders_preserves_api_state_and_closes_handle(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    """ULONG件数・HANDLE幅の応答を読み、失敗や空集合と自診断PIDを区別してhandleを閉じる。"""
    # Microsoft Learn NtQueryInformationFile/FILE_INFORMATION_CLASSと
    # System Informer phnt/ntioapi.hのFILE_PROCESS_IDS_USING_FILE_INFORMATION（2026-11-02）。
    kernel = types.SimpleNamespace(
        CreateFileW=mock.Mock(return_value=ctypes.c_void_p(-1).value if outcome == "open-denied" else 123),
        CloseHandle=mock.Mock(return_value=0 if outcome == "close-failed" else 1),
    )
    foreign_pid = os.getpid() + 1

    def query(handle, io_status, buffer, length, information_class, **_kwargs):
        assert handle == 123 and information_class == 47
        assert length == len(buffer)
        if outcome == "raise":
            raise OSError("模擬API失敗")
        if outcome in ("query-denied", "overflow"):
            return ctypes.c_int32(0xC0000022 if outcome == "query-denied" else 0x80000005).value
        pids = [os.getpid(), foreign_pid] if outcome == "holder" else []
        ctypes.c_uint32.from_buffer(buffer).value = len(pids)
        # HANDLE配列はpointer境界に配置される（32bitでoffset=4、64bitでoffset=8）。
        offset = ctypes.sizeof(ctypes.c_void_p)
        for index, pid in enumerate(pids):
            ctypes.c_void_p.from_buffer(buffer, offset + index * ctypes.sizeof(ctypes.c_void_p)).value = pid
        byte_count = offset + len(pids) * ctypes.sizeof(ctypes.c_void_p) if pids else 4
        ctypes.cast(io_status, ctypes.POINTER(ctypes.c_size_t))[1] = byte_count
        if outcome == "incomplete":
            ctypes.c_uint32.from_buffer(buffer).value = 0xFFFFFFFF
        return 0

    native = types.SimpleNamespace(NtQueryInformationFile=mock.Mock(side_effect=query))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_kwargs: kernel if name == "kernel32" else native, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(process_tree, "identity", lambda pid, **_kwargs: {"pid": pid, "created_at": 1.0, "ppid": 12})
    if outcome in ("raise", "close-failed"):
        with pytest.raises(OSError, match="模擬API失敗" if outcome == "raise" else "handleを閉じられません"):
            resource_diagnostics.file_holders(pathlib.Path("failure"))
    else:
        result = resource_diagnostics.file_holders(pathlib.Path("failure"))
        if outcome in ("holder", "empty"):
            assert result["state"] == "success"
            assert result["diagnostic_handle_pid"] == os.getpid()
            assert result["diagnostic_pid_observed"] == (outcome == "holder")
            assert [item["pid"] for item in result["holders"]] == ([foreign_pid] if outcome == "holder" else [])
        else:
            assert result["state"] == "unavailable" and "holders" not in result
            if outcome == "open-denied":
                assert result["winerror"] == 5
            elif outcome in ("query-denied", "overflow"):
                assert result["ntstatus"] == ("0xc0000022" if outcome == "query-denied" else "0x80000005")
    if outcome == "open-denied":
        kernel.CloseHandle.assert_not_called()
    else:
        kernel.CloseHandle.assert_called_once_with(123)


def test_non_windows_does_not_query_native_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """非Windowsの取得不能を、成功した空集合に変換しない。"""
    monkeypatch.setattr(sys, "platform", "linux")
    assert resource_diagnostics.file_holders(pathlib.Path("failure")) == {"state": "unsupported", "platform": "linux"}


def test_diagnostic_api_failure_keeps_original_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """API呼出し自体の例外も採取不能として、元の失敗パスと分離して記録する。"""
    target = tmp_path / "locked"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(resource_diagnostics, "file_holders", mock.Mock(side_effect=OSError("診断API失敗")))
    resource_diagnostics.report_cleanup_failure(
        OSError(errno.EACCES, "使用中", str(target)), directory=tmp_path, before=[], after=[]
    )
    record = json.loads(capsys.readouterr().err.removeprefix("resource-diagnostics: "))
    assert record["failures"][0]["path"] == str(target)
    assert record["failures"][0]["ownership"] == {"state": "unavailable", "error": "OSError"}


@pytest.mark.parametrize("failure_stage,diagnostic_failure", [("delete", False), ("delete", True), ("reclaim", False)])
def test_pytest_cleanup_preserves_failure_and_reports_cause_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_stage: str,
    diagnostic_failure: bool,
) -> None:
    """pytestの登録済みcleanupを通し、原因鎖のパスだけの照会と元例外の伝播を確認する。"""
    target = tmp_path / "locked"
    original = RuntimeError("元の回収失敗")
    if failure_stage == "delete":
        original.__cause__ = OSError(errno.EACCES, "使用中", str(target))
    callbacks: list[Callable[[], None]] = []
    config = types.SimpleNamespace(
        rootpath=tmp_path,
        pluginmanager=pytest.PytestPluginManager(),
        option=types.SimpleNamespace(basetemp=None),
        getoption=lambda *_args, **_kwargs: None,
        # `pytest_configure`が読むini設定は、登録時の値（譲渡の累計時間の上限）を返す。
        getini=lambda _name: str(slot_yield.DEFAULT_YIELD_BUDGET),
        add_cleanup=callbacks.append,
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(pytest_plugin.managed_temp, "create_managed_temp", lambda *_args, **_kwargs: tmp_path / "owned")
    monkeypatch.setattr(process_tree, "collect_descendants", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        process_tree,
        "reclaim_descendants",
        mock.Mock(side_effect=original if failure_stage == "reclaim" else None, return_value=[]),
    )
    cleanup = mock.Mock(side_effect=original)
    monkeypatch.setattr(pytest_plugin.managed_temp, "cleanup_managed_temp", cleanup)
    holders = mock.Mock(return_value={"state": "unavailable", "winerror": 5})
    monkeypatch.setattr(resource_diagnostics, "file_holders", holders)
    if diagnostic_failure:
        monkeypatch.setattr(
            resource_diagnostics,
            "datetime",
            types.SimpleNamespace(
                UTC=None, datetime=types.SimpleNamespace(now=mock.Mock(side_effect=RuntimeError("診断失敗")))
            ),
        )
    pytest_plugin.pytest_configure(cast(pytest.Config, config))
    with pytest.raises(RuntimeError) as error:
        callbacks[0]()
    assert error.value is original
    if diagnostic_failure:
        diagnostic = capsys.readouterr().err
        assert "unavailable=RuntimeError" in diagnostic
        assert "元の回収例外のtraceback" in diagnostic and "原因分析担当へ渡せ" in diagnostic
    if failure_stage == "reclaim":
        cleanup.assert_not_called()
    if not diagnostic_failure:
        line = next(line for line in capsys.readouterr().err.splitlines() if line.startswith("resource-diagnostics: "))
        record = json.loads(line.removeprefix("resource-diagnostics: "))
        assert "元の回収例外のtraceback" in record["next_action"]
        if failure_stage == "delete":
            holders.assert_called_once_with(target)
            assert record["failures"][0]["path"] == str(target)
            assert record["failures"][0]["ownership"]["state"] == "unavailable"
        else:
            holders.assert_not_called()
            assert not record["failures"] and record["path_state"] == "unavailable"
        assert record["before"][0]["pid"] == os.getpid()
        assert record["after"][0]["pid"] == os.getpid()


@pytest.mark.skipif(sys.platform != "win32", reason="Windowsのファイル共有違反とネイティブAPIを検証する")
def test_windows_owned_holder_is_identified_and_released(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], host_environ: Callable[[], dict[str, str]]
) -> None:
    """所有子の実PIDと失敗パスを対応付け、子の終了後に削除と診断handleの解放を確認する。"""
    target = tmp_path / "locked.txt"
    target.write_text("保持試験", encoding="utf-8")
    ready = tmp_path / "ready"
    script = tmp_path / "holder.py"
    script.write_text(
        "import os, pathlib, sys\n"
        "with open(sys.argv[1], 'rb'):\n"
        "    ready = pathlib.Path(sys.argv[2])\n"
        "    staging = ready.with_suffix('.tmp')\n"
        "    staging.write_text(str(os.getpid()), encoding='utf-8')\n"
        "    staging.replace(ready)\n"
        "    sys.stdin.buffer.read(1)\n",
        encoding="utf-8",
    )
    with process_tree.process_scope(
        [sys.executable, str(script), str(target), str(ready)],
        stdin=subprocess.PIPE,
        env=host_environ(),
    ) as child:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert child.poll() is None, "保持用の子が準備前に終了した"
            assert time.monotonic() < deadline, "保持用の子が準備完了を通知しなかった"
            time.sleep(0.01)
        holder_pid = int(ready.read_text(encoding="utf-8"))
        before = [process_tree.identity(holder_pid)]
        with pytest.raises(OSError) as failure:
            target.unlink()
        assert getattr(failure.value, "winerror", None) == 32
        resource_diagnostics.report_cleanup_failure(
            failure.value,
            directory=tmp_path,
            before=before,
            after=[process_tree.identity(holder_pid)],
        )
        record = json.loads(capsys.readouterr().err.removeprefix("resource-diagnostics: "))
        observed = record["failures"][0]
        assert observed["path"] == str(target) and observed["winerror"] == 32
        ownership = observed["ownership"]
        assert ownership["state"] == "success", ownership
        holder = next(item for item in ownership["holders"] if item["pid"] == holder_pid)
        assert holder["created_at"] == before[0]["created_at"]
        assert holder["ppid"] == before[0]["ppid"]
        assert ownership["diagnostic_handle_pid"] == os.getpid()
        assert child.stdin is not None
        child.stdin.write(b"x")
        child.stdin.flush()
        child.wait(timeout=30)
    target.unlink()
    assert not target.exists()
    print("Windows保持試験: " + json.dumps(record, ensure_ascii=True), flush=True)
