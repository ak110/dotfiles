"""`scripts/update_dotfiles.py`のテスト。

通常4段の表示順序・fail-fast・排他ロック・標準ストリーム・Codex管理daemonの一時停止を検証する。
"""

# pylint: disable=protected-access

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, cast

import platformdirs
import psutil
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position

_REAL_UPDATE_GIT_WITH_RECOVERY = update_dotfiles._update_git_with_recovery  # pylint: disable=protected-access
_REPOSITORY_ATK_BIN = pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit" / "bin"


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


def test_child_env_preserves_existing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の環境変数が保持される。"""
    monkeypatch.setenv("UPDATE_DOTFILES_TEST_SENTINEL", "preserved")

    environment = update_dotfiles._child_env()  # pylint: disable=protected-access

    assert environment["UPDATE_DOTFILES_TEST_SENTINEL"] == "preserved"
    assert environment["MISE_AUTO_INSTALL"] == "0"
    assert str(pathlib.Path.home() / ".local" / "bin") in environment["PATH"].split(os.pathsep)


def test_save_worktree_uses_repository_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """worktree退避はPATH上の裸のatkではなく作業コピーのランチャーを使う。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({}, calls, stdout_by_command={str(_REPOSITORY_ATK_BIN / "atk"): "refs/worktree/test\n"}),
    )

    assert update_dotfiles._save_worktree("test") == "refs/worktree/test"  # pylint: disable=protected-access
    assert pathlib.Path(calls[0][0]) == _REPOSITORY_ATK_BIN / "atk"


def test_save_worktree_reports_launcher_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """ランチャー起動不能はtracebackを抑止して文脈付き失敗にする。"""
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))

    assert update_dotfiles._save_worktree("test") is None  # pylint: disable=protected-access
    captured = capsys.readouterr()
    assert "未コミット内容の退避を開始できませんでした" in captured.err
    assert "Traceback" not in captured.err


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
    state_dir = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))
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


def test_missing_persistent_log_stops_before_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_dotfiles, "_configure_persistent_log", lambda _run_id: None)
    monkeypatch.setattr(
        update_dotfiles,
        "_update_git_with_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("更新を開始してはならない")),
    )

    assert update_dotfiles.main() == 1


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

        assert update_dotfiles.main() == 7
        assert [call[1] for call in calls] == ["git", "init", "status", "diff"]


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
        """失敗した段の名前と標準エラーの末尾が、実行の記録から読み取れること。"""
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
        assert "対象ツールの導入に失敗しました" in report["stderr_tail"]

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
            f"fatal: pull failed\n永続ログ: {update_dotfiles._LOG_PATH}\n"  # noqa: SLF001
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

    上限ありのテストは上限の到達ではなく端末入力の受け渡しを検査するため、
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
        ).returncode

    monkeypatch.setattr(update_dotfiles, "_run_git_pull", _pull)


class TestGitConflictRecovery:
    """一時Git上流でcommit競合と未コミット復元競合からの回復を検証する。"""

    def test_rebase_conflict_preserves_original_commit_and_tracks_upstream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        local, seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("local commit\n", encoding="utf-8")
        _git(local, "add", "tracked.txt")
        _git(local, "commit", "-m", "local")
        original_head = _git(local, "rev-parse", "HEAD")
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

    def test_dirty_restore_conflict_keeps_recoverable_ref_and_clean_upstream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        local, seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (local / "staged.txt").write_text("staged\n", encoding="utf-8")
        (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
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
        saved_paths = _git(local, "stash", "show", "--include-untracked", "--name-only", stash_ref).splitlines()
        assert result == 0
        assert _git(local, "status", "--porcelain=v1") == ""
        assert _git(local, "rev-parse", "HEAD") == _git(local, "rev-parse", "@{upstream}")
        assert {"tracked.txt", "staged.txt", "untracked.txt"} <= set(saved_paths)


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

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        worktree_refs = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree").splitlines()
        stash_ref = next(ref for ref in worktree_refs if ref.startswith("refs/worktree/update-dotfiles-"))
        saved_paths = set(_git(local, "stash", "show", "--include-untracked", "--name-only", stash_ref).splitlines())
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
    scripts_dir = pathlib.Path(__file__).resolve().parent
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(scripts_dir)!r})\n"
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
        ("processes", "auto_restart", "reason"),
        [
            pytest.param((_DAEMON, _STDIO_DELEGATION), None, "利用セッションが稼働中", id="delegation"),
            pytest.param((_DAEMON, _codex(14, "codex", "session")), None, "codex (1件)", id="interactive-session"),
            pytest.param((_DAEMON,), "1", "DOTFILES_CODEX_DAEMON_AUTO_RESTART=1", id="auto-restart"),
        ],
    )
    def test_daemon_is_kept_when_use_cannot_be_excluded(  # noqa: PLR0913  # pylint: disable=too-many-arguments
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
        capsys: pytest.CaptureFixture[str],
        processes: tuple[Any, ...],
        auto_restart: str | None,
        reason: str,
    ) -> None:
        """利用セッションか明示設定があれば停止せず、理由を表示する。"""
        monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: processes)
        if auto_restart is not None:
            monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", auto_restart)
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]
        output = capsys.readouterr().out
        assert "Codex管理daemonを停止せずに更新します" in output
        assert reason in output

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
