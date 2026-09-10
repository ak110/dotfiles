"""`scripts/update_dotfiles.py`のテスト。

通常4段とforce時5段の直列実行順序・fail-fast・排他ロック・標準ストリームを検証する。
"""

import contextlib
import importlib
import os
import pathlib
import select
import signal
import subprocess
import sys
import time
from typing import Any, cast

import psutil
import pytest

pty = None if sys.platform == "win32" else importlib.import_module("pty")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position


def _fake_run(
    returncodes: dict[str, int],
    calls: list[list[str]],
    *,
    stdout_by_command: dict[str, str] | None = None,
    stderr_by_command: dict[str, str] | None = None,
    environments: list[dict[str, str]] | None = None,
    encodings: list[str | None] | None = None,
) -> Any:
    """コマンド名（argv[0:2]相当）ごとの終了コードを返すfake `subprocess.run`。"""
    stdout_by_command = stdout_by_command or {}
    stderr_by_command = stderr_by_command or {}

    def fake_run(argv: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        calls.append(list(argv))
        if environments is not None:
            environments.append(cast(dict[str, str], kwargs["env"]))
        if encodings is not None:
            encodings.append(cast(str | None, kwargs.get("encoding")))
        key = argv[1] if argv[0] == "chezmoi" else argv[0]
        returncode = returncodes.get(key, 0)
        stdout_text = stdout_by_command.get(key, "")
        stderr_text = stderr_by_command.get(key, "")
        text_mode = bool(kwargs.get("text") or kwargs.get("encoding"))
        stdout: Any = stdout_text if text_mode else stdout_text.encode()
        stderr: Any = stderr_text if text_mode else stderr_text.encode()
        return subprocess.CompletedProcess(argv, returncode=returncode, stdout=stdout, stderr=stderr)

    return fake_run


class _FakePopen:
    """git pull用`Popen`の最小fake。"""

    pid = 43210

    def __init__(
        self,
        argv: list[str],
        calls: list[list[str]],
        *,
        returncode: int = 0,
        output_text: str = "",
        error_text: str = "",
        environments: list[dict[str, str]] | None = None,
        encodings: list[str | None] | None = None,
        communicate_timeouts: list[int | None] | None = None,
        **kwargs: object,
    ) -> None:
        self.returncode = returncode
        self._stdout = output_text
        self._stderr = error_text
        self._communicate_timeouts = communicate_timeouts
        self.kill_calls = 0
        calls.append(list(argv))
        if environments is not None:
            environments.append(cast(dict[str, str], kwargs["env"]))
        if encodings is not None:
            encodings.append(cast(str | None, kwargs.get("encoding")))

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        """設定済みの出力を返し、待機上限を記録する。"""
        if self._communicate_timeouts is not None:
            self._communicate_timeouts.append(timeout)
        return self._stdout, self._stderr

    def kill(self) -> None:
        """直接子へのkill呼び出しを記録する。"""
        self.kill_calls += 1


class _TimeoutPopen(_FakePopen):
    """初回又は全回の`communicate`をタイムアウトさせるfake。"""

    def __init__(
        self,
        argv: list[str],
        calls: list[list[str]],
        *,
        output_text: str = "",
        error_text: str = "",
        communicate_timeouts: list[int | None] | None = None,
        always_timeout: bool = False,
    ) -> None:
        super().__init__(
            argv,
            calls,
            output_text=output_text,
            error_text=error_text,
            communicate_timeouts=communicate_timeouts,
        )
        self._always_timeout = always_timeout
        self._communicate_count = 0

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        self._communicate_count += 1
        if self._communicate_timeouts is not None:
            self._communicate_timeouts.append(timeout)
        if self._always_timeout or self._communicate_count == 1:
            assert timeout is not None
            raise subprocess.TimeoutExpired(["chezmoi", "git"], timeout)
        return self._stdout, self._stderr


def _fake_popen(
    returncodes: dict[str, int],
    calls: list[list[str]],
    *,
    stdout_by_command: dict[str, str] | None = None,
    stderr_by_command: dict[str, str] | None = None,
    environments: list[dict[str, str]] | None = None,
    encodings: list[str | None] | None = None,
    communicate_timeouts: list[int | None] | None = None,
) -> Any:
    """git pullの終了コードと出力を返すfake `subprocess.Popen`。"""
    stdout_by_command = stdout_by_command or {}
    stderr_by_command = stderr_by_command or {}

    def fake_popen(argv: list[str], **kwargs: object) -> _FakePopen:
        key = argv[1] if argv[0] == "chezmoi" else argv[0]
        return _FakePopen(
            argv,
            calls,
            returncode=returncodes.get(key, 0),
            output_text=stdout_by_command.get(key, ""),
            error_text=stderr_by_command.get(key, ""),
            environments=environments,
            encodings=encodings,
            communicate_timeouts=communicate_timeouts,
            **kwargs,
        )

    return fake_popen


def test_run_git_pull_disables_mise_auto_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程1のサブプロセス起動が`MISE_AUTO_INSTALL=0`を含む環境で実行される。"""
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, environments=environments))

    assert update_dotfiles._run_git_pull(1, 4) == 0  # pylint: disable=protected-access
    assert environments[0]["MISE_AUTO_INSTALL"] == "0"


def test_run_git_pull_disables_submodule_recursion(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程1がgitのサブコマンドより前に`-c submodule.recurse=false`を渡す。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))

    assert update_dotfiles._run_git_pull(1, 4) == 0  # pylint: disable=protected-access
    argv = calls[0]
    assert argv[argv.index("-c") + 1] == "submodule.recurse=false"
    assert argv.index("-c") < argv.index("pull")


def test_run_git_pull_decodes_output_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程1の取得出力をUTF-8でデコードする。"""
    calls: list[list[str]] = []
    encodings: list[str | None] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, encodings=encodings))

    assert update_dotfiles._run_git_pull(1, 4) == 0  # pylint: disable=protected-access
    assert encodings == ["utf-8"]


