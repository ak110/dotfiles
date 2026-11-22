import pathlib
import subprocess
import sys

import psutil
import pytest

from agent_toolkit._common import process_tree

pytestmark = pytest.mark.windows


@pytest.mark.cpu_limit(
    3.0,
    reason=(
        "起点が出力を継承した子を残して先に終了する実プロセス2つで、時間切れの後に子の自然終了を待たず回収する代表例。"
        "回収のための子孫の走査を含む。"
    ),
)
def test_run_with_timeout_reclaims_output_holder_left_by_exited_root(tmp_path, monkeypatch):
    pids = tmp_path / "pids"
    finished = tmp_path / "holder.finished"

    holder = f"import pathlib, time; time.sleep(20); pathlib.Path({str(finished)!r}).touch()"
    root = (
        "import os, pathlib, subprocess, sys; "
        f"holder = subprocess.Popen([sys.executable, '-c', {holder!r}]); "
        f"pathlib.Path({str(pids)!r} + '.tmp').write_text(f'{{os.getpid()}} {{holder.pid}}'); "
        f"os.replace({str(pids)!r} + '.tmp', {str(pids)!r})"
    )

    class ExitedRootPopen(subprocess.Popen):
        def communicate(self, input=None, timeout=None):  # pylint: disable=redefined-builtin
            if timeout is not None:
                self.wait(timeout=30)
            return super().communicate(input, timeout=timeout)

    monkeypatch.setattr(subprocess, "Popen", ExitedRootPopen)
    if sys.platform != "win32":
        _observe_exited_parent_like_windows(monkeypatch, pids)
    with pytest.raises(subprocess.TimeoutExpired):
        process_tree.run_with_timeout([_python_without_launcher(), "-c", root], capture_output=True, timeout=1)
    assert not finished.exists(), "時間切れの後、出力を保持する子の自然終了まで待った"


@pytest.mark.cpu_limit(1.0, reason="出力を閉じて正常終了する実プロセス1つで、終了後の全OS走査を省くことを確かめる。")
def test_run_with_timeout_skips_rescan_after_normal_exit(monkeypatch):
    roots: list[int] = []

    class RecordingPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            roots.append(self.pid)

    observed = psutil.Process.is_running
    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(psutil.Process, "is_running", lambda target: target.pid in roots or observed(target))
    monkeypatch.setattr(process_tree, "_scan_descendants", lambda *_args, **_kwargs: pytest.fail("正常終了した所有の再走査"))
    result = process_tree.run_with_timeout(
        [_python_without_launcher(), "-c", "print('完了')"], capture_output=True, encoding="utf-8", timeout=30
    )
    assert (result.returncode, result.stdout) == (0, "完了\n")


def _python_without_launcher() -> str:
    """子を直接起動するPython本体のパスを返す。

    Windowsの仮想環境の`python.exe`は本体を子として起動する中継役であり、`Popen.pid`が子を起動した本体にならない。
    """
    return getattr(sys, "_base_executable", sys.executable)


def _observe_exited_parent_like_windows(monkeypatch: pytest.MonkeyPatch, pids: pathlib.Path) -> None:
    """POSIXで、Windowsが終了した起点について示す観測を与える。

    Windowsは保持したhandleで終了後の起点の世代を確かめられ（PIDと起動時刻で識別する）、子の親PIDを書き換えない。
    POSIXでは親を失った子がinitへ付け替わって起点から辿れないため、`pids`に記録された起点と子の観測だけをWindowsにそろえる。
    """
    parent_pids = process_tree._parent_pids  # pylint: disable=protected-access
    retain = process_tree.ProcessIdentityPins.retain

    def recorded() -> tuple[int, int] | None:
        if not pids.exists():
            return None
        root, holder = map(int, pids.read_text().split())
        return root, holder

    def keep_parent(requested: set[int]) -> dict[int, int]:
        observed = parent_pids(requested)
        if (pair := recorded()) is not None and pair[0] in requested:
            observed[pair[1]] = pair[0]
        return observed

    def retain_root(self: process_tree.ProcessIdentityPins, process: psutil.Process) -> bool:
        return ((pair := recorded()) is not None and process.pid == pair[0]) or retain(self, process)

    monkeypatch.setattr(process_tree.process_identity, "start_id", lambda _process: None)
    monkeypatch.setattr(process_tree, "_parent_pids", keep_parent)
    monkeypatch.setattr(process_tree.ProcessIdentityPins, "retain", retain_root)
