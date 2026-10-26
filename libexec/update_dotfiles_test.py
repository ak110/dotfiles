"""`libexec/update_dotfiles.py`のテスト。

通常4段の表示順序・前提となる段のfail-fastと続行する段の失敗の集約・排他ロック・標準ストリーム・Codex管理daemonの一時停止を検証する。
"""

# pylint: disable=protected-access

import contextlib
import ctypes
import io
import json
import logging
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any, Self, cast

import platformdirs
import psutil
import pytest
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.isolation import restore_host_environment

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position

from pytools._internal import common  # noqa: E402  # pylint: disable=wrong-import-position

_REAL_UPDATE_GIT_WITH_RECOVERY = update_dotfiles._update_git_with_recovery  # pylint: disable=protected-access
_REPOSITORY_ATK_BIN = pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit" / "scripts"


@pytest.fixture(autouse=True)
def _separate_git_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """段階順の単体テストでは、Git回復の統合挙動を低水準pullから分離する。"""
    monkeypatch.setattr(update_dotfiles, "_LOG_PATH", tmp_path / "state" / "update-dotfiles.log")
    monkeypatch.setattr(
        update_dotfiles,
        "_update_git_with_recovery",
        lambda step_no, total, timeout: update_dotfiles._run_git_pull(  # pylint: disable=protected-access
            step_no, total, timeout=timeout
        ),
    )


@pytest.fixture(autouse=True)
def _no_codex_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """実行環境のCodexを分類対象から外し、実在する管理daemonを停止・起動しないようにする。"""
    monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: ())


@pytest.fixture(autouse=True, name="sync_report_path")
def _isolate_sync_report(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """同期結果の記録先を一時領域へ向け、実行環境の状態ディレクトリを書き換えないようにする。"""
    report_path = tmp_path / "state" / "sync-report.json"
    monkeypatch.setattr(update_dotfiles.sync_report, "REPORT_PATH", report_path)
    return report_path


_FAKE_CODEX = "/fake/bin/codex"


def test_plugin_pulls_precede_chezmoi(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """本体pull後・init前にGitプラグインだけを名前順に更新する。"""
    for name in ("20-last", "10-first", "plain"):
        plugin = tmp_path / "plugins" / name
        plugin.mkdir(parents=True)
        if name != "plain":
            (plugin / ".git").write_text("gitdir: fixture", encoding="utf-8")
    calls: list[list[str]] = []
    timeouts: list[int | None] = []
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", "17")
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, communicate_timeouts=timeouts))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))

    assert update_dotfiles.main() == 0
    assert calls[0][:2] == ["chezmoi", "git"]
    assert calls[1:3] == [
        ["git", "-C", str(tmp_path / "plugins" / name), "-c", "submodule.recurse=false", "pull", "--rebase"]
        for name in ("10-first", "20-last")
    ]
    assert calls[3][:2] == ["chezmoi", "init"]
    assert timeouts == [17, 17, 17]


@pytest.mark.parametrize("timeout", [False, True])
def test_plugin_pull_failure_continues_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    timeout: bool,
) -> None:
    """失敗とtimeoutを対象名・Git出力付きで記録し、次のpullと以降の段を続けて終了コード1で終える。"""
    for name in ("10-fail", "20-next"):
        (tmp_path / "plugins" / name / ".git").mkdir(parents=True)
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
    calls: list[list[str]] = []

    def popen(argv: list[str], **kwargs: Any) -> _FakePopen:
        if argv[0] == "chezmoi":
            return _FakePopen(argv, calls, **kwargs)
        if timeout:
            return _TimeoutPopen(argv, calls, error_text="partial plugin error")
        return _FakePopen(argv, calls, returncode=7, output_text="plugin stdout", error_text="plugin stderr", **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
    monkeypatch.setattr(update_dotfiles, "_kill_process_tree", lambda _process: None)
    assert update_dotfiles.main() == 1
    assert [call[:2] for call in calls if call[0] == "chezmoi"] == [
        ["chezmoi", "git"],
        ["chezmoi", "init"],
        ["chezmoi", "status"],
        ["chezmoi", "diff"],
        ["chezmoi", "apply"],
    ]
    assert any("20-next" in " ".join(call) for call in calls)
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert "10-fail" in report["failed_stage"]
    assert "20-next" in report["failed_stage"]
    assert "10-fail" in report["stderr_tail"]
    assert ("partial plugin error" if timeout else "plugin stderr") in report["stderr_tail"]
    if not timeout:
        assert "plugin stdout" in report["stderr_tail"]


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
        if argv[0] == "chezmoi":
            key = argv[1]
        elif argv[0] == _FAKE_CODEX:
            key = f"daemon {argv[-1]}"
        else:
            key = argv[0]
        returncode = returncodes.get(key, 0)
        stdout_text = stdout_by_command.get(key, "")
        stderr_text = stderr_by_command.get(key, "")
        text_mode = bool(kwargs.get("text") or kwargs.get("encoding"))
        stdout: Any = stdout_text if text_mode else stdout_text.encode()
        stderr: Any = stderr_text if text_mode else stderr_text.encode()
        return subprocess.CompletedProcess(argv, returncode=returncode, stdout=stdout, stderr=stderr)

    return fake_run


@pytest.mark.parametrize("mode", ["full", "minimal"])
def test_public_mode_argument_persists_and_reaches_every_update_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, mode: str
) -> None:
    """更新の公開入力から保存値を変え、Git・テンプレート・post-applyへ同じ実効値を渡す。"""
    configuration = tmp_path / "config.json"
    configuration.write_text(
        json.dumps({"dotfiles_mode": "minimal" if mode == "full" else "full", "other": 42}), encoding="utf-8"
    )
    monkeypatch.setattr(update_dotfiles.dotfiles_mode, "configuration_path", lambda: configuration)
    monkeypatch.setenv(update_dotfiles.dotfiles_mode.MODE_ENV, "full")
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, environments=environments))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, environments=environments))
    assert update_dotfiles.main(["--mode", mode]) == 0
    assert json.loads(configuration.read_text(encoding="utf-8")) == {"dotfiles_mode": mode, "other": 42}
    assert environments
    assert all(environment[update_dotfiles.dotfiles_mode.MODE_ENV] == mode for environment in environments)
    assert [call[:2] for call in calls if call[0] == "chezmoi"] == [
        ["chezmoi", "git"],
        ["chezmoi", "init"],
        ["chezmoi", "status"],
        ["chezmoi", "diff"],
        ["chezmoi", "apply"],
    ]


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
        self.stdout = io.StringIO(output_text)
        self.stderr = io.StringIO(error_text)
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

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.stdout.close()
        self.stderr.close()


class _TimeoutPopen(_FakePopen):
    """初回または全回の`communicate`をタイムアウトさせるfake。"""

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
        def __init__(self, pid: int, children: list[Self] | None = None) -> None:
            self.pid = pid
            self._children = children or []

        def children(self, *, recursive: bool) -> list[Self]:
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


@pytest.mark.parametrize("platform_name", ["posix", "nt"])
def test_git_timeout_does_not_wait_again_when_output_recovery_times_out(
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
) -> None:
    """二重timeout後は復帰し、POSIXだけが呼出側でpipeを閉じる。"""
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
    # os.name全体を変えるとpathlibの実装選択へ波及するため、対象モジュールだけを差し替える。
    monkeypatch.setattr(update_dotfiles, "os", type("Platform", (), {"name": platform_name}))
    monkeypatch.setattr(update_dotfiles, "_child_env", lambda: {})

    assert update_dotfiles._run_git_pull(1, 4, timeout=3) == 1  # pylint: disable=protected-access
    assert communicate_timeouts == [3, 30]
    assert process.stdout.closed is (platform_name == "posix")
    assert process.stderr.closed is (platform_name == "posix")
    process.stdout.close()
    process.stderr.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linuxのsubreaperで列挙外の孫もテスト自身が回収する")
