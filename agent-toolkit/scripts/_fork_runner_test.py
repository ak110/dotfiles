"""_fork_runner.run_script のテスト。

subprocess.run同等の観測結果（終了コード・標準出力・標準エラー出力・環境変数伝播・作業ディレクトリ）に加え、
常駐fork-server方式固有の境界条件（大容量入出力・空入力・シグナル死・timeout・並行呼び出し）を検証する。
"""

import concurrent.futures
import os
import pathlib
import signal
import subprocess
import sys

import _fork_runner
import pytest


def _write_script(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "target.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_normal_exit_echoes_stdin(tmp_path: pathlib.Path) -> None:
    script = _write_script(tmp_path, "import sys\nsys.stdout.write(sys.stdin.read())\n")
    result = _fork_runner.run_script(script, input="hello")
    assert result.returncode == 0
    assert result.stdout == "hello"
    assert result.stderr == ""


def test_sys_exit_code_propagates(tmp_path: pathlib.Path) -> None:
    script = _write_script(tmp_path, "import sys\nsys.exit(2)\n")
    result = _fork_runner.run_script(script)
    assert result.returncode == 2


def test_uncaught_exception_returns_exit_1(tmp_path: pathlib.Path) -> None:
    script = _write_script(tmp_path, "raise RuntimeError('boom')\n")
    result = _fork_runner.run_script(script)
    assert result.returncode == 1
    assert "boom" in result.stderr


def test_env_propagates_to_child(tmp_path: pathlib.Path) -> None:
    script = _write_script(tmp_path, "import os, sys\nsys.stdout.write(os.environ.get('FORK_RUNNER_TEST_VAR', ''))\n")
    env = os.environ.copy()
    env["FORK_RUNNER_TEST_VAR"] = "value1"
    result = _fork_runner.run_script(script, env=env)
    assert result.stdout == "value1"


def test_cwd_changes_child_working_directory(tmp_path: pathlib.Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = _write_script(tmp_path, "import os, sys\nsys.stdout.write(os.getcwd())\n")
    result = _fork_runner.run_script(script, cwd=workdir)
    assert result.stdout == str(workdir)


def test_argv_propagates_to_child(tmp_path: pathlib.Path) -> None:
    script = _write_script(tmp_path, "import sys\nsys.stdout.write(','.join(sys.argv[1:]))\n")
    result = _fork_runner.run_script(script, argv=("a", "b"))
    assert result.stdout == "a,b"


def test_fallback_without_fork(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _write_script(tmp_path, "import sys\nsys.stdout.write(sys.stdin.read())\n")
    monkeypatch.setattr(_fork_runner, "_HAS_FORK", False)
    result = _fork_runner.run_script(script, input="fallback")
    assert result.returncode == 0
    assert result.stdout == "fallback"


def test_large_io_exceeding_pipe_buffer_roundtrips(tmp_path: pathlib.Path) -> None:
    # (a) パイプバッファ64KiBを超える大容量stdin/stdout/stderrの往復。一時ファイル経由のため上限の影響はない。
    payload = "x" * (128 * 1024)
    script = _write_script(
        tmp_path,
        "import sys\ndata = sys.stdin.read()\nsys.stdout.write(data)\nsys.stderr.write(data)\n",
    )
    result = _fork_runner.run_script(script, input=payload)
    assert result.returncode == 0
    assert result.stdout == payload
    assert result.stderr == payload


def test_empty_input_reaches_eof(tmp_path: pathlib.Path) -> None:
    # (b) 空入力のEOF。
    script = _write_script(tmp_path, "import sys\nsys.stdout.write(repr(sys.stdin.read()))\n")
    result = _fork_runner.run_script(script, input="")
    assert result.returncode == 0
    assert result.stdout == "''"


@pytest.mark.skipif(sys.platform == "win32", reason="signal.SIGKILLがWindowsのsignalモジュールに存在しないため")
def test_child_signal_death_returns_negative_returncode(tmp_path: pathlib.Path) -> None:
    # (c) 子プロセスのシグナル死で負のreturncodeが返る。
    script = _write_script(tmp_path, "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
    result = _fork_runner.run_script(script)
    assert result.returncode == -signal.SIGKILL


def test_timeout_raises_and_server_survives(tmp_path: pathlib.Path) -> None:
    # (d) timeout超過でsubprocess.TimeoutExpiredが送出され、後続リクエストが正常処理される。
    slow_script = _write_script(tmp_path, "import time\ntime.sleep(5)\n")
    with pytest.raises(subprocess.TimeoutExpired):
        _fork_runner.run_script(slow_script, timeout=0.1)
    fast_script = tmp_path / "fast.py"
    fast_script.write_text("import sys\nsys.stdout.write('alive')\n", encoding="utf-8")
    result = _fork_runner.run_script(fast_script)
    assert result.returncode == 0
    assert result.stdout == "alive"


def test_concurrent_threads_return_correct_results(tmp_path: pathlib.Path) -> None:
    # (e) 並行スレッド2本からの同時呼び出しが_LOCK直列化により正しい結果をそれぞれ返す。
    script_a = tmp_path / "a.py"
    script_a.write_text("import sys\nsys.stdout.write(sys.stdin.read())\n", encoding="utf-8")
    script_b = tmp_path / "b.py"
    script_b.write_text("import sys\nsys.stdout.write(sys.stdin.read())\n", encoding="utf-8")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(_fork_runner.run_script, script_a, input="alpha")
        future_b = executor.submit(_fork_runner.run_script, script_b, input="beta")
        result_a = future_a.result()
        result_b = future_b.result()
    assert result_a.stdout == "alpha"
    assert result_b.stdout == "beta"


def test_unspecified_env_reflects_current_environ(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # env未指定時は呼び出し時点のos.environが子へ渡る（サーバー起動時の環境を継承しない）。
    script = _write_script(tmp_path, "import os, sys\nsys.stdout.write(os.environ.get('FORK_RUNNER_TEST_VAR', ''))\n")
    _fork_runner.run_script(script)  # サーバーを先に起動させる
    monkeypatch.setenv("FORK_RUNNER_TEST_VAR", "after-start")
    result = _fork_runner.run_script(script)
    assert result.stdout == "after-start"


def test_unspecified_cwd_reflects_current_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # cwd未指定時は呼び出し時点の作業ディレクトリが子へ渡る。
    script = _write_script(tmp_path, "import os, sys\nsys.stdout.write(os.getcwd())\n")
    workdir = tmp_path / "moved"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    result = _fork_runner.run_script(script)
    assert result.stdout == str(workdir)


def test_server_death_recovers_on_next_call(tmp_path: pathlib.Path) -> None:
    # サーバー強制終了後の呼び出しが再起動またはフォールバックで正常結果を返す。
    script = _write_script(tmp_path, "import sys\nsys.stdout.write(sys.stdin.read())\n")
    _fork_runner.run_script(script, input="first")  # サーバーを起動させる
    server = _fork_runner._SERVER  # noqa: SLF001 -- 障害系再現のため内部状態へアクセスする  # pylint: disable=protected-access
    assert server is not None
    server.kill()
    server.wait()
    result = _fork_runner.run_script(script, input="recovered")
    assert result.returncode == 0
    assert result.stdout == "recovered"
