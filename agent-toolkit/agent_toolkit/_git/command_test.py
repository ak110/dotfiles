"""Gitコマンド共通ラッパーの出力契約を検証する。"""

import os
import pathlib
import subprocess
import sys
import time

import psutil
import pytest

from agent_toolkit._git import command as subject

pytestmark = pytest.mark.windows


def test_run_quiet_suppresses_success_output(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """成功したGitの出力を標準出力にも標準エラーにも書かない。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_quiet_reports_failure_and_preserves_exception(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """失敗したGitの診断と終了コードを保持する。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)
    capsys.readouterr()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        subject.run_quiet(["rev-parse", "--verify", "missing"], tmp_path)

    captured = capsys.readouterr()
    error = exc_info.value
    assert captured.out == ""
    assert captured.err == error.stderr
    assert error.returncode == 128
    assert error.cmd == ["git", "rev-parse", "--verify", "missing"]
    assert error.output == ""
    assert error.stderr


def test_run_quiet_redirects_failure_stdout_to_stderr(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """失敗したGitの標準出力も呼び出し側の標準エラーへ書く。"""
    (tmp_path / "first.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("second\n", encoding="utf-8")

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        subject.run_quiet(["diff", "--no-index", "--", "first.txt", "second.txt"], tmp_path)

    captured = capsys.readouterr()
    error = exc_info.value
    assert captured.out == ""
    assert captured.err == error.output
    assert error.returncode == 1
    assert error.output
    assert error.stderr == ""


def test_run_quiet_can_hold_failure_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """出力転送を無効にした失敗は診断を例外だけへ保持する。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)
    capsys.readouterr()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        subject.run_quiet(
            ["rev-parse", "--verify", "missing"],
            tmp_path,
            forward_error_output=False,
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert exc_info.value.output == ""
    assert exc_info.value.stderr


def test_run_passes_input_and_environment(tmp_path: pathlib.Path) -> None:
    """標準入力と環境変数を`git`へ渡し、テキストの結果を返すこと。"""
    subject.run_quiet(["init", "--initial-branch=main"], tmp_path)
    blob = subject.run(["hash-object", "-w", "--stdin"], tmp_path, capture_output=True, text=True, input="本文\n", check=True)
    shown = subject.run(["cat-file", "-p", blob.stdout.strip()], tmp_path, capture_output=True, check=True)
    configured = subject.run(
        ["config", "--get", "user.name"],
        tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "環境の名前"},
    )

    assert shown.stdout == "本文\n".encode()
    assert configured.stdout == "環境の名前\n"


def test_optional_helpers_return_none_on_failure(tmp_path: pathlib.Path) -> None:
    """非0終了では`None`を返し、`optional_stdout`は起動の失敗でも`None`を返すこと。"""
    assert subject.optional_stdout(["rev-parse", "--verify", "missing"], tmp_path, timeout=30) is None
    assert subject.optional_output(["rev-parse", "--verify", "missing"], tmp_path, timeout=30) is None
    assert subject.optional_stdout(["status"], tmp_path / "missing-directory", timeout=30) is None
    with pytest.raises(OSError):
        subject.optional_output(["status"], tmp_path / "missing-directory", timeout=30)


def test_command_line_prefixes_git() -> None:
    """例外と記録へ載せるコマンドは`git`で始まる引数列であること。"""
    assert subject.command_line(["status", "--porcelain"]) == ["git", "status", "--porcelain"]


def test_run_passes_text_input_without_newline_conversion(tmp_path: pathlib.Path) -> None:
    """`text=True`の文字列の入力は、改行を変えずにUTF-8のバイト列として子プロセスへ届く。"""
    received = tmp_path / "received.bin"
    script = tmp_path / "read_stdin.py"
    script.write_text(
        "import pathlib, sys\npathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())\n"
        "sys.stdout.buffer.write('出力\\r\\n次行\\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('診断\\r\\n'.encode('utf-8'))\n",
        encoding="utf-8",
    )
    shell_command = f'"{pathlib.Path(sys.executable).as_posix()}" "{script.as_posix()}" "{received.as_posix()}"'

    result = subject.run(
        ["-c", f"alias.dump=!{shell_command}", "dump"], tmp_path, text=True, capture_output=True, input="a\nb\n日本語\n"
    )

    assert result.returncode == 0
    assert received.read_bytes() == "a\nb\n日本語\n".encode()
    assert result.stdout == "出力\n次行\n"
    assert result.stderr == "診断\n"


def test_scoped_deadline_preserves_defaults_explicit_limits_and_restores_context(monkeypatch):
    clock = [100.0]
    limits = []

    def execute(command, **kwargs):
        limits.append(kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subject.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(subject.subprocess, "run", execute)
    monkeypatch.setattr(subject.process_tree, "run_with_timeout", execute)
    subject.run(["status"])
    subject.run(["status"], timeout=7)
    with subject.timeout_scope(10):
        subject.run(["status"])
        subject.run(["status"], timeout=2)
        clock[0] = 106
        with subject.timeout_scope(20):
            subject.run(["status"], timeout=30)
        clock[0] = 111
        with pytest.raises(subprocess.TimeoutExpired):
            subject.run(["status"])
    subject.run(["status"])
    with pytest.raises(RuntimeError), subject.timeout_scope(10):
        raise RuntimeError("文脈を中断した")
    subject.run(["status"], timeout=7)
    assert limits == [subject.LOCAL_QUERY_TIMEOUT_SECONDS, 7, 10, 2, 4, subject.LOCAL_QUERY_TIMEOUT_SECONDS, 7]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["fetch"], subject.TRANSFER_TIMEOUT_SECONDS),
        (["pull", "--ff-only"], subject.TRANSFER_TIMEOUT_SECONDS),
        (["-C", "repo", "push"], subject.TRANSFER_TIMEOUT_SECONDS),
        (["clone", "url"], subject.TRANSFER_TIMEOUT_SECONDS),
        (["submodule", "update"], subject.TRANSFER_TIMEOUT_SECONDS),
        (["remote", "update"], subject.TRANSFER_TIMEOUT_SECONDS),
        (["remote", "-v", "update"], subject.TRANSFER_TIMEOUT_SECONDS),
        (["ls-remote", "--heads", "origin"], subject.REMOTE_QUERY_TIMEOUT_SECONDS),
        (["-c", "credential.helper=", "ls-remote", "origin"], subject.REMOTE_QUERY_TIMEOUT_SECONDS),
        (["remote", "get-url", "origin"], subject.REMOTE_QUERY_TIMEOUT_SECONDS),
        (["rev-parse", "HEAD"], subject.LOCAL_QUERY_TIMEOUT_SECONDS),
        (["status", "--porcelain"], subject.LOCAL_QUERY_TIMEOUT_SECONDS),
        (["--git-dir", "x", "--work-tree", "y", "config", "user.name"], subject.LOCAL_QUERY_TIMEOUT_SECONDS),
        (["--git-dir=x", "log", "-1"], subject.LOCAL_QUERY_TIMEOUT_SECONDS),
        (["--no-pager", "diff"], subject.LOCAL_QUERY_TIMEOUT_SECONDS),
        (["cat-file", "-e", "HEAD"], subject.LOCAL_QUERY_TIMEOUT_SECONDS),
        (["-c", "submodule.recurse=false", "rebase", "@{u}"], subject.MUTATION_TIMEOUT_SECONDS),
        (["worktree", "add", "path"], subject.MUTATION_TIMEOUT_SECONDS),
        (["commit", "-m", "fetch"], subject.MUTATION_TIMEOUT_SECONDS),
        (["unknown-subcommand"], subject.MUTATION_TIMEOUT_SECONDS),
        ([], subject.MUTATION_TIMEOUT_SECONDS),
    ],
)
def test_run_applies_default_timeout_by_subcommand_kind(
    monkeypatch: pytest.MonkeyPatch, args: list[str], expected: float
) -> None:
    """時間上限を指定しない呼び出しは、大域オプションを除いたサブコマンドの区分のデフォルト値を受ける。"""
    limits: list[float] = []

    def record(command: list[str], *, timeout: float, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        limits.append(timeout)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subject, "_launch", record)

    subject.run(args)
    subject.run(args, timeout=7)
    with subject.timeout_scope(5):
        subject.run(args)

    assert limits[:2] == [expected, 7]
    assert 0 < limits[2] <= 5


_UNRESPONSIVE_REMOTE_SCRIPT = """
import pathlib
import subprocess
import sys
import time

# 接続後に応答しないリモートを模す。gitの孫に当たる子も起動し、子孫まで止まることを観測できるようにする。
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
pathlib.Path(sys.argv[1]).write_text(f"{child.pid}", encoding="utf-8")
time.sleep(30)
sys.exit(255)
"""


def test_fetch_from_unresponsive_remote_stops_at_default_timeout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """応答しないリモートへのfetchはデフォルトの時間上限で打ち切られ、gitが起動した子孫のプロセスも残らない。

    デフォルトの上限が無い場合、fetchはリモートの代役が終わる30秒後まで戻らず、時間切れも送出しない。
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    subject.run_quiet(["init", "--initial-branch=main"], repository)
    subject.run_quiet(["remote", "add", "origin", "ssh://unresponsive.invalid/repo.git"], repository)
    script = tmp_path / "unresponsive_remote.py"
    script.write_text(_UNRESPONSIVE_REMOTE_SCRIPT, encoding="utf-8")
    pid_file = tmp_path / "grandchild.pid"
    remote_command = f'"{pathlib.Path(sys.executable).as_posix()}" "{script.as_posix()}" "{pid_file.as_posix()}"'
    monkeypatch.setattr(subject, "TRANSFER_TIMEOUT_SECONDS", 3.0)

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        subject.run(
            ["fetch", "origin"],
            repository,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_SSH_COMMAND": remote_command},
        )

    assert time.monotonic() - started < 20
    assert raised.value.cmd == ["git", "fetch", "origin"]
    assert raised.value.timeout == pytest.approx(3.0)
    assert subject.timeout_cwd(raised.value) == str(repository)
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while psutil.pid_exists(grandchild) and psutil.Process(grandchild).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline, f"gitの子孫のプロセスが残っている: {grandchild}"
        time.sleep(0.05)
