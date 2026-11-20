"""起動前準備だけを専用端末で実行し、キャッシュ待ちの中断と期限を区別する。"""

import contextlib
import os
import pathlib
import shutil
import subprocess
import sys
import time

import psutil
import pytest

_BOOTSTRAP = pathlib.Path(__file__).with_name("update_bootstrap.sh")
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linuxの制御端末とflockを使う")


def _write_tools(tmp_path: pathlib.Path, stage: str) -> None:
    """実uvとネットワークを使わず、対象の子と孫だけをテストのロックで待たせる。"""
    uv = tmp_path / "fake-uv"
    uv.write_text(
        f"""#!{sys.executable}
import fcntl, os, pathlib, signal, sys
command = ' '.join(sys.argv[1:]).replace(' 3.15', '').removesuffix(' --no-cache')
if pathlib.Path(sys.argv[0]).name == 'curl':
    command = 'uv installer'
with open('calls', 'a') as stream:
    print(command, file=stream)
if command == 'python find' and os.environ['STAGE'] == 'python install':
    sys.exit(2)
if command == os.environ['STAGE']:
    if 'FAILURE' in os.environ:
        sys.exit(int(os.environ['FAILURE']))
    def stopped(signum, _frame):
        with open('signal', 'ab', buffering=0) as log:
            log.write(signal.Signals(signum).name.encode() + b'\\n')
        if not os.environ.get('STUBBORN'):
            sys.exit(0)
    signal.signal(signal.SIGINT, stopped)
    signal.signal(signal.SIGTERM, stopped)
    with open('lock', 'rb') as lock:
        pathlib.Path('child').write_text(str(os.getpid()))
        if os.fork() == 0:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            pathlib.Path('grandchild').write_text(str(os.getpid()))
            fcntl.flock(lock, fcntl.LOCK_SH)
            os._exit(0)
        os.wait()
if command == 'uv installer':
    print('ln -s "$PWD/fake-uv" "$UV_INSTALL_DIR/uv"')
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    (tmp_path / "curl").symlink_to(uv)
    if stage != "uv installer":
        (tmp_path / "uv").symlink_to(uv)
    real_timeout = shutil.which("timeout")
    assert real_timeout
    timer = tmp_path / "timeout"
    timer.write_text(
        "#!/bin/bash\nargs=()\nfor arg do\n"
        "  case $arg in 60|300|900) arg=$LIMIT;; --kill-after=10) arg=--kill-after=.2;; esac\n"
        '  args+=("$arg")\ndone\n'
        f'exec "{real_timeout}" "${{args[@]}}"\n',
        encoding="utf-8",
    )
    timer.chmod(0o755)


@pytest.mark.parametrize(
    ("stage", "label", "expected"),
    [
        ("uv installer", "uvの導入", 130),
        ("self update", "uvの自己更新", 130),
        ("python install --upgrade", "管理Pythonの更新", 130),
        ("python find", "管理Pythonの検索", 130),
        ("python install", "管理Pythonの導入", 130),
        ("python install --upgrade", "管理Pythonの更新", 0),
        ("self update", "uvの自己更新", 0),
        ("python install", "管理Pythonの導入", 124),
    ],
)
def test_locked_bootstrap(tmp_path: pathlib.Path, stage: str, label: str, expected: int) -> None:
    """ロック保持者と無関係な子は残し、中断だけが再試行・後続処理を止める。"""
    import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error
    import pty  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    interrupt = expected == 130
    _write_tools(tmp_path, stage)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "STAGE": stage,
        "STUBBORN": "1" if stage == "self update" else "",
        "LIMIT": "30" if interrupt else "1",
    }
    command = ["bash", "-c", 'source "$1" "$2"; echo later > later', "bash", str(_BOOTSTRAP), str(tmp_path / "uv")]
    master, slave = pty.openpty()
    with (
        os.fdopen(master, "wb", buffering=0) as terminal,
        os.fdopen(slave, "rb", buffering=0) as tty,
        (tmp_path / "lock").open("wb") as holder,
        (tmp_path / "output").open("wb") as output,
        subprocess.Popen(["sleep", "30"], env=env, start_new_session=True) as unrelated,
    ):
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            with subprocess.Popen(
                ["setsid", "--ctty", *command], cwd=tmp_path, env=env, stdin=tty, stdout=output, stderr=output
            ) as process:
                try:
                    deadline = time.monotonic() + 5
                    ready = tmp_path / "grandchild"
                    while not ready.exists() or not ready.stat().st_size:
                        assert process.poll() is None and time.monotonic() < deadline
                        time.sleep(0.01)
                    progress = (tmp_path / "output").read_text().splitlines()[-1]
                    assert label in progress and "Ctrl+C" in progress
                    if stage in {"python install --upgrade", "python install"}:
                        assert "uvキャッシュの整理中は待つ場合があります" in progress
                    if interrupt:
                        terminal.write(b"\x03")
                    assert process.wait(timeout=5) == expected
                    assert (tmp_path / "later").exists() is (expected == 0)
                    calls = (tmp_path / "calls").read_text().splitlines()
                    if interrupt:
                        assert calls[-1] == stage and calls.count(stage) == 1
                    elif stage == "python install":
                        assert calls[-2:] == [stage, stage]
                    elif stage == "python install --upgrade":
                        assert "exit=124" in (tmp_path / "output").read_text()
                    for name in ("child", "grandchild"):
                        pid = int((tmp_path / name).read_text())
                        deadline = time.monotonic() + 1
                        with contextlib.suppress(psutil.NoSuchProcess):
                            descendant = psutil.Process(pid)
                            while descendant.status() != psutil.STATUS_ZOMBIE:
                                assert time.monotonic() < deadline, name
                                time.sleep(0.01)
                    if stage in {"python install --upgrade", "python find", "python install"}:
                        assert ("SIGINT" if interrupt else "SIGTERM") in (tmp_path / "signal").read_text().splitlines()
                    assert unrelated.poll() is None
                    with (tmp_path / "lock").open("rb") as probe, pytest.raises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
                finally:
                    fcntl.flock(holder, fcntl.LOCK_UN)
        finally:
            unrelated.terminate()
    # 同じロックの解放後は中断なしで本体への分岐まで戻れる。
    (tmp_path / "later").unlink(missing_ok=True)
    env["STAGE"] = "python install --upgrade"
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, check=False, timeout=5)
    assert result.returncode == 0 and (tmp_path / "later").exists(), result.stderr


@pytest.mark.parametrize(("stage", "expected"), [("python install --upgrade", 0), ("python install", 17)])
def test_ordinary_failure(tmp_path: pathlib.Path, stage: str, expected: int) -> None:
    """通常失敗は更新なら記録して続行し、必須の導入なら再試行して終了値を返す。"""
    _write_tools(tmp_path, stage)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" "$2"; printf "%s" "$UPDATE_DOTFILES_BOOTSTRAP_FAILURES"',
            "bash",
            str(_BOOTSTRAP),
            str(tmp_path / "uv"),
        ],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": f"{tmp_path}:/usr/bin:/bin", "STAGE": stage, "FAILURE": "17", "LIMIT": "5"},
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=5,
    )
    assert result.returncode == expected, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    if expected == 0:
        assert calls[-1] == "python find"
        assert "管理Pythonの更新: exit=17" in result.stdout
    else:
        assert calls[-2:] == [stage, stage]