@pytest.mark.parametrize(
    ("raw_value", "expected_timeout"),
    [(None, 600), ("17", 17), ("0", None)],
)
def test_git_timeout_environment_is_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    raw_value: str | None,
    expected_timeout: int | None,
) -> None:
    """未設定・正数・0をgit pullの待機上限へ反映する。"""
    if raw_value is None:
        monkeypatch.delenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", raising=False)
    else:
        monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", raw_value)
    calls: list[list[str]] = []
    communicate_timeouts: list[int | None] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _fake_popen({}, calls, communicate_timeouts=communicate_timeouts),
    )
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

    assert update_dotfiles.main() == 0
    assert communicate_timeouts == [expected_timeout]


@pytest.mark.parametrize("raw_value", ["-1", "invalid"])
def test_invalid_git_timeout_returns_2_without_starting_process(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """負数と整数でない上限値を値と受理条件付きで拒否する。"""
    monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", raw_value)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail(f"Popen: {args}, {kwargs}"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail(f"run: {args}, {kwargs}"))

    assert update_dotfiles.main() == 2
    error = capsys.readouterr().err
    assert raw_value in error
    assert "0以上の整数" in error


def test_run_step_does_not_receive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """git pull以外のchezmoi工程へ待機上限を渡さない。"""
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert "timeout" not in kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    for argv in (
        ["chezmoi", "init"],
        ["chezmoi", "status"],
        ["chezmoi", "diff"],
        ["chezmoi", "apply"],
    ):
        assert update_dotfiles._run_step(1, 1, "test", argv)[0] == 0  # pylint: disable=protected-access
    assert len(calls) == 4


def test_git_timeout_kills_descendants_and_limits_output_recovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """上限超過時に子孫と親を終了し、回収上限を付けて未完了を報告する。"""
    calls: list[list[str]] = []
    communicate_timeouts: list[int | None] = []
    process = _TimeoutPopen(
        ["chezmoi", "git"],
        calls,
        output_text="partial output\n",
        error_text="partial error\n",
        communicate_timeouts=communicate_timeouts,
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    killed: list[int] = []

    class Process:
        def __init__(self, pid: int, children: list["Process"] | None = None) -> None:
            self.pid = pid
            self._children = children or []

        def children(self, *, recursive: bool) -> list["Process"]:
            assert recursive
            return self._children

        def kill(self) -> None:
            killed.append(self.pid)

    descendants = [Process(101), Process(102)]
    parent = Process(process.pid, descendants)
    monkeypatch.setattr(psutil, "Process", lambda pid: parent if pid == process.pid else pytest.fail(str(pid)))
    waited: list[tuple[list[int], int]] = []
    monkeypatch.setattr(
        psutil,
        "wait_procs",
        lambda processes, timeout: waited.append(([item.pid for item in processes], timeout)),
    )

    assert update_dotfiles._run_git_pull(1, 4, timeout=7) == 1  # pylint: disable=protected-access

    assert communicate_timeouts == [7, 30]
    assert killed == [101, 102, process.pid]
    assert waited == [([101, 102, process.pid], 5)]
    assert process.kill_calls == 1
    captured = capsys.readouterr()
    assert "partial output" in captured.out
    assert "git pull" in captured.err
    assert "未完了" in captured.err
    assert "7秒" in captured.err
    assert "UPDATE_DOTFILES_GIT_TIMEOUT_SEC" in captured.err
    assert "子孫プロセスを終了" in captured.err


def test_git_timeout_does_not_wait_again_when_output_recovery_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """終了後の出力回収も超過した場合は空出力として復帰する。"""
    calls: list[list[str]] = []
    communicate_timeouts: list[int | None] = []
    process = _TimeoutPopen(
        ["chezmoi", "git"],
        calls,
        always_timeout=True,
        communicate_timeouts=communicate_timeouts,
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(update_dotfiles, "_kill_process_tree", lambda _process: None)

    assert update_dotfiles._run_git_pull(1, 4, timeout=3) == 1  # pylint: disable=protected-access
    assert communicate_timeouts == [3, 30]


def test_run_step_disables_mise_auto_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程2以降のサブプロセス起動が`MISE_AUTO_INSTALL=0`を含む環境で実行される。"""
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, environments=environments))

    returncode, _output = update_dotfiles._run_step(2, 4, "test", ["chezmoi", "init"])  # pylint: disable=protected-access

    assert returncode == 0
    assert environments[0]["MISE_AUTO_INSTALL"] == "0"


def test_run_step_decodes_captured_output_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """工程2以降の取得出力をUTF-8でデコードする。"""
    calls: list[list[str]] = []
    encodings: list[str | None] = []
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, encodings=encodings))

    returncode, _output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3,
        4,
        "test",
        ["chezmoi", "status"],
        capture=True,
    )

    assert returncode == 0
    assert encodings == ["utf-8"]


def test_run_step_decodes_utf8_bytes(capsys: pytest.CaptureFixture[str]) -> None:
    """CP932ではデコードできないUTF-8出力を文字列として転送する。"""
    code = "import sys; sys.stdout.buffer.write('日本語—'.encode('utf-8')); sys.stderr.buffer.write('警告—'.encode('utf-8'))"

    returncode, output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3,
        4,
        "test",
        [sys.executable, "-c", code],
        capture=True,
    )

    assert returncode == 0
    assert output == "日本語—"
    assert capsys.readouterr().err == "警告—"


def test_child_env_preserves_existing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の環境変数が保持される。"""
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_SENTINEL", "preserved")

    environment = update_dotfiles._child_env()  # pylint: disable=protected-access

    assert environment["UPDATE_DOTFILES_TEST_SENTINEL"] == "preserved"
    assert environment["MISE_AUTO_INSTALL"] == "0"


class TestFourStepsInOrder:
    """4段が順に呼ばれ、成功時にexit code 0を返すことを検証する。"""

    def test_all_steps_succeed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert calls[0][:2] == ["chezmoi", "git"]
        assert calls[1][:2] == ["chezmoi", "init"]
        assert calls[2][:2] == ["chezmoi", "status"]
        assert calls[3][:2] == ["chezmoi", "apply"]
        assert "--quiet" in calls[0]
        assert "--force" not in calls[3]

    def test_force_adds_diff_before_forced_apply(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"diff": "diff output\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main(["--force"]) == 0
        assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
        assert calls[3] == ["chezmoi", "diff", "--no-pager"]
        assert calls[4] == ["chezmoi", "apply", "--force"]
        captured = capsys.readouterr()
        assert "=== [4/5] chezmoi diff" in captured.out
        assert "diff output\n" in captured.out
        assert not captured.err


class TestStepFailureStopsExecution:
    """途中段の失敗でそのexit codeを返し、以降の段を呼ばないことを検証する。"""

    def test_step2_failure_skips_step3_and_4(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"init": 3}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 3
        assert len(calls) == 2
        assert calls[1][:2] == ["chezmoi", "init"]

    def test_step3_failure_stops_before_apply(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`chezmoi status`段の失敗も他段と同様にfail-fastすること。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 2
        assert len(calls) == 3

    def test_diff_failure_stops_before_apply(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"diff": 7}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main(["--force"]) == 7
        assert [call[1] for call in calls] == ["git", "init", "status", "diff"]


class TestCapturedStderr:
    """各段が生成する標準エラー出力の転送先を検証する。"""

    def test_successful_git_output_is_forwarded_to_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            _fake_popen(
                {},
                calls,
                stdout_by_command={"git": "git stdout\n"},
                stderr_by_command={"git": "remote: Enumerating objects\n"},
            ),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run(
                {},
                calls,
                stdout_by_command={"git": "git stdout\n"},
                stderr_by_command={"git": "remote: Enumerating objects\n"},
            ),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        captured = capsys.readouterr()
        assert "git stdout\n" in captured.out
        assert "remote: Enumerating objects\n" in captured.out
        assert not captured.err

    def test_failed_git_stderr_remains_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            _fake_popen({"git": 2}, calls, stderr_by_command={"git": "fatal: pull failed\n"}),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({"git": 2}, calls, stderr_by_command={"git": "fatal: pull failed\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 2
        captured = capsys.readouterr()
        assert "fatal: pull failed" not in captured.out
        assert captured.err == "fatal: pull failed\n"
        assert len(calls) == 1

    def test_successful_status_stderr_is_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stderr_by_command={"status": "chezmoi status warning\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert capsys.readouterr().err == "chezmoi status warning\n"

    def test_force_diff_stderr_is_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stderr_by_command={"diff": "chezmoi diff warning\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main(["--force"]) == 0
        assert capsys.readouterr().err == "chezmoi diff warning\n"


def test_unknown_argument_exits_2() -> None:
    """未知引数はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        update_dotfiles.main(["--unknown"])
    assert exc_info.value.code == 2


_LOCK_HOLDER_CODE = (
    "import filelock, pathlib, sys, time\n"
    "lock = filelock.FileLock(sys.argv[1])\n"
    "lock.acquire()\n"
    "pathlib.Path(sys.argv[2]).write_text('ready', encoding='utf-8')\n"
    "while not pathlib.Path(sys.argv[3]).exists():\n"
    "    time.sleep(0.05)\n"
    "lock.release()\n"
)

_FAKE_CHEZMOI_CODE = r"""#!/usr/bin/env python3
import os
import pathlib
import subprocess
import sys
import time

if os.environ["UPDATE_DOTFILES_TEST_MODE"] == "interactive":
    terminal = os.open("/dev/tty", os.O_RDWR)
    os.write(terminal, b"passphrase: ")
    value = bytearray()
    while not value.endswith(b"\n"):
        value.extend(os.read(terminal, 1))
    os.close(terminal)
    if value.strip() != b"secret-value":
        raise SystemExit(9)
    print(f"received: {value.decode().strip()}")
else:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"])
    pathlib.Path(os.environ["UPDATE_DOTFILES_TEST_PID_PATH"]).write_text(str(child.pid), encoding="utf-8")
    time.sleep(6)
"""


def _run_git_pull_in_pty(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
    timeout: int | None,
    input_text: str | None = None,
) -> tuple[int, str, pathlib.Path]:
    """疑似端末内で実物の`_run_git_pull`を実行し、終了コードと出力を返す。"""
    if pty is None:
        pytest.skip("ptyを利用できない環境")
    executable = tmp_path / "bin" / "chezmoi"
    executable.parent.mkdir()
    executable.write_text(_FAKE_CHEZMOI_CODE, encoding="utf-8")
    executable.chmod(0o755)
    descendant_pid_path = tmp_path / "descendant.pid"
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_MODE", mode)
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_PID_PATH", str(descendant_pid_path))

    pid, terminal_fd = pty.fork()
    if pid == 0:  # pragma: no cover - 検証対象となる子プロセス側
        returncode = update_dotfiles._run_git_pull(1, 4, timeout=timeout)  # pylint: disable=protected-access
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(returncode)

    output = bytearray()
    input_sent = False
    deadline = time.monotonic() + 12
    status: int | None = None
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([terminal_fd], [], [], 0.1)
            if readable:
                with contextlib.suppress(OSError):
                    output.extend(os.read(terminal_fd, 4096))
            if input_text is not None and not input_sent and b"passphrase:" in output:
                os.write(terminal_fd, f"{input_text}\n".encode())
                input_sent = True
            waited_pid, candidate_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = candidate_status
                break
        if status is None:
            os.kill(pid, signal.SIGKILL)
            _waited_pid, status = os.waitpid(pid, 0)
            pytest.fail("疑似端末内のgit pull検体が12秒以内に終了しなかった")
    finally:
        os.close(terminal_fd)
    return os.waitstatus_to_exitcode(status), output.decode(errors="replace"), descendant_pid_path


@pytest.mark.skipif(sys.platform == "win32", reason="ptyと/dev/ttyを使用するLinux検体")
@pytest.mark.parametrize("timeout", [1, None])
def test_git_pull_preserves_terminal_interaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    timeout: int | None,
) -> None:
    """上限の有無にかかわらず、子が制御端末から入力を受け取る。"""
    returncode, output, _pid_path = _run_git_pull_in_pty(
        tmp_path,
        monkeypatch,
        mode="interactive",
        timeout=timeout,
        input_text="secret-value",
    )
    assert returncode == 0
    assert "passphrase:" in output


@pytest.mark.skipif(sys.platform == "win32", reason="ptyを使用するLinux検体")
def test_git_pull_timeout_terminates_stream_holding_descendant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """標準ストリームを継承する孫も終了し、回収上限の内側で復帰する。"""
    started = time.monotonic()
    returncode, output, pid_path = _run_git_pull_in_pty(
        tmp_path,
        monkeypatch,
        mode="hang",
        timeout=1,
    )
    elapsed = time.monotonic() - started

    assert returncode == 1
    assert elapsed < 8
    del output
    descendant_pid = int(pid_path.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(descendant_pid) or psutil.Process(descendant_pid).status() == psutil.STATUS_ZOMBIE


class TestLockExclusion:
    """排他ロックが機能すること（別プロセス保持中はタイムアウトしてexit code 1）を検証する。

    同一プロセス内で2個の`FileLock`オブジェクトを取得するだけでは、プロセス間排他という
    中核要件（複数の`atk wi process-loop`常駐・手動実行の同時実行対策）を検証できないため、
    `subprocess.Popen`で別プロセスにロックを保持させる。
    """

    def test_lock_timeout_returns_1_without_running_chezmoi(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        lock_path = tmp_path / "locks" / "update-dotfiles.lock"
        lock_path.parent.mkdir(parents=True)
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", lock_path)
        monkeypatch.setattr(update_dotfiles, "_LOCK_TIMEOUT_SEC", 0.2)

        ready_path = tmp_path / "holder_ready"
        release_path = tmp_path / "holder_release"
        with subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", _LOCK_HOLDER_CODE, str(lock_path), str(ready_path), str(release_path)]
        ) as holder:
            try:
                ready_deadline = time.monotonic() + 30
                while not ready_path.exists():
                    holder_returncode = holder.poll()
                    if holder_returncode is not None:
                        pytest.fail(f"ロック保持プロセスが終了した（終了コード: {holder_returncode}）")
                    if time.monotonic() >= ready_deadline:
                        pytest.fail("別プロセスがロックを取得できなかった")
                    time.sleep(0.05)

                assert update_dotfiles.main() == 1
            finally:
                release_path.write_text("release", encoding="utf-8")
                holder.wait(timeout=5)
        assert not calls


class TestFilterApplyPending:
    """`chezmoi status`出力の2列目フィルタを公開インターフェース経由で検証する。"""

    def test_second_column_non_space_lines_are_kept(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        status_output = "\n".join(["  .chezmoiroot", "A  .gitignore", "RM bin/foo"])
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"status": status_output}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        captured = capsys.readouterr()
        assert "RM bin/foo" in captured.out
        assert "  .chezmoiroot" not in captured.out
        assert "A  .gitignore" not in captured.out

    def test_short_lines_are_excluded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"status": "A"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert "\nA\n" not in f"\n{capsys.readouterr().out}\n"
