"""Windows実APIで、列挙を使わない所属回収と明示離脱を検証する。"""

import subprocess
import sys
import time
from typing import Any, cast

import pytest

from agent_toolkit._common.windows_job import CREATE_SUSPENDED, WindowsJob
from agent_toolkit._testing import late_descendant


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
        "creationflags=0x01000208, check=True, timeout=5)\n"
        "time.sleep(60)\n",
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
def test_job_rejects_mismatched_handle_and_leaves_unrelated_process_alive(host_environ):
    """異なる世代のhandleを所属へ変換せず、無関係な同時起動プロセスを終了しない。"""
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
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
