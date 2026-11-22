"""Windows実APIで、列挙を使わない所属回収と明示離脱を検証する。"""

import subprocess
import sys
import time
from typing import Any, cast

import pytest

from agent_toolkit._common.windows_job import CREATE_SUSPENDED, WindowsJob, start_owned_process
from agent_toolkit._testing import late_descendant


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Jobの所属・離脱と実際のプロセス回収を検証する")
def test_job_reclaims_orphan_and_preserves_explicit_breakaway(tmp_path, host_environ):
    """短命な親の子を回収し、明示離脱した子がJobを閉じた後に完了する。"""
    script = tmp_path / "inner.py"
    outside = tmp_path / "outside.txt"
    release = tmp_path / "outside.release"
    ready = tmp_path / "outside.ready"
    outside_source = (
        "from pathlib import Path\n"
        "import sys, time\n"
        "Path(sys.argv[3]).touch()\n"
        "deadline = time.monotonic() + 45\n"
        "while not Path(sys.argv[2]).exists():\n"
        "    if time.monotonic() >= deadline: raise SystemExit(1)\n"
        "    time.sleep(0.01)\n"
        "path = Path(sys.argv[1])\n"
        "pending = path.with_suffix('.tmp')\n"
        "pending.write_text('outside', encoding='utf-8')\n"
        "pending.replace(path)\n"
    )
    script.write_text(
        "import subprocess, sys\n"
        "from agent_toolkit._common.windows_job import start_independent_process\n"
        f"start_independent_process([sys.executable, '-c', {outside_source!r}, sys.argv[1], sys.argv[2], sys.argv[3]], "
        "windows=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        + late_descendant.held_source(tmp_path / "job.release"),
        encoding="utf-8",
    )
    with (
        WindowsJob() as job,
        start_owned_process(
            late_descendant.cli_command(tmp_path, str(script), str(outside), str(release), str(ready)),
            job=job,
            env=host_environ(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as root,
    ):
        closed = False
        try:
            late_descendant.wait_spawner(tmp_path)
            late_descendant.trigger(tmp_path)
            deadline = time.monotonic() + 10
            while not ready.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("明示離脱したコマンドの起動を確認できませんでした")
                time.sleep(0.01)
            job.terminate()
            job.close()
            closed = True
            root.wait(timeout=10)
            late_descendant.assert_released(tmp_path)
            release.touch()
            deadline = time.monotonic() + 10
            while not outside.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("明示離脱したコマンドが完了しませんでした")
                time.sleep(0.01)
            assert outside.read_text(encoding="utf-8") == "outside"
        finally:
            release.touch()
            # Popen.__exit__が待つ前に、失敗時もJobの全所属を停止する。
            try:
                if not closed:
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