def test_git_pull_closes_real_pipes_after_detached_descendant_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """直接子が終了し別sessionの孫だけがpipeを保持しても、二重timeout後に両pipeを閉じる。"""
    # Linux prctlの公開定数。孤児の孫を本テストがwaitpidし、PID 1へ残さない。
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int(0)
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0  # PR_GET_CHILD_SUBREAPER
    pid_path = tmp_path / "holder.pid"
    child_code = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
    )
    holder_pid: int | None = None

    def restore_subreaper() -> None:
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0

    def reclaim_holder() -> None:
        nonlocal holder_pid
        if holder_pid is None and pid_path.exists():
            holder_pid = int(pid_path.read_text(encoding="utf-8"))
        if holder_pid is not None:
            os.kill(holder_pid, 9)
            os.waitpid(holder_pid, 0)

    with contextlib.ExitStack() as cleanup:
        assert libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
        cleanup.callback(restore_subreaper)
        process = cleanup.enter_context(
            common.process_scope(
                [sys.executable, "-c", child_code, str(pid_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
            )
        )
        cleanup.callback(reclaim_holder)
        process.wait(timeout=10)
        holder_pid = int(pid_path.read_text(encoding="utf-8"))
        monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
        monkeypatch.setattr(update_dotfiles, "_GIT_OUTPUT_RECOVERY_TIMEOUT_SEC", 0.05)
        assert update_dotfiles._run_git_pull(1, 4, timeout=1) == 1
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed
    assert holder_pid is not None and not psutil.pid_exists(holder_pid)


def test_main_closes_persistent_log_when_start_record_is_interrupted(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """開始記録が中断されても、獲得済みのログファイルを閉じて登録を外す。"""
    original_handlers = update_dotfiles.logger.handlers.copy()
    created: list[logging.FileHandler] = []
    real_handler = update_dotfiles.logging.handlers.RotatingFileHandler

    def handler(*args: Any, **kwargs: Any):
        instance = real_handler(*args, **kwargs)
        # 被検証側の終了後を観測し、旧版の失敗対照でもfixtureが残存資源を回収する。
        request.addfinalizer(instance.close)
        request.addfinalizer(lambda: update_dotfiles.logger.removeHandler(instance))
        created.append(instance)
        return instance

    def interrupt(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(update_dotfiles.logging.handlers, "RotatingFileHandler", handler)
    monkeypatch.setattr(update_dotfiles.sync_report, "write_start", interrupt)
    with pytest.raises(KeyboardInterrupt):
        update_dotfiles.main([])
    assert len(created) == 1
    assert created[0].stream is None
    assert update_dotfiles.logger.handlers == original_handlers


@pytest.mark.parametrize("operation", ["pull", "stash"])
def test_git_subprocess_closes_both_pipes_when_one_close_fails(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    """POSIXのpipe解放の片側が失敗しても、残る側を閉じて元の例外を返す。"""

    class FailingClose(io.StringIO):
        def close(self) -> None:
            if not self.closed:
                super().close()
                raise OSError("pipe close failed")

    process = _FakePopen(["test"], [])
    process.stdout.close()
    process.stdout = FailingClose()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(update_dotfiles, "_git_env", lambda: {})
    monkeypatch.setattr(update_dotfiles, "os", type("Platform", (), {"name": "posix"}))
    with pytest.raises(OSError, match="pipe close failed"):
        if operation == "pull":
            update_dotfiles._run_git_pull(1, 4, timeout=1)
        else:
            update_dotfiles._run_worktree_stash("save")
    assert process.stdout.closed and process.stderr.closed


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


@pytest.mark.parametrize("exit_code", [0, 7])
def test_run_step_preserves_exit_and_non_utf8_diagnostic(capsys: pytest.CaptureFixture[str], exit_code: int) -> None:
    code = "import sys; sys.stderr.buffer.write(b'diagnostic: \\x93'); sys.exit(int(sys.argv[1]))"

    returncode, output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3, 4, "test", [sys.executable, "-c", code, str(exit_code)], capture=True
    )

    assert returncode == exit_code
    assert output == ""
    assert "diagnostic: �" in capsys.readouterr().err
    assert update_dotfiles._last_stderr_tail == "diagnostic: �"  # pylint: disable=protected-access


_INTERLEAVED_CHILD_CODE = """
import sys
for index in (1, 2):
    sys.stdout.write(f"out{index}\\n")
    sys.stdout.flush()
    sys.stderr.write(f"err{index}\\n")
    sys.stderr.flush()
sys.exit(int(sys.argv[1]))
"""


@pytest.mark.parametrize("exit_code", [0, 4])
def test_run_step_streams_interleaved_child_output_in_write_order(capfd: pytest.CaptureFixture[str], exit_code: int) -> None:
    """子が交互に書いた出力のストリームを分離し、終了コードを保持する。"""

    returncode, output = update_dotfiles._run_step(  # pylint: disable=protected-access
        3, 5, "chezmoi apply", [sys.executable, "-c", _INTERLEAVED_CHILD_CODE, str(exit_code)]
    )

    captured = capfd.readouterr()
    assert returncode == exit_code
    assert output == ""
    assert captured.out.splitlines() == ["=== [3/5] chezmoi apply ===", "out1", "out2"]
    assert captured.err.splitlines() == ["err1", "err2"]
    assert update_dotfiles._last_stderr_tail is None


def test_child_env_preserves_existing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の環境変数が保持される。"""
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_SENTINEL", "preserved")

    environment = update_dotfiles._child_env()  # pylint: disable=protected-access

    assert environment["UPDATE_DOTFILES_TEST_SENTINEL"] == "preserved"
    assert environment["MISE_AUTO_INSTALL"] == "0"
    assert str(pathlib.Path.home() / ".local" / "bin") in environment["PATH"].split(os.pathsep)


def test_inherited_streams_preserve_combined_write_order() -> None:
    """端末と同じ出力先を使う場合、子の交互出力は見出しの後へ書込順で届く。"""
    code = (
        "import sys, update_dotfiles; "
        f"rc, _ = update_dotfiles._run_step(3, 5, 'apply', [sys.executable, '-c', {_INTERLEAVED_CHILD_CODE!r}, '4']); "
        "raise SystemExit(rc)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=pathlib.Path(__file__).resolve().parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 4
    assert result.stdout.splitlines() == ["=== [3/5] apply ===", "out1", "err1", "out2", "err2"]


def test_save_worktree_uses_repository_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """worktree退避は同じPythonからcheckoutの共通実装を起動する。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _fake_popen({}, calls, stdout_by_command={sys.executable: "refs/worktree/test\n"}),
    )

    assert update_dotfiles._save_worktree("test") == "refs/worktree/test"  # pylint: disable=protected-access
    assert calls[0][0] == sys.executable
    assert pathlib.Path(calls[0][1]) == pathlib.Path(update_dotfiles.__file__).with_name("update_dotfiles_stash.py")
    assert calls[0][2:] == ["save", "--label=test"]


def test_alternate_index_is_passed_only_to_update_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """本体Gitと退避だけが代替indexを受け取り、pluginとchezmoiは通常環境を使う。"""
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    alternate = tmp_path / "alternate-index"
    monkeypatch.setattr(update_dotfiles, "_update_index", alternate)
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, environments=environments))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, environments=environments))
    assert update_dotfiles._run_git_pull(1, 4) == 0
    assert update_dotfiles._run_worktree_stash("protect-old") is not None
    assert update_dotfiles._git_capture("status").returncode == 0
    assert update_dotfiles._run_git_pull(1, 4, plugin=tmp_path / "plugin") == 0
    assert update_dotfiles._run_step(2, 4, "init", ["chezmoi", "init"])[0] == 0
    assert all(env["GIT_INDEX_FILE"] == str(alternate) for env in environments[:3])
    assert all("GIT_INDEX_FILE" not in env for env in environments[3:])


def test_save_worktree_reports_launcher_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """ランチャー起動不能はtracebackを抑止して文脈付き失敗にする。"""
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))

    assert update_dotfiles._save_worktree("test") is None  # pylint: disable=protected-access
    captured = capsys.readouterr()
    assert "未コミット内容の退避操作を開始できませんでした" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("always_timeout", [False, True])
def test_old_stash_timeout_continues_update_and_records_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    always_timeout: bool,
) -> None:
    """旧退避の保護が停止しても有限回の回収で戻り、失敗を記録してpullへ進み、終了コード1で終える。"""
    ref = "refs/worktree/update-dotfiles-old"
    calls: list[list[str]] = []
    timeouts: list[int | None] = []
    process = _TimeoutPopen(
        ["atk", "worktree-stash", "protect", ref],
        calls,
        output_text="partial stdout",
        error_text="partial stderr",
        communicate_timeouts=timeouts,
        always_timeout=always_timeout,
    )
    monkeypatch.setattr(update_dotfiles, "_update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
    monkeypatch.setattr(update_dotfiles, "_git_operation_in_progress", lambda: False)

    def git_capture(*args: str) -> subprocess.CompletedProcess[str]:
        if args[-1] == "index":
            return subprocess.CompletedProcess([], 0, str(tmp_path / "index"), "")
        if "status" in args or "--others" in args:
            return subprocess.CompletedProcess([], 0, "", "")
        return subprocess.CompletedProcess([], 0, ref + "\n", "")

    monkeypatch.setattr(update_dotfiles, "_git_capture", git_capture)
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
    pulls: list[int] = []

    def fake_pull(*_args: object, **_kwargs: object) -> int:
        pulls.append(1)
        return 0

    monkeypatch.setattr(update_dotfiles, "_run_git_pull", fake_pull)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    chezmoi_calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run({}, chezmoi_calls))
    killed: list[int] = []
    monkeypatch.setattr(update_dotfiles, "_kill_process_tree", lambda child: killed.append(child.pid))

    assert update_dotfiles.main() == 1
    assert timeouts == [120, 30]
    assert killed == [process.pid]
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    assert pulls == [1]
    assert [call[1] for call in chezmoi_calls] == ["init", "status", "diff", "apply"]
    assert report["failed_stage"] == "過去の退避データの整理"
    assert "120秒以内" in report["stderr_tail"]
    captured = capsys.readouterr()
    assert "worktree-stash" not in captured.out
    assert "過去の退避データを確認できませんでしたが、更新を続けます" in captured.err
    assert "git statusとupdate-dotfiles logs" in captured.err
    log = update_dotfiles._LOG_PATH.read_text(encoding="utf-8")
    assert "退避操作開始" in log
    assert "退避操作timeout" in log
    if not always_timeout:
        assert "partial stdout" in log
        assert "partial stderr" in report["stderr_tail"]


