"""update_dotfiles試験の共通fixture・入力と補助関数。"""

import io
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, Self, cast

import platformdirs
import psutil
import pytest
from agent_toolkit._git import stale_lock
from agent_toolkit._testing.isolation import restore_host_environment

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position

_REAL_UPDATE_GIT_WITH_RECOVERY = update_dotfiles_git.update_git_with_recovery  # pylint: disable=protected-access

_REAL_PREPARE_AGENT_TOOLKIT_ENVIRONMENT = update_dotfiles._prepare_agent_toolkit_environment  # pylint: disable=protected-access

_REPOSITORY_ATK_BIN = pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit" / "scripts"


@pytest.fixture(autouse=True)
def _separate_git_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """段階順の単体テストでは、Git回復の統合挙動を低水準pullから分離する。"""
    monkeypatch.setattr(update_dotfiles_runtime, "LOG_PATH", tmp_path / "state" / "update-dotfiles.log")
    monkeypatch.setattr(
        update_dotfiles_git,
        "update_git_with_recovery",
        lambda step_no, total, timeout: update_dotfiles_process.run_git_pull(  # pylint: disable=protected-access
            step_no, total, timeout=timeout
        ),
    )


@pytest.fixture(autouse=True)
def _skip_agent_toolkit_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    """段階順の単体テストでは、共有依存環境の準備をchezmoiの段の呼び出し列から分離する。

    準備の位置と失敗時の続行は専用の試験が実際の関数へ戻して確かめる。
    """
    monkeypatch.setattr(update_dotfiles, "_prepare_agent_toolkit_environment", lambda _total: None)


_REAL_PROCESS_ITER = psutil.process_iter


def _own_process_iter(*args: Any, **kwargs: Any) -> Any:
    """試験プロセスの子孫だけを走査結果に残す。"""
    try:
        own = {child.pid for child in psutil.Process().children(recursive=True)}
    except psutil.Error:
        own = set()
    return (process for process in _REAL_PROCESS_ITER(*args, **kwargs) if process.pid in own)


@pytest.fixture(autouse=True)
def _own_git_process_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """進行中Git操作の走査を、試験自身が起動したプロセスへ限定する。

    OSのプロセス表は試験間・実行環境と共有され、無関係なgitプロセスや照会を拒否するプロセスで
    実ガードが更新を見送ると、回復処理を検証する試験の合否が環境で変わる。
    照合と走査例外の扱い自体は製品の判定を通し、専用試験が走査結果を個別に差し替えて確かめる。
    """
    monkeypatch.setattr(stale_lock.psutil, "process_iter", _own_process_iter)


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
    """Git pullの終了コードと出力を返すfake `subprocess.Popen`。"""
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


_INTERLEAVED_CHILD_CODE = """
import sys
for index in (1, 2):
    sys.stdout.write(f"out{index}\\n")
    sys.stdout.flush()
    sys.stderr.write(f"err{index}\\n")
    sys.stderr.flush()
sys.exit(int(sys.argv[1]))
"""


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
        "import os,pathlib,signal,time; signal.signal(signal.SIGHUP, signal.SIG_IGN); "
        "release=pathlib.Path(os.environ['UPDATE_DOTFILES_TEST_PID_PATH']+'.release'); "
        "release.with_suffix('.ready').touch(); deadline=time.monotonic()+120; "
        "exec('while not release.exists():\\n assert time.monotonic()<deadline\\n time.sleep(0.01)')",
    ])
    pid_path = pathlib.Path(os.environ["UPDATE_DOTFILES_TEST_PID_PATH"])
    deadline = time.monotonic() + 30
    while not pid_path.with_suffix('.pid.ready').exists():
        assert child.poll() is None and time.monotonic() < deadline
        time.sleep(0.01)
    pending = pid_path.with_suffix(".tmp")
    pending.write_text(str(child.pid), encoding="utf-8")
    pending.replace(pid_path)
    child.wait(timeout=120)
"""


def _run_git_pull_in_pty(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
    timeout: float | None,
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
            update_dotfiles_process.kill_process_tree(process)  # pylint: disable=protected-access
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"端末ランナーが30秒以内に終了しなかった: {stdout}\n{stderr}")
    assert process.returncode == 0, stderr
    assert not stderr, stderr
    result = json.loads(stdout)
    assert "DeprecationWarning" not in result["output"], result["output"]
    assert not result["descendant_running"], "製品のgit pullが終了した時点で孫プロセスが残っている"
    return result["returncode"], result["output"], descendant_pid_path, result["elapsed"]


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
            env=update_dotfiles_runtime.git_env(),
        ).returncode

    monkeypatch.setattr(update_dotfiles_process, "run_git_pull", _pull)


@pytest.fixture(name="real_stash_runtime")
def _real_stash_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, share_package_caches: None) -> None:
    """実CLIのランタイム解決はホストへ戻し、CLI自身の保存先は一時領域へ限定する。"""
    del share_package_caches
    restore_host_environment(monkeypatch)
    monkeypatch.setenv("AGENT_TOOLKIT_RUNTIME_DATA", str(tmp_path / "runtime"))


class _UnreadableGitProcess:
    """環境変数とcwdの照会を拒否するgitプロセスの走査結果。"""

    info = {"name": "git", "cmdline": ["git", "status"]}

    def __init__(self, *, pid: int) -> None:
        self.pid = pid

    def environ(self) -> dict[str, str]:
        raise psutil.AccessDenied(self.pid)

    def cwd(self) -> str:
        raise psutil.AccessDenied(self.pid)


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
