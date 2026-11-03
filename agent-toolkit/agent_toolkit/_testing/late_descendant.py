"""終了処理の最初の列挙の後に、資源を保持する子孫を起動させる持ち込み試験の補助。

模擬CLIは補助プロセス（spawner）を起動してから本来の処理を続ける。
spawnerは`trigger`ファイルが現れると、`held/held.txt`を開いたまま待つholderを起動する。
`arm`は`process_tree`の子孫列挙を包み、終了処理の最初の列挙が返った直後に`trigger`を作ってholderの準備を待つ。
これにより、終了対象の列挙と終了要求の間に子孫が増える順序を決定的に発生させる。

Windowsでは、spawnerがholderの起動後に終了し、親を失ったholderを再現する。
Codex App Serverの終了時に観測した、親が終了した後も一時領域を保持し続けた孫と同じ形である。
POSIXでは親を失った子がinitへ付け替わり、どの列挙方式でも起点から辿れない（`process_tree`の保証境界）。
このためPOSIXのspawnerはholderの親のまま残る。
"""

import pathlib
import shutil
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import psutil

from agent_toolkit._common import process_tree

_SCRIPT = r"""
import os, pathlib, runpy, subprocess, sys, time

role, directory = sys.argv[1], pathlib.Path(sys.argv[2])

def launch(child_role):
    # 起動元の標準入出力（App Serverのpipeなど）を引き継がせず、起動の失敗は診断ファイルへ残す。
    with open(directory / (child_role + ".err"), "ab") as error:
        subprocess.Popen(
            [sys.executable, __file__, child_role, str(directory)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error,
        )

if role == "holder":
    with open(directory / "held" / "held.txt", "w", encoding="utf-8"):
        (directory / "holder.pid").write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(120)
    sys.exit(0)
if role == "spawner":
    (directory / "spawner.pid").write_text(str(os.getpid()), encoding="utf-8")
    while not (directory / "trigger").exists():
        time.sleep(0.01)
    launch("holder")
    while not (directory / "holder.pid").exists():
        time.sleep(0.01)
    if os.name == "nt":
        sys.exit(0)
    time.sleep(120)
    sys.exit(0)
# role == "cli": spawnerを起動し、残りの引数があればそのスクリプトを本来の処理として実行する。
launch("spawner")
if len(sys.argv) > 3:
    sys.argv = sys.argv[3:]
    runpy.run_path(sys.argv[0], run_name="__main__")
else:
    time.sleep(120)
"""

READY_TIMEOUT_SECONDS = 30.0
"""spawnerとholderの起動を待つ上限（秒）。CIの高負荷時でもPythonの起動が収まる値とする。"""


def prepare(directory: pathlib.Path) -> pathlib.Path:
    """作業ディレクトリへ模擬CLIのスクリプトを置き、そのパスを返す。"""
    (directory / "held").mkdir(parents=True, exist_ok=True)
    script = directory / "late_descendant_cli.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    return script


def cli_command(directory: pathlib.Path, *inner: str) -> list[str]:
    """spawnerを起動してから`inner`のスクリプトを実行する（省略時は待機する）模擬CLIの起動コマンドを返す。"""
    return [sys.executable, str(prepare(directory)), "cli", str(directory), *inner]


def wait_spawner(directory: pathlib.Path) -> None:
    """spawnerの起動を待つ。上限までに起動しない場合は失敗させる。"""
    _wait_file(directory / "spawner.pid", "spawnerの起動を確認できませんでした", directory / "spawner.err")


def arm(
    directory: pathlib.Path, setattr_: Callable[[Any, str, Any], None], *, thread_name: str | None = None
) -> threading.Event:
    """`process_tree`の子孫列挙を包み、最初の列挙の直後にholderを起動させる。

    修正前の実装は`collect_descendants`、修正後は`_scan_descendants`で列挙するため、存在する方を包む。
    `thread_name`を指定すると、そのスレッドからの列挙だけを対象にする。
    holderが起動しない場合（列挙より先に生成が止められた場合）は上限まで待って処理を続ける。
    戻り値のEventは同期点を通過したときに設定される。
    """
    name = "_scan_descendants" if hasattr(process_tree, "_scan_descendants") else "collect_descendants"
    original = getattr(process_tree, name)
    fired = threading.Event()

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not fired.is_set() and (thread_name is None or threading.current_thread().name == thread_name):
            fired.set()
            (directory / "trigger").touch()
            deadline = time.monotonic() + READY_TIMEOUT_SECONDS
            while not (directory / "holder.pid").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
        return result

    setattr_(process_tree, name, wrapper)
    return fired


def assert_released(directory: pathlib.Path) -> None:
    """holderが起動し、終了処理の後に終了してファイルを解放したことを確かめる。

    holderが起動していなければ同期点を通過していないため失敗させる。
    解放の観測は上限付きで待つ（POSIXの強制終了後、親を失った子の後始末は非同期に進むため）。
    """
    holder_file = directory / "holder.pid"
    assert holder_file.exists(), "終了処理の最初の列挙の後にholderが起動しませんでした"
    holder = int(holder_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10.0
    while _holds(holder) and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        assert not _holds(holder), f"列挙の後に起動したholder（pid {holder}）が残っています"
        shutil.rmtree(directory / "held")
    finally:
        if _holds(holder):
            psutil.Process(holder).kill()


def _holds(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.Error, OSError:
        return False


def _wait_file(path: pathlib.Path, message: str, diagnostics: pathlib.Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while not path.exists():
        if time.monotonic() >= deadline:
            detail = "（診断ファイルなし）"
            if diagnostics.exists():
                detail = diagnostics.read_text(encoding="utf-8", errors="replace")
            raise AssertionError(f"{message}: {detail}")
        time.sleep(0.01)