@pytest.fixture(name="logs_path")
def _logs_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """CLIの状態保存先を両OSで隔離する。"""
    for variable in (
        "HOME",
        "USERPROFILE",
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
    ):
        monkeypatch.setenv(variable, str(tmp_path / variable))
    # WindowsのKnownFolder APIも子へ継承する公開overrideで隔離する。
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", os.environ["LOCALAPPDATA"])
    state_dir = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))
    assert state_dir.is_relative_to(tmp_path)
    state_dir.mkdir(parents=True)
    return state_dir / "update-dotfiles.log"


def _run_logs_cli(arguments: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    """実スクリプトのCLIを新しいプロセスで実行する。"""
    return subprocess.run(
        [sys.executable, str(pathlib.Path(update_dotfiles.__file__).resolve()), *(arguments or ["logs"])],
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_logs_cli_shows_latest_update_only(logs_path: pathlib.Path) -> None:
    """単独post-applyと以前の更新を表示へ混ぜない。"""
    latest = "2026-10-01 12:00:00,000 run=100-2 INFO update-dotfiles開始: root=test\n"
    logs_path.write_text(
        "2026-10-01 11:00:00,000 run=9-1 INFO 以前の更新\n"
        + latest
        + "2026-10-01 12:00:01,000 run=post-apply-999 INFO 単独起動\n単独の続き\n",
        encoding="utf-8",
    )

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == latest
    assert not result.stderr


@pytest.mark.parametrize("exit_code", [0, 7])
def test_logs_cli_preserves_rotated_multiline_records(logs_path: pathlib.Path, exit_code: int) -> None:
    """成功・失敗の実行を世代境界から読み、diffとpost-applyの続きも保つ。"""
    first = "2026-10-01 12:00:00,000 run=100-2 INFO update-dotfiles開始: root=test\n"
    diff = "2026-10-01 12:00:01,000 run=100-2 INFO chezmoi diffの出力:\n-old\n+日本語の差分\n"
    post_apply = "2026-10-01 12:00:02,000 run=100-2 WARNING post-applyの記録\nTraceback: 保存した続き\n"
    finish = f"2026-10-01 12:00:03,000 run=100-2 INFO update-dotfiles終了: exit={exit_code}\n"
    logs_path.with_name(f"{logs_path.name}.3").write_text(
        "2026-10-01 11:00:00,000 run=9-1 INFO 以前の更新\n" + first, encoding="utf-8"
    )
    logs_path.with_name(f"{logs_path.name}.1").write_text(diff, encoding="utf-8")
    logs_path.write_text(post_apply + finish, encoding="utf-8")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == first + diff + post_apply + finish
    assert not result.stderr


def test_logs_cli_reads_update_when_start_record_has_rotated_away(logs_path: pathlib.Path) -> None:
    """開始記録が残らない更新も、保存された記録を表示する。"""
    saved = "2026-10-01 12:00:03,000 run=100-2 INFO update-dotfiles終了: exit=7\n"
    logs_path.write_text(saved, encoding="utf-8")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == saved


@pytest.mark.parametrize("contents", [None, "", "2026-10-01 12:00:00,000 run=post-apply-999 INFO 単独起動\n"])
def test_logs_cli_reports_no_saved_update(logs_path: pathlib.Path, contents: str | None) -> None:
    """保存された更新実行が無いことをユーザーへ案内する。"""
    if contents is not None:
        logs_path.write_text(contents, encoding="utf-8")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert result.stdout == "保存済みの更新ログはありません。\n"
    assert not result.stderr


def test_logs_cli_does_not_write_state(logs_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """表示によるログ追記、同期結果更新、ロック生成と更新開始を検出する。"""
    logs_path.write_text("2026-10-01 12:00:00,000 run=100-2 INFO 保存済みログ\n", encoding="utf-8")
    report = logs_path.with_name("sync-report.json")
    report.write_text('{"status": "failed"}', encoding="utf-8")
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in logs_path.parent.iterdir()}
    monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", "invalid")

    result = _run_logs_cli()

    assert result.returncode == 0, result.stderr
    assert "保存済みログ" in result.stdout
    after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in logs_path.parent.iterdir()}
    assert after == before


def test_logs_cli_reports_read_failure(logs_path: pathlib.Path) -> None:
    """読めない保存先を空ログ扱いせず、対象と失敗を示す。"""
    logs_path.mkdir()

    result = _run_logs_cli()

    assert result.returncode == 1
    assert not result.stdout
    assert str(logs_path) in result.stderr
    assert "読み取れませんでした" in result.stderr
    assert "Traceback" not in result.stderr


def test_help_describes_logs(logs_path: pathlib.Path) -> None:
    """ヘルプの表示は正常終了し、更新を開始しない。"""
    result = _run_logs_cli(["--help"])

    assert result.returncode == 0, result.stderr
    assert not list(logs_path.parent.iterdir())


class TestFiveStepsInOrder:
    """diffを含む5処理が順に呼ばれ、成功時にexit code 0を返すことを検証する。"""

    def test_all_steps_succeed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run(
                {},
                calls,
                stdout_by_command={
                    "status": " M private-status-value\n",
                    "diff": "private-diff-value\n",
                },
            ),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert calls[0][:2] == ["chezmoi", "git"]
        assert calls[1][:2] == ["chezmoi", "init"]
        assert calls[2][:2] == ["chezmoi", "status"]
        assert calls[3][:2] == ["chezmoi", "diff"]
        assert calls[4][:2] == ["chezmoi", "apply"]
        assert "--quiet" in calls[0]
        assert "--force" in calls[4]
        log_text = update_dotfiles._LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert "update-dotfiles開始" in log_text
        expected_stages = [
            "1/5 git pull",
            "2/5 chezmoi init (テンプレート再展開)",
            "3/5 chezmoi status (apply予定のファイル)",
            "4/5 chezmoi diff (上書き前の差分)",
            "5/5 chezmoi apply (post-apply実行)",
        ]
        assert [line.partition("stage開始: ")[2] for line in log_text.splitlines() if "stage開始: " in line] == expected_stages
        assert [
            line.partition("stage終了: ")[2].partition(" exit=")[0] for line in log_text.splitlines() if "stage終了: " in line
        ] == expected_stages
        assert "update-dotfiles終了: exit=0" in log_text
        assert "private-status-value" not in log_text
        assert "private-diff-value" in log_text
        output = capsys.readouterr().out
        assert "=== [4/4] chezmoi apply" in output
        assert "=== [4/4] chezmoi diff" not in output

    def test_diff_precedes_forced_apply(
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

        assert update_dotfiles.main() == 0
        assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
        assert calls[3] == ["chezmoi", "diff", "--no-pager"]
        assert calls[4] == ["chezmoi", "apply", "--force"]
        captured = capsys.readouterr()
        assert "chezmoi diff" not in captured.out
        assert "diff output\n" not in captured.out
        assert "=== [4/4] chezmoi apply" in captured.out
        assert "diff output\n" in update_dotfiles._LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert not captured.err


def test_child_python_output_uses_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "cp932")

    assert update_dotfiles._child_env()["PYTHONIOENCODING"] == "utf-8:replace"  # noqa: SLF001


def test_missing_persistent_log_continues_updates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, sync_report_path: pathlib.Path
) -> None:
    """永続ログを開始できなくても更新を続け、終了コード1と失敗した段を残す。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(update_dotfiles, "_configure_persistent_log", lambda _run_id: contextlib.nullcontext(None))
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

    assert update_dotfiles.main() == 1
    assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
    assert json.loads(sync_report_path.read_text(encoding="utf-8"))["failed_stage"] == "永続ログの開始"


class TestStepFailureStopsExecution:
    """後続の前提になる段の失敗でそのexit codeを返し、以降の段を呼ばないことを検証する。

    失敗しても前提を壊さない段は、後続の段を続けて終了コード1で終える。
    """

    def test_step2_failure_skips_step3_and_4(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"init": 3}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 3
        assert len(calls) == 2
        assert calls[1][:2] == ["chezmoi", "init"]

    def test_dotfiles_pull_failure_stops_before_init(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """dotfiles自身のpullの失敗は、不確定なソースを配備しないよう以降の段を止める。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({"git": 4}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 4
        assert [call[1] for call in calls] == ["git"]

    @pytest.mark.parametrize(("failed_command", "exit_code"), [("status", 2), ("diff", 7)])
    def test_display_stage_failure_continues_to_apply(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        failed_command: str,
        exit_code: int,
    ) -> None:
        """表示専用の`chezmoi status`・`chezmoi diff`の失敗は記録して`chezmoi apply`まで続け、終了コード1で終える。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({failed_command: exit_code}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
        err = capsys.readouterr().err
        assert "失敗しても後続の段を続けた段:" in err
        assert f"chezmoi {failed_command}" in err

    def test_failure_guidance_follows_continued_failure_list_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """失敗の案内は続行した段の一覧の後に1回だけ出る。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2, "diff": 7}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert err.count("次の操作:") == 1
        assert err.count("永続ログ:") == 1
        assert err.count("失敗の詳細は update-dotfiles logs") == 1
        assert err.index("失敗しても後続の段を続けた段:") < err.index("次の操作:") < err.index("永続ログ:")

    def test_post_apply_failure_shows_next_action_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """post-applyの失敗で`chezmoi apply`が失敗した実行でも、次の操作と永続ログの所在が画面に各1回出る。

        post-applyは`update-dotfiles`経由では失敗後の案内を出さないため、終了時の案内が唯一の次の操作になる。
        """
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"apply": 1}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert err.count("次の操作:") == 1
        assert err.count("永続ログ:") == 1

    def test_multiple_continued_failures_are_all_listed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        sync_report_path: pathlib.Path,
    ) -> None:
        """続行した複数の失敗を一覧に並べ、同期記録へ全ての段の名前を残す。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2, "diff": 7}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert "chezmoi status" in err and "chezmoi diff" in err
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert "chezmoi status" in report["failed_stage"] and "chezmoi diff" in report["failed_stage"]

    def test_apply_failure_after_continued_failure_keeps_apply_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """前提となる`chezmoi apply`が失敗した場合は、その終了コードを返し、続行した失敗も一覧に並べる。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2, "apply": 5}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 5
        assert "chezmoi status" in capsys.readouterr().err

    def test_mise_lock_discard_failure_continues_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`mise.lock`の差分を破棄できなくても、pullへ進み、失敗を記録して終了コード1で終える。"""
        local, _seed, _remote = _create_git_pair(tmp_path)
        monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
        monkeypatch.setattr(update_dotfiles, "_update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
        monkeypatch.setattr(update_dotfiles, "_discard_mise_lock_changes", lambda: False)
        _patch_real_pull(monkeypatch, local)
        chezmoi_calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({}, chezmoi_calls))

        assert update_dotfiles.main() == 1
        assert [call[1] for call in chezmoi_calls if call[0] == "chezmoi"] == ["init", "status", "diff", "apply"]
        err = capsys.readouterr().err
        assert "mise.lockの差分を破棄できませんでしたが、更新を続けます" in err
        assert "mise.lockの差分の破棄" in err


class TestSyncReport:
    """同期結果の記録が、次のセッションの続行判定へ必要な内容を残すことを検証する。"""

    def test_successful_run_is_recorded_as_succeeded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        sync_report_path: pathlib.Path,
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0

        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["status"] == "succeeded"
        assert report["exit_code"] == 0
        assert report["failed_stage"] is None
        assert report["stderr_tail"] is None
        assert report["run_id"]
        assert report["started_at"] and report["finished_at"]

    def test_failed_stage_and_stderr_are_recorded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        sync_report_path: pathlib.Path,
    ) -> None:
        """継承する段の失敗は、段の名前と終了コードから読み取れる。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({"apply": 5}, calls, stderr_by_command={"apply": "対象ツールの導入に失敗しました\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 5

        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["status"] == "failed"
        assert report["exit_code"] == 5
        assert "chezmoi apply" in report["failed_stage"]
        assert report["stderr_tail"] is None

    @pytest.mark.parametrize(
        ("failed_command", "log_stage", "stage_title"),
        [
            ("git", "1/5", "git pull"),
            ("init", "2/5", "chezmoi init (テンプレート再展開)"),
            ("status", "3/5", "chezmoi status (apply予定のファイル)"),
            ("diff", "4/5", "chezmoi diff (上書き前の差分)"),
            ("apply", "5/5", "chezmoi apply (post-apply実行)"),
        ],
    )
    def test_launch_failure_keeps_log_stage_and_report_title(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        sync_report_path: pathlib.Path,
        failed_command: str,
        log_stage: str,
        stage_title: str,
    ) -> None:
        calls: list[list[str]] = []
        successful_popen = _fake_popen({}, calls)

        def popen(argv: list[str], **kwargs: object) -> _FakePopen:
            if failed_command == "git":
                raise OSError("起動不能")
            return successful_popen(argv, **kwargs)

        successful_run = _fake_run({}, calls)

        def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if argv[1] == failed_command:
                raise OSError("起動不能")
            return successful_run(argv, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", popen)
        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        log_text = update_dotfiles._LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert f"stage起動失敗: {log_stage} {stage_title}" in log_text
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["failed_stage"] == stage_title


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
        assert captured.err == (
            "fatal: pull failed\n次の操作: 失敗理由を確認し、update-dotfilesを再実行してください。\n"
            f"永続ログ: {update_dotfiles._LOG_PATH}\n"  # noqa: SLF001
            "失敗の詳細は update-dotfiles logs で直近1回の実行ログを表示して確認できる。\n"
        )
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

    def test_diff_stderr_is_forwarded(
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

        assert update_dotfiles.main() == 0
        assert capsys.readouterr().err == "chezmoi diff warning\n"


@pytest.mark.parametrize("argument", ["--force", "--unknown"])
def test_removed_or_unknown_argument_exits_2(argument: str) -> None:
    """廃止済みまたは未知の引数はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        update_dotfiles.main([argument])
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
    # 端末終了のSIGHUPや自然終了で、製品の子孫回収漏れを隠さない。
    child = subprocess.Popen([
        sys.executable, "-c",
        "import signal, time; signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)",
    ])
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
) -> tuple[int, str, pathlib.Path, float]:
    """疑似端末内で実物の`_run_git_pull`を実行し、終了コードと出力を返す。"""
    executable = tmp_path / "bin" / "chezmoi"
    executable.parent.mkdir()
    executable.write_text(_FAKE_CHEZMOI_CODE, encoding="utf-8")
    executable.chmod(0o755)
    descendant_pid_path = tmp_path / "descendant.pid"
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_MODE", mode)
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_PID_PATH", str(descendant_pid_path))

    runner = pathlib.Path(__file__).with_name("_update_dotfiles_pty_runner.py")
    with subprocess.Popen(  # noqa: S603
        [sys.executable, "-W", "error::DeprecationWarning", str(runner)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    ) as process:
        try:
            stdout, stderr = process.communicate(json.dumps({"timeout": timeout, "input_text": input_text}), timeout=30)
        except subprocess.TimeoutExpired:
            update_dotfiles._kill_process_tree(process)  # pylint: disable=protected-access
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"端末ランナーが30秒以内に終了しなかった: {stdout}\n{stderr}")
    assert process.returncode == 0, stderr
    assert not stderr, stderr
    result = json.loads(stdout)
    assert "DeprecationWarning" not in result["output"], result["output"]
    assert not result["descendant_running"], "製品のgit pullが終了した時点で孫プロセスが残っている"
    return result["returncode"], result["output"], descendant_pid_path, result["elapsed"]


@pytest.mark.skipif(sys.platform == "win32", reason="ptyと/dev/ttyを使用するLinux専用のテスト")
@pytest.mark.filterwarnings("error::DeprecationWarning")
@pytest.mark.parametrize("timeout", [30, None])
def test_git_pull_preserves_terminal_interaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    timeout: int | None,
) -> None:
    """上限の有無にかかわらず、子が制御端末から入力を受け取る。

    上限ありのテストは端末入力の受け渡しを検査するため、
    並行実行の負荷でも到達しない秒数を渡す。上限の到達側は
    `test_git_pull_timeout_terminates_stream_holding_descendant`が検査する。
    """
    returncode, output, _pid_path, _elapsed = _run_git_pull_in_pty(
        tmp_path,
        monkeypatch,
        mode="interactive",
        timeout=timeout,
        input_text="secret-value",
    )
    assert returncode == 0
    assert "passphrase:" in output


@pytest.mark.skipif(sys.platform == "win32", reason="ptyを使用するLinux専用のテスト")
@pytest.mark.filterwarnings("error::DeprecationWarning")
def test_git_pull_timeout_terminates_stream_holding_descendant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """標準ストリームを継承する孫も終了し、回収上限の内側で復帰する。"""
    started = time.monotonic()
    returncode, output, pid_path, elapsed = _run_git_pull_in_pty(
        tmp_path,
        monkeypatch,
        mode="hang",
        timeout=5,
    )
    assert returncode == 1
    assert elapsed < 12
    del output
    pid_deadline = time.monotonic() + 5
    while not pid_path.exists():
        if time.monotonic() >= pid_deadline:
            pytest.fail(f"孫プロセスのPIDファイルが5秒以内に作成されなかった（開始から{time.monotonic() - started:.3f}秒）")
        time.sleep(0.05)
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


def _git(repo: pathlib.Path, *arguments: str) -> str:
    """テスト用Gitリポジトリでコマンドを実行し、標準出力を返す。"""
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _create_git_pair(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """ローカル作業ツリー、上流更新用作業ツリー、bare上流を作成する。"""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    local = tmp_path / "local"
    subprocess.run(["git", "init", "--bare", "--initial-branch=master", str(remote)], check=True, capture_output=True)  # noqa: S603
    subprocess.run(["git", "init", "--initial-branch=master", str(seed)], check=True, capture_output=True)  # noqa: S603
    _git(seed, "config", "user.name", "Test User")
    _git(seed, "config", "user.email", "test@example.invalid")
    (seed / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "tracked.txt")
    _git(seed, "commit", "-m", "base")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "master")
    subprocess.run(["git", "clone", str(remote), str(local)], check=True, capture_output=True)  # noqa: S603
    _git(local, "config", "user.name", "Test User")
    _git(local, "config", "user.email", "test@example.invalid")
    return local, seed, remote


def _patch_real_pull(monkeypatch: pytest.MonkeyPatch, local: pathlib.Path) -> None:
    """競合回復テストのpullだけを一時Gitリポジトリへ向ける。"""

    monkeypatch.setenv("PATH", os.pathsep.join((str(_REPOSITORY_ATK_BIN), os.environ.get("PATH", ""))))

    def _pull(_step_no: int, _total: int, *, timeout: int | None) -> int:
        del timeout
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(local), "pull", "--rebase", "--quiet"],
            check=False,
            env=update_dotfiles._git_env(),
        ).returncode

    monkeypatch.setattr(update_dotfiles, "_run_git_pull", _pull)


@pytest.fixture(name="real_stash_runtime")
def _real_stash_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, share_package_caches: None) -> None:
    """実CLIのランタイム解決はホストへ戻し、CLI自身の保存先は一時領域へ限定する。"""
    del share_package_caches
    restore_host_environment(monkeypatch)
    monkeypatch.setenv("AGENT_TOOLKIT_RUNTIME_DATA", str(tmp_path / "runtime"))


@pytest.mark.usefixtures("real_stash_runtime")
class TestGitConflictRecovery:
    """一時Git上流でcommit競合と未コミット復元競合からの回復を検証する。"""

    @pytest.mark.parametrize("dirty", [False, True])
    def test_rebase_conflict_preserves_original_commit_and_tracks_upstream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, dirty: bool
    ) -> None:
        local, seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("local commit\n", encoding="utf-8")
        _git(local, "add", "tracked.txt")
        _git(local, "commit", "-m", "local")
        original_head = _git(local, "rev-parse", "HEAD")
        if dirty:
            (local / "staged.txt").write_text("staged\n", encoding="utf-8")
            _git(local, "add", "staged.txt")
            (local / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
            (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        (seed / "tracked.txt").write_text("upstream\n", encoding="utf-8")
        _git(seed, "add", "tracked.txt")
        _git(seed, "commit", "-m", "upstream")
        _git(seed, "push")
        monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        recovery_refs = _git(local, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads").splitlines()
        recovery = next(line.split()[1] for line in recovery_refs if "refs/heads/update-dotfiles-recovery-" in line)
        assert result == 0
        assert _git(local, "rev-parse", "HEAD") == _git(local, "rev-parse", "@{upstream}")
        assert recovery == original_head
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "upstream\n"
        if dirty:
            _assert_saved_changes_survive_gc(local, tmp_path)

    @pytest.mark.parametrize(
        "untracked_name",
        [
            "untracked.txt",
            "日本語 space.txt",
            pytest.param(
                " 日本語 space\n改行.txt ",
                marks=pytest.mark.skipif(os.name == "nt", reason="Windowsのファイル名は改行と末尾空白を許可しない"),
            ),
        ],
    )
    def test_dirty_restore_conflict_keeps_recoverable_ref_and_clean_upstream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, untracked_name: str
    ) -> None:
        local, seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (local / "staged.txt").write_text("staged\n", encoding="utf-8")
        (local / untracked_name).write_text("untracked\n", encoding="utf-8")
        _git(local, "add", "staged.txt")
        (seed / "tracked.txt").write_text("upstream\n", encoding="utf-8")
        _git(seed, "add", "tracked.txt")
        _git(seed, "commit", "-m", "upstream")
        _git(seed, "push")
        monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        worktree_refs = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree").splitlines()
        stash_ref = next(ref for ref in worktree_refs if ref.startswith("refs/worktree/update-dotfiles-"))
        saved_paths = git_repository.run_git(
            local, "stash", "show", "--include-untracked", "--name-only", "-z", stash_ref
        ).stdout.split("\0")
        assert result == 0
        assert _git(local, "status", "--porcelain=v1") == ""
        assert _git(local, "rev-parse", "HEAD") == _git(local, "rev-parse", "@{upstream}")
        assert {"tracked.txt", "staged.txt", untracked_name} <= set(saved_paths)
        _assert_saved_changes_survive_gc(local, tmp_path, untracked_name=untracked_name)


def _assert_saved_changes_survive_gc(
    local: pathlib.Path, tmp_path: pathlib.Path, *, untracked_name: str = "untracked.txt"
) -> None:
    """未復元の3状態を別worktreeのGC後も復旧できる。"""
    other = tmp_path / "gc-worktree"
    _git(local, "worktree", "add", "-b", "gc-worktree", str(other))
    _git(other, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
    _git(other, "gc", "--prune=now")
    ref = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree").splitlines()[0]
    _git(other, "checkout", "--detach", f"main-worktree/{ref}^1")
    _git(other, "stash", "apply", "--index", f"main-worktree/{ref}")
    assert (other / "tracked.txt").read_text(encoding="utf-8") in {"dirty\n", "unstaged\n"}
    assert (other / "staged.txt").read_text(encoding="utf-8") == "staged\n"
    assert (other / untracked_name).read_text(encoding="utf-8") == "untracked\n"
    assert "A  staged.txt" in _git(other, "status", "--porcelain=v1").splitlines()


def _track_mise_lock(local: pathlib.Path, seed: pathlib.Path) -> None:
    """上流へ`mise.lock`を追加し、ローカルへ取り込んでから上流側で更新する。"""
    (seed / "mise.lock").write_text("lock v1\n", encoding="utf-8")
    _git(seed, "add", "mise.lock")
    _git(seed, "commit", "-m", "lock v1")
    _git(seed, "push")
    _git(local, "pull", "--quiet")
    (seed / "mise.lock").write_text("lock v2\n", encoding="utf-8")
    _git(seed, "add", "mise.lock")
    _git(seed, "commit", "-m", "lock v2")
    _git(seed, "push")


@pytest.mark.usefixtures("real_stash_runtime")
class TestStashLifecycle:
    """公開された更新コマンドで復元・回収・旧退避保護の寿命を確認する。"""

    @pytest.mark.parametrize("pull_code,cleanup_failure", [(0, False), (7, False), (0, True), (7, True)])
    def test_restored_changes_survive_cleanup_outcome(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        pull_code: int,
        cleanup_failure: bool,
    ) -> None:
        local, _seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
        (local / "staged.txt").write_text("staged\n", encoding="utf-8")
        _git(local, "add", "staged.txt")
        (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
        monkeypatch.setattr(update_dotfiles, "_update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
        monkeypatch.setattr(update_dotfiles, "_run_git_pull", lambda *_args, **_kwargs: pull_code)
        monkeypatch.setattr(update_dotfiles, "_run_step", lambda *_args, **_kwargs: (0, ""))
        monkeypatch.setattr(update_dotfiles, "_pause_codex_daemon", lambda: None)
        run_stash = update_dotfiles._run_worktree_stash

        def fail_drop(*arguments: str) -> subprocess.CompletedProcess[str] | None:
            if cleanup_failure and arguments[0] == "drop":
                return None
            return run_stash(*arguments)

        monkeypatch.setattr(update_dotfiles, "_run_worktree_stash", fail_drop)
        assert update_dotfiles.main() == (pull_code or int(cleanup_failure))
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "unstaged\n"
        assert (local / "staged.txt").read_text(encoding="utf-8") == "staged\n"
        assert (local / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        assert set(_git(local, "status", "--porcelain=v1").splitlines()) == {
            " M tracked.txt",
            "A  staged.txt",
            "?? untracked.txt",
        }
        refs = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree", "refs/atk/worktree-stash").splitlines()
        assert len(refs) == (2 if cleanup_failure else 0)
        if cleanup_failure:
            diagnostic = capsys.readouterr().err
            ref = next(ref for ref in refs if ref.startswith("refs/worktree/"))
            assert "復元は成功" in diagnostic
            assert f"atk worktree-stash drop {ref}" in diagnostic
            assert run_stash("drop", ref) is not None
            assert _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree", "refs/atk/worktree-stash") == ""

    def test_old_ref_is_protected_before_pull_gc(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        local, _seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("legacy\n", encoding="utf-8")
        _git(local, "stash", "push")
        ref = "refs/worktree/update-dotfiles-old"
        _git(local, "update-ref", ref, _git(local, "rev-parse", "refs/stash"))
        _git(local, "stash", "drop")
        other = tmp_path / "other"
        _git(local, "worktree", "add", "-b", "other", str(other))
        monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)

        def gc_during_pull(*_args: object, **_kwargs: object) -> int:
            _git(other, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
            _git(other, "gc", "--prune=now")
            return 0

        monkeypatch.setattr(update_dotfiles, "_run_git_pull", gc_during_pull)
        assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=30) == 0
        _git(local, "stash", "apply", "--index", ref)
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "legacy\n"


@pytest.mark.usefixtures("real_stash_runtime")
class TestMiseLockDiscard:
    """pull前に`mise.lock`の差分を破棄し、他の未コミット内容だけを退避・復元する。"""

    def test_mise_lock_only_change_is_discarded_without_stash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`mise.lock`だけの差分では退避用refを作成せず、更新後の`mise.lock`が上流と一致する。"""
        local, seed, _remote = _create_git_pair(tmp_path)
        _track_mise_lock(local, seed)
        (local / "mise.lock").write_text("rewritten by mise\n", encoding="utf-8")
        _git(local, "add", "mise.lock")
        monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        assert result == 0
        assert _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree") == ""
        assert _git(local, "status", "--porcelain=v1") == ""
        assert (local / "mise.lock").read_text(encoding="utf-8") == "lock v2\n"
        assert "未コミット内容" not in capsys.readouterr().out

    def test_other_changes_are_stashed_and_restored_without_mise_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """他のファイルの差分（stage済み・未追跡を含む）は退避・復元し、退避内容に`mise.lock`を含めない。"""
        local, seed, _remote = _create_git_pair(tmp_path)
        _track_mise_lock(local, seed)
        (local / "mise.lock").write_text("rewritten by mise\n", encoding="utf-8")
        (local / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (local / "staged.txt").write_text("staged\n", encoding="utf-8")
        _git(local, "add", "staged.txt")
        (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        saved_paths: set[str] = set()
        original_restore = update_dotfiles._restore_worktree

        def observe_restore(ref: str) -> bool:
            saved_paths.update(_git(local, "stash", "show", "--include-untracked", "--name-only", ref).splitlines())
            return original_restore(ref)

        monkeypatch.setattr(update_dotfiles, "_restore_worktree", observe_restore)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        assert _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree", "refs/atk/worktree-stash") == ""
        assert result == 0
        assert {"tracked.txt", "staged.txt", "untracked.txt"} <= saved_paths
        assert "mise.lock" not in saved_paths
        assert (local / "mise.lock").read_text(encoding="utf-8") == "lock v2\n"
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "dirty\n"
        assert (local / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        status = _git(local, "status", "--porcelain=v1").splitlines()
        assert "A  staged.txt" in status
        assert not [line for line in status if line.endswith("mise.lock")]


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


def test_stage_heading_precedes_child_output_when_stdout_is_not_a_terminal() -> None:
    """標準出力がパイプの場合も、段見出しがその段の子プロセスの出力より前に並ぶ。

    自動更新サービスのjournalや保存した出力では標準出力がブロックバッファになり、
    子プロセスが同じ出力先へ直接書くと、見出しが後段の出力の後にまとまって現れる。
    """
    libexec_dir = pathlib.Path(__file__).resolve().parent
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(libexec_dir)!r})\n"
        "import update_dotfiles\n"
        "update_dotfiles._run_step(1, 4, 'stage', [sys.executable, '-c', 'print(\"child\", flush=True)'])\n"
        "update_dotfiles._run_step(2, 4, 'next', [sys.executable, '-c', 'print(\"child2\", flush=True)'])\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, check=False, encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["=== [1/4] stage ===", "child", "=== [2/4] next ===", "child2"]


def _codex(pid: int, label: str, role: str) -> update_dotfiles.codex_processes.CodexProcess:
    return update_dotfiles.codex_processes.CodexProcess(pid, label, cast(Any, role))


_DAEMON = _codex(10, "codex app-server --managed-daemon", "managed-daemon")
_DAEMON_HELPER = _codex(11, "codex-code-mode-host", "managed-daemon")
_UPDATE_LOOP = _codex(12, "codex app-server", "update-loop")
_STDIO_DELEGATION = _codex(13, "codex app-server", "session")


def _dirty_snapshot(local: pathlib.Path) -> tuple[str, str, str, dict[str, bytes]]:
    """HEAD・stage・未stage・未追跡を読取りだけで比較する。"""
    return (
        _git(local, "rev-parse", "HEAD"),
        _git(local, "diff", "--cached", "--binary"),
        _git(local, "diff", "--binary"),
        {path.name: path.read_bytes() for path in local.glob("*.txt")},
    )


def _prepare_dirty(local: pathlib.Path) -> None:
    """3種類の未コミット状態を用意する。"""
    (local / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(local, "add", "staged.txt")
    (local / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")


def _wait_process_ready(process: subprocess.Popen[str], ready: pathlib.Path) -> None:
    """起動したプロセスが有限時間で同期点へ到達したことを確かめる。"""
    deadline = time.monotonic() + 15
    while not ready.exists():
        assert process.poll() is None
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.mark.skipif(sys.platform == "win32", reason="使用中cwdの削除はPOSIX上で検証する")
def test_update_ignores_unrelated_git_with_removed_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """別Gitのcwdが走査後に消えても、対象rootの所有取得を妨げない。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    directory = tmp_path / "unrelated-git-cwd"
    directory.mkdir()
    ready, release = tmp_path / "ready", tmp_path / "release"
    gate = tmp_path / "gate.py"
    gate.write_text(
        "import pathlib, sys, time\n"
        "ready, release = map(pathlib.Path, sys.argv[1:])\n"
        "ready.touch()\n"
        "deadline = time.monotonic() + 30\n"
        "while not release.exists() and time.monotonic() < deadline: time.sleep(0.02)\n",
        encoding="utf-8",
    )
    alias = "!" + " ".join(shlex.quote(str(value)) for value in (sys.executable, gate, ready, release))
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles, "_update_owned_git", lambda *_args, **_kwargs: 0)
    with subprocess.Popen(
        ["git", "-C", str(directory), "-c", f"alias.wait={alias}", "wait"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            _wait_process_ready(process, ready)
            snapshot = next(candidate for candidate in psutil.process_iter(["name", "cmdline"]) if candidate.pid == process.pid)
            directory.rmdir()
            assert snapshot.cwd() == str(directory)
            # 最初の走査後、index取得までの間に消えたcwdを2回目の検査へ渡す。
            scans = iter([[], [snapshot]])
            monkeypatch.setattr(update_dotfiles.psutil, "process_iter", lambda *_args, **_kwargs: iter(next(scans)))

            assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == 0
        finally:
            release.touch()
            stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, (stdout, stderr)
    assert not list((local / ".git").glob("update-dotfiles-index-*"))


@pytest.mark.parametrize("phase", ["pre-commit", "editor", "commit-a"])
@pytest.mark.parametrize("scope", ["same", "other-worktree", "unrelated"])
def test_update_skips_while_git_commit_in_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    phase: str,
    host_environ: Callable[[], dict[str, str]],
    scope: str,
) -> None:
    """実commitのhook・編集区間を保持し、最初の変更前の見送りを検査する。"""
    local, seed, _remote = _create_git_pair(tmp_path)
    target = local
    if scope == "other-worktree":
        target = tmp_path / "other"
        _git(local, "worktree", "add", "-b", "other", str(target))
    elif scope == "unrelated":
        target = seed
    _prepare_dirty(local)
    original = _dirty_snapshot(local)
    ready, release = tmp_path / "ready", tmp_path / "release"
    gate = tmp_path / "gate.py"
    gate.write_text(
        "import pathlib, sys, time\n"
        "ready, release, tracked = map(pathlib.Path, sys.argv[1:4])\n"
        "original = tracked.read_bytes()\n"
        "if sys.argv[4] == 'pre-commit': tracked.write_bytes(b'base\\n')\n"
        "ready.touch()\n"
        "deadline = time.monotonic() + 30\n"
        "while not release.exists() and time.monotonic() < deadline: time.sleep(0.02)\n"
        "tracked.write_bytes(original)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    command = " ".join(
        shlex.quote(str(value).replace("\\", "/"))
        for value in (
            sys.executable,
            gate,
            ready,
            release,
            local / "tracked.txt",
            phase,
        )
    )
    env = host_environ()
    if phase == "editor":
        env["GIT_EDITOR"] = command
        arguments = ["commit"]
    else:
        hook = local / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\n" + command + "\n", encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        arguments = ["commit", *(["-a"] if phase == "commit-a" else []), "-m", "同期点"]
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", target)
    monkeypatch.setattr(update_dotfiles, "_protect_old_worktree_stashes", lambda: pytest.fail("変更開始"))
    if scope != "same":
        monkeypatch.setattr(update_dotfiles, "_update_owned_git", lambda *_args, **_kwargs: 0)
    with subprocess.Popen(
        ["git", "-C", str(local), *arguments],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            _wait_process_ready(process, ready)
            before = _dirty_snapshot(local)
            assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == int(scope == "same")
            assert _dirty_snapshot(local) == before
        finally:
            release.touch()
            stdout, stderr = process.communicate(timeout=15)
    assert process.returncode != 0, (stdout, stderr)
    assert _dirty_snapshot(local) == original
    assert not list((local / ".git").glob("update-dotfiles-index-*"))


@pytest.mark.usefixtures("real_stash_runtime")
@pytest.mark.parametrize("commit_all", [False, True])
def test_update_holds_index_lock_against_later_git_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    commit_all: bool,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """更新所有中の後発commitを実Gitが拒否し、別worktreeは独立して動く。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    other = tmp_path / "other"
    _git(local, "worktree", "add", "-b", "other", str(other))
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)

    def later_commit(*_args: object, **_kwargs: object) -> int:
        result = subprocess.run(
            ["git", "-C", str(local), "commit", *(["-a"] if commit_all else []), "-m", "後発"],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode != 0
        assert "index.lock" in result.stderr
        (other / "other.txt").write_text("other\n", encoding="utf-8")
        _git(other, "add", "other.txt")
        _git(other, "commit", "-m", "別worktree")
        return 0

    monkeypatch.setattr(update_dotfiles, "_run_git_pull", later_commit)
    assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == 0
    assert _dirty_snapshot(local) == before
    assert (other / "other.txt").read_text(encoding="utf-8") == "other\n"
    assert not (local / ".git" / "index.lock").exists()
    assert "GIT_INDEX_FILE" not in update_dotfiles._child_env()


@pytest.mark.parametrize("outcome", ["success", "failure", "interrupt", "publish-failure", "stat-failure"])
def test_update_recovers_index_after_interrupted_git_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    outcome: str,
) -> None:
    """成功・失敗・中断のindexを公開し、自身の一時物とロックだけ回収する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    other = tmp_path / "other"
    _git(local, "worktree", "add", "-b", "other", str(other))
    other_index = pathlib.Path(_git(other, "rev-parse", "--git-path", "index"))
    other_lock = other_index.with_name("index.lock")
    other_lock.write_bytes(b"other owner")
    before_other = _dirty_snapshot(other)
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)

    def change(*_args: object, **_kwargs: object) -> int:
        assert update_dotfiles._run_git_change("add", "tracked.txt")
        if outcome == "interrupt":
            raise KeyboardInterrupt
        if outcome == "publish-failure":

            def fail_publish(*_args: object, **_kwargs: object) -> None:
                raise OSError("index公開失敗")

            monkeypatch.setattr(update_dotfiles.os, "replace", fail_publish)
        if outcome == "stat-failure":
            original_stat = pathlib.Path.stat
            failed = False

            def fail_stat(path: pathlib.Path, **kwargs: Any) -> os.stat_result:
                nonlocal failed
                if path == local / ".git" / "index.lock" and not failed:
                    failed = True
                    raise OSError("ロック確認失敗")
                return original_stat(path, **kwargs)

            monkeypatch.setattr(pathlib.Path, "stat", fail_stat)
        if outcome == "failure":
            assert not update_dotfiles._run_git_change("restore", "--", "missing-file")
            return 1
        return 0

    monkeypatch.setattr(update_dotfiles, "_update_owned_git", change)
    with update_dotfiles._configure_persistent_log("test"):
        if outcome == "interrupt":
            with pytest.raises(KeyboardInterrupt):
                _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5)
        else:
            assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == int(outcome != "success")
        assert ("tracked.txt" in _git(local, "diff", "--cached", "--name-only")) == (
            outcome not in {"publish-failure", "stat-failure"}
        )
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "unstaged\n"
        assert other_lock.read_bytes() == b"other owner"
        assert _dirty_snapshot(other) == before_other
        assert not (local / ".git" / "index.lock").exists()
        assert not list((local / ".git").glob("update-dotfiles-index-*"))
        if outcome == "failure":
            log = update_dotfiles._LOG_PATH.read_text(encoding="utf-8")
            assert "git restore -- missing-file: exit=1" in log
            assert "pathspec" in log


@pytest.mark.parametrize("race", ["started-before-lock", "foreign-lock", "copy-interrupt"])
def test_update_preserves_index_when_ownership_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    race: str,
) -> None:
    """ロック後の再確認と準備中断でも、既存indexと別主体のロックを保持する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles, "_update_owned_git", lambda *_args, **_kwargs: pytest.fail("変更開始"))
    lock = local / ".git" / "index.lock"
    if race == "started-before-lock":
        checks = iter([False, True])
        monkeypatch.setattr(update_dotfiles, "_git_operation_in_progress", lambda: next(checks))
    elif race == "foreign-lock":
        lock.write_bytes(b"foreign")
    else:

        def interrupt_copy(source: pathlib.Path, destination: pathlib.Path, **_kwargs: object) -> None:
            del source
            destination.write_bytes(b"partial index")
            raise KeyboardInterrupt

        monkeypatch.setattr(update_dotfiles.shutil, "copyfile", interrupt_copy)
    if race == "copy-interrupt":
        with pytest.raises(KeyboardInterrupt):
            _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5)
    else:
        assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=5) == 1
    assert _dirty_snapshot(local) == before
    assert not list((local / ".git").glob("update-dotfiles-index-*"))
    if race == "foreign-lock":
        assert lock.read_bytes() == b"foreign"
    else:
        assert not lock.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIXの実SIGTERMによるプロセス中断を検証する")
def test_update_recovers_index_after_sigterm(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """実プロセスへの停止通知後もGit作業を再開でき、完了済みstageを保持する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    ready = tmp_path / "ready"
    runner = tmp_path / "interrupt_update.py"
    runner.write_text(
        "import importlib.util, pathlib, sys, time\n"
        f"sys.path.insert(0, {str(_REPOSITORY_ATK_BIN.parents[1])!r})\n"
        f"spec = importlib.util.spec_from_file_location('update_dotfiles', {update_dotfiles.__file__!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"module._DOTFILES_ROOT = pathlib.Path({str(local)!r})\n"
        "def change(*args, **kwargs):\n"
        "    assert module._run_git_change('add', 'tracked.txt')\n"
        f"    pathlib.Path({str(ready)!r}).touch()\n"
        "    time.sleep(30)\n"
        "module._update_owned_git = change\n"
        "module._update_git_with_recovery(1, 4, timeout=5)\n",
        encoding="utf-8",
    )
    with subprocess.Popen(
        [sys.executable, str(runner)],
        env=host_environ(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as process:
        try:
            _wait_process_ready(process, ready)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert process.returncode != 0, (stdout, stderr)
    assert not (local / ".git" / "index.lock").exists()
    assert not list((local / ".git").glob("update-dotfiles-index-*"))
    assert "tracked.txt" in _git(local, "diff", "--cached", "--name-only")
    _git(local, "add", "untracked.txt")


@pytest.mark.parametrize(
    "state,command",
    [
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("sequencer", "cherry-pick"),
    ],
)
def test_update_pending_operation_explains_completion_and_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    state: str,
    command: str,
) -> None:
    """操作状態による見送りが作業を保持し、手動の完了・中止手順を保存する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    (local / ".git" / state).touch()
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(update_dotfiles, "_update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
    assert update_dotfiles.main() == 1
    assert _dirty_snapshot(local) == before
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    for text in ("git status", f"git {command}", "--continue", "--abort"):
        assert text in report["stderr_tail"]
        assert text in update_dotfiles._LOG_PATH.read_text(encoding="utf-8")


@pytest.mark.usefixtures("real_stash_runtime")
@pytest.mark.parametrize("pull_code", [0, 7])
def test_update_restore_overwrite_refusal_records_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    pull_code: int,
) -> None:
    """実Gitの上書き拒否から、保存ログと失敗同期結果で診断・退避先へ到達できる。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    (local / "tracked.txt").write_text("保存対象\n", encoding="utf-8")
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(update_dotfiles, "_update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
    monkeypatch.setattr(update_dotfiles, "_run_step", lambda *_args, **_kwargs: (0, ""))

    def overwrite(*_args: object, **_kwargs: object) -> int:
        (local / "tracked.txt").write_text("復元を拒否する変更\n", encoding="utf-8")
        return pull_code

    monkeypatch.setattr(update_dotfiles, "_run_git_pull", overwrite)
    assert update_dotfiles.main() == pull_code
    ref = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree").splitlines()[0]
    log = update_dotfiles._LOG_PATH.read_text(encoding="utf-8")
    assert f"git stash apply --index {ref}: exit=1" in log
    assert "would be overwritten" in log
    assert "復元に失敗" in log
    assert f"git stash apply --index {ref}" in log
    if pull_code:
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert "would be overwritten" in report["stderr_tail"]
        assert "復元に失敗" in report["stderr_tail"]
        assert ref in report["stderr_tail"]
    else:
        assert "競合から回復" in log
        assert "git switch update-dotfiles-recovery-" in log


def test_skipped_update_keeps_pending_for_next_timer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
) -> None:
    """自動更新スクリプトが見送りを未完了として保持し、次の周期で再試行する。"""
    import update_dotfiles_if_upstream_changed as timer  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    local, _seed, _remote = _create_git_pair(tmp_path)
    launcher = local / "bin" / "update-dotfiles"
    launcher.parent.mkdir()
    launcher.touch()
    monkeypatch.setattr(timer, "_DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(update_dotfiles, "_update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
    monkeypatch.setattr(update_dotfiles, "_git_operation_in_progress", lambda: True)
    attempts: list[int] = []
    head = _git(local, "rev-parse", "HEAD")
    git_outputs = {
        ("symbolic-ref", "HEAD"): "refs/heads/develop",
        ("rev-parse", "--symbolic-full-name", "@{upstream}"): "refs/remotes/origin/develop",
        ("--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all"): "",
        ("ls-remote", "--exit-code", "--refs", "origin", "refs/heads/develop"): f"{head}\trefs/heads/develop",
        ("rev-parse", "HEAD"): head,
    }

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command == [str(launcher)]:
            code = update_dotfiles.main()
            attempts.append(code)
            return subprocess.CompletedProcess(command, code, "", "")
        assert command[:3] == ["git", "-C", str(local)]
        return subprocess.CompletedProcess(command, 0, git_outputs[tuple(command[3:])], "")

    monkeypatch.setattr(timer, "_run", run)
    pending = tmp_path / "test.pending"
    for _ in range(2):
        assert timer._update_if_needed(pending) == 1
        assert pending.exists()
        assert not pending.with_suffix(".applied").exists()
    assert attempts == [1, 1]
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["exit_code"] == 1
    assert "タイマー" in report["stderr_tail"]
    assert "Git操作が進行中" in update_dotfiles._LOG_PATH.read_text(encoding="utf-8")


class TestCodexDaemonPause:
    """`chezmoi apply`前後の管理daemonの一時停止と再起動を、停止条件の境界ごとに検証する。"""

    @pytest.fixture(name="codex_env")
    def _codex_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> list[list[str]]:
        """Linuxで管理daemonと更新ループだけが稼働する状態を既定とする。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(update_dotfiles.sys, "platform", "linux")
        monkeypatch.delenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", raising=False)
        monkeypatch.setattr(update_dotfiles.shutil, "which", lambda name, path=None: _FAKE_CODEX if name == "codex" else None)
        monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: (_DAEMON, _DAEMON_HELPER, _UPDATE_LOOP))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        return calls

    @staticmethod
    def _commands(calls: list[list[str]]) -> list[str]:
        return [f"daemon {call[-1]}" if call[0] == _FAKE_CODEX else call[1] for call in calls]

    def test_daemon_only_is_stopped_before_apply_and_restarted(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """利用セッションが無ければ、apply前に停止しapply後に起動する。"""
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "daemon stop", "apply", "daemon start"]
        assert [_FAKE_CODEX, "app-server", "daemon", "stop"] in codex_env
        output = capsys.readouterr().out
        assert "Codex管理daemonを一時停止します" in output
        assert "Codex管理daemonを再起動しました。" in output
        log_text = update_dotfiles._LOG_PATH.read_text(encoding="utf-8")
        assert "Codex管理daemonを一時停止" in log_text
        assert "Codex管理daemonを再起動" in log_text

    def test_update_loop_only_runs_without_daemon_commands(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """管理daemonが停止済みで更新ループだけが残る場合は、daemonを起動せずに更新する。"""
        monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: (_UPDATE_LOOP,))
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]
        assert "Codex管理daemon" not in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("processes", "auto_restart"),
        [
            pytest.param((_DAEMON, _STDIO_DELEGATION), None, id="delegation"),
            pytest.param((_DAEMON, _codex(14, "codex", "session")), None, id="interactive-session"),
            pytest.param((_DAEMON,), "1", id="auto-restart"),
        ],
    )
    def test_daemon_is_kept_when_use_cannot_be_excluded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
        capsys: pytest.CaptureFixture[str],
        processes: tuple[Any, ...],
        auto_restart: str | None,
    ) -> None:
        """利用セッションか明示設定があれば、停止と事前案内を省いてapplyを続行する。"""
        monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: processes)
        if auto_restart is not None:
            monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", auto_restart)
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]
        captured = capsys.readouterr()
        assert "Codex管理daemon" not in captured.out
        assert "延期" not in captured.out
        assert not captured.err
        log_text = update_dotfiles._LOG_PATH.read_text(encoding="utf-8")
        assert "Codex管理daemonを停止せず" not in log_text
        assert "延期された場合" not in log_text

    def test_non_linux_does_not_inspect_codex(self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]]) -> None:
        """Linux以外では診断ログ復元が無く、plugin保護もeuryale限定のため、daemonを扱わない。"""
        monkeypatch.setattr(update_dotfiles.sys, "platform", "win32")
        monkeypatch.setattr(
            update_dotfiles.codex_processes,
            "codex_processes",
            lambda: (_ for _ in ()).throw(AssertionError("Linux以外でCodexを走査してはならない")),
        )
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]

    def test_apply_failure_still_restarts_daemon(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]], sync_report_path: pathlib.Path
    ) -> None:
        """applyが失敗しても停止したdaemonを起動し、applyの終了コードと失敗段を保つ。"""
        monkeypatch.setattr(subprocess, "run", _fake_run({"apply": 5}, codex_env))

        assert update_dotfiles.main() == 5
        assert self._commands(codex_env)[-2:] == ["apply", "daemon start"]
        assert "chezmoi apply" in json.loads(sync_report_path.read_text(encoding="utf-8"))["failed_stage"]

    def test_apply_exception_still_restarts_daemon(self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]]) -> None:
        """apply段が例外で中断しても、停止したdaemonの起動を試みる。"""
        fake_run = _fake_run({}, codex_env)

        def run(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if argv[:2] == ["chezmoi", "apply"]:
                raise KeyboardInterrupt
            return fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)

        with pytest.raises(KeyboardInterrupt):
            update_dotfiles.main()
        assert self._commands(codex_env)[-1] == "daemon start"

    def test_stop_failure_is_reported_and_restart_is_attempted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
        sync_report_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """停止に失敗してもapplyと起動を行い、停止の失敗を終了コードと同期結果へ反映する。"""
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({"daemon stop": 3}, codex_env, stderr_by_command={"daemon stop": "stop failed\n"}),
        )

        assert update_dotfiles.main() == 1
        assert self._commands(codex_env)[-3:] == ["daemon stop", "apply", "daemon start"]
        assert "Codex管理daemonを停止できませんでした（exit 3: stop failed）。" in capsys.readouterr().err
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["failed_stage"] == "Codex管理daemonの一時停止と再起動"
        assert "停止できませんでした" in report["stderr_tail"]

    def test_restart_failure_reports_manual_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
        sync_report_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """起動に失敗した場合は、手動の復帰操作を標準エラー、ログ、同期結果から確認できる。"""
        monkeypatch.setattr(subprocess, "run", _fake_run({"daemon start": 4}, codex_env))

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert "Codex管理daemonを再起動できませんでした（exit 4）" in err
        assert "codex app-server daemon start" in err
        assert "update-dotfiles logs" in err
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["failed_stage"] == "Codex管理daemonの一時停止と再起動"
        assert "codex app-server daemon start" in report["stderr_tail"]
        assert "codex app-server daemon start" in update_dotfiles._LOG_PATH.read_text(encoding="utf-8")
