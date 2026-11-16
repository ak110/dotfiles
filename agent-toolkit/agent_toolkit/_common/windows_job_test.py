"""Windows実APIで、列挙を使わない所属回収と明示離脱を検証する。"""

import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_toolkit._common.windows_job import CREATE_SUSPENDED, WindowsJob
from agent_toolkit._testing import late_descendant


@pytest.mark.parametrize("in_job,limits", [(False, 0), (True, 0), (True, 0x800), (True, 0x1000)])
def test_current_job_limits_distinguish_no_job_from_restricted_job(in_job, limits):
    """Job非所属と離脱不可を区別し、外側Jobの制限を変更せず返す。"""

    def membership(_process, _job, value):
        value._obj.value = in_job  # pylint: disable=protected-access
        return True

    def query(handle, _kind, value, _size, _returned):
        assert handle is None
        value._obj.basic.flags = limits  # pylint: disable=protected-access
        return True

    job = WindowsJob.__new__(WindowsJob)
    job._kernel = SimpleNamespace(  # pylint: disable=protected-access
        GetCurrentProcess=lambda: 123, IsProcessInJob=membership, QueryInformationJobObject=query
    )
    assert job.current_job_limit_flags() == (limits if in_job else None)


@pytest.mark.parametrize("operation", ["IsProcessInJob", "QueryInformationJobObject"])
def test_current_job_query_failure_is_not_treated_as_unrestricted(operation):
    """照会失敗を非所属や離脱許可へ変換しない。"""

    def membership(_process, _job, value):
        value._obj.value = True  # pylint: disable=protected-access
        return operation != "IsProcessInJob"

    job = WindowsJob.__new__(WindowsJob)
    job._kernel = SimpleNamespace(  # pylint: disable=protected-access
        GetCurrentProcess=lambda: 123, IsProcessInJob=membership, QueryInformationJobObject=lambda *_: False
    )
    job._ctypes = SimpleNamespace(WinError=lambda _: OSError("injected query failure"), get_last_error=lambda: 5)  # pylint: disable=protected-access
    with pytest.raises(OSError, match="injected query failure"):
        job.current_job_limit_flags()


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Jobの所属・離脱と実際のプロセス回収を検証する")
def test_job_reclaims_orphan_and_preserves_explicit_breakaway(tmp_path, host_environ):
    """短命な親の子を回収し、明示離脱した短命コマンドの成功を保持する。"""
    script = tmp_path / "inner.py"
    outside = tmp_path / "outside.txt"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        "subprocess.run([sys.executable, '-c', "
        '\'import pathlib, sys; path = pathlib.Path(sys.argv[1]); pending = path.with_suffix(".tmp"); '
        'pending.write_text("outside", encoding="utf-8"); pending.replace(path)\', sys.argv[1]], '
        "creationflags=0x01000208, check=True, timeout=5)\n" + late_descendant.held_source(tmp_path / "job.release"),
        encoding="utf-8",
    )
    with (
        WindowsJob() as job,
        subprocess.Popen(
            late_descendant.cli_command(tmp_path, str(script), str(outside)),
            creationflags=CREATE_SUSPENDED,
            env=host_environ(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as root,
    ):
        try:
            # CPythonのPopenが元世代を保持するhandleを使い、PIDの再解決による所属登録を行わない。
            # _handleはWindows版CPythonの属性であり、Linux上のPylintからは解決できない。
            job.assign_suspended(
                int(cast(Any, root)._handle),  # noqa: SLF001  # pylint: disable=protected-access,no-member
                root.pid,
            )
            late_descendant.wait_spawner(tmp_path)
            late_descendant.trigger(tmp_path)
            deadline = time.monotonic() + 10
            while not outside.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("明示離脱したコマンドが完了しませんでした")
                time.sleep(0.01)
            assert outside.read_text(encoding="utf-8") == "outside"
            job.terminate()
            root.wait(timeout=10)
            late_descendant.assert_released(tmp_path)
        finally:
            # Popen.__exit__が待つ前に、失敗時もJobの全所属を停止する。
            try:
                job.terminate()
            finally:
                try:
                    job.close()
                finally:
                    if root.poll() is None:
                        root.kill()
                    root.wait(timeout=10)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="元世代のWindows handleと別の所有プロセスを実物で照合する")
def test_job_rejects_mismatched_handle_and_leaves_unrelated_process_alive(tmp_path, host_environ):
    """異なる世代のhandleを所属へ変換せず、無関係な同時起動プロセスを終了しない。"""
    command = [sys.executable, "-c", late_descendant.held_source(tmp_path / "suspended.release")]
    with WindowsJob() as job, subprocess.Popen(command, creationflags=CREATE_SUSPENDED, env=host_environ()) as root:
        try:
            with subprocess.Popen(command, creationflags=CREATE_SUSPENDED, env=host_environ()) as unrelated:
                try:
                    with pytest.raises(OSError, match="handleとPIDが一致"):
                        job.assign_suspended(
                            int(cast(Any, unrelated)._handle),  # noqa: SLF001  # pylint: disable=protected-access,no-member
                            root.pid,
                        )
                    job.assign_suspended(
                        int(cast(Any, root)._handle),  # noqa: SLF001  # pylint: disable=protected-access,no-member
                        root.pid,
                    )
                    job.terminate()
                    root.wait(timeout=10)
                    assert unrelated.poll() is None
                finally:
                    unrelated.kill()
                    unrelated.wait(timeout=10)
        finally:
            if root.poll() is None:
                root.kill()
            root.wait(timeout=10)
