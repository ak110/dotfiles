"""独立更新jobを隔離した公開ランチャーと小さな更新projectから検証する。"""

import contextlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Generator
from unittest.mock import Mock

import psutil
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import update_dotfiles_job  # noqa: E402  # pylint: disable=wrong-import-position

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_POSIX = pytest.mark.skipif(sys.platform == "win32", reason="fake uvとUnixシグナルはPOSIXで検証する")


@pytest.fixture(name="project")
def _project(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> tuple[pathlib.Path, dict[str, str]]:
    """実HOME・状態領域・実更新から独立したprojectとfake uvを用意する。"""
    root = tmp_path / "project with spaces"
    (root / "libexec").mkdir(parents=True)
    (root / "bin").mkdir()
    shutil.copy2(_ROOT / "libexec/update_dotfiles_job.py", root / "libexec/update_dotfiles_job.py")
    common = root / "agent-toolkit/agent_toolkit/_common"
    common.mkdir(parents=True)
    shutil.copy2(_ROOT / "agent-toolkit/agent_toolkit/_common/windows_job.py", common / "windows_job.py")
    shutil.copy2(_ROOT / "bin/update-dotfiles", root / "bin/update-dotfiles")
    (root / "libexec/update_uv.sh").write_text('"$1" self update\n', encoding="utf-8")
    (root / "libexec/update_dotfiles.py").write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "if set(sys.argv[1:]) & {'logs', '-h', '--help'}:\n"
        "    print('saved log or help')\n"
        "    raise SystemExit(0)\n"
        "print('更新出力', flush=True)\n"
        "print('更新診断', file=sys.stderr, flush=True)\n"
        "assert os.environ['DOTFILES_PRUNE_OUTSIDE_UV'] == '1'\n"
        "if os.environ.get('SPAWN_CHILD') == '1':\n"
        "    child = subprocess.Popen([sys.executable, '-c', \"import os,pathlib,time; "
        "pathlib.Path(os.environ['CHILD_READY']).write_text(str(os.getpid())); time.sleep(60)\"])\n"
        "    while not pathlib.Path(os.environ['CHILD_READY']).exists():\n"
        "        time.sleep(0.01)\n"
        "pathlib.Path(os.environ['READY']).write_text(str(os.getpid()), encoding='utf-8')\n"
        "while os.environ.get('HOLD') == '1' and not pathlib.Path(os.environ['RELEASE']).exists():\n"
        "    time.sleep(0.02)\n"
        "raise SystemExit(int(os.environ.get('RUN_EXIT', '0')))\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    native = home / ".local/bin"
    native.mkdir(parents=True)
    environment = host_environ()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_THREAD_ID": "own-thread",
            "CALLS": str(tmp_path / "calls"),
            "READY": str(tmp_path / "ready"),
            "RELEASE": str(tmp_path / "release"),
            "CHILD_READY": str(tmp_path / "child-ready"),
            "NATIVE_PYTHON": sys.executable,
            "PYTHONIOENCODING": "utf-8:replace",
        }
    )
    for name in (
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
    ):
        environment[name] = str(tmp_path / name)
    environment["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = environment["LOCALAPPDATA"]
    environment.pop("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", None)
    (native / "uv").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'if [[ "$1 $2" == "python find" ]]; then\n'
        '  if [[ "${INSTALL_REQUIRED:-0}" == 1 && ! -f "$HOME/installed" ]]; then exit 2; fi\n'
        '  printf "%s\\n" "$NATIVE_PYTHON"; exit 0\nfi\n'
        'if [[ "$1 $2" == "python install" ]]; then touch "$HOME/installed"; exit "${PYTHON_EXIT:-0}"; fi\n'
        'if [[ "$1 $2" == "self update" ]]; then exit "${SELF_EXIT:-0}"; fi\n'
        'if [[ "$1" == run ]]; then shift 3; exec "$NATIVE_PYTHON" "$@"; fi\n'
        "exit 33\n",
        encoding="utf-8",
    )
    (native / "uv").chmod(0o755)
    (native / "dotfiles-post-apply").write_text(
        '#!/usr/bin/env bash\nprintf "prune %s\\n" "$*" >> "$CALLS"\nexit "${PRUNE_EXIT:-0}"\n',
        encoding="utf-8",
    )
    (native / "dotfiles-post-apply").chmod(0o755)
    return root, environment


def _invoke(project: tuple[pathlib.Path, dict[str, str]], *arguments: str) -> subprocess.CompletedProcess[str]:
    root, environment = project
    return subprocess.run(
        [str(root / "bin/update-dotfiles"), *arguments],
        env=environment,
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def _wait_file(path: pathlib.Path) -> None:
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"同期点に到達しません: {path}")
        time.sleep(0.02)


def _job(project: tuple[pathlib.Path, dict[str, str]]) -> pathlib.Path:
    _root, environment = project
    paths = list((pathlib.Path(environment["XDG_STATE_HOME"]) / "agent-toolkit/update-jobs").glob("*/input.json"))
    assert len(paths) == 1
    return paths[0].parent


@pytest.mark.parametrize("run_exit,prune_exit,self_exit", [(0, 0, 0), (23, 0, 0), (0, 9, 7)])
@_POSIX
def test_public_codex_job_orders_pipeline_and_retrieves_same_result(
    project: tuple[pathlib.Path, dict[str, str]],
    run_exit: int,
    prune_exit: int,
    self_exit: int,
) -> None:
    """自己更新・本体・成功後pruneを実行し、明示IDと自己threadで同じ終了値を再取得する。"""
    _root, environment = project
    environment.update(
        {"RUN_EXIT": str(run_exit), "PRUNE_EXIT": str(prune_exit), "SELF_EXIT": str(self_exit), "INSTALL_REQUIRED": "1"}
    )
    result = _invoke(project, "--prune")
    assert result.returncode == (run_exit or prune_exit), result.stdout + result.stderr
    job = _job(project)
    assert "更新出力" in result.stdout and "更新診断" in result.stderr
    metadata = json.loads((job / "input.json").read_text(encoding="utf-8"))
    assert metadata["arguments"] == ["--prune"] and metadata["thread_id"] == "own-thread"
    calls = pathlib.Path(environment["CALLS"]).read_text(encoding="utf-8").splitlines()
    assert calls[:4] == ["python find 3.15", "python install 3.15", "python find 3.15", "self update"]
    assert calls[4] == "python install --upgrade 3.15"
    assert calls[5].startswith("run --no-project --script ")
    assert calls[6:] == ([] if run_exit else ["prune --prune-only-if-minimal --prune"])
    explicit = _invoke(project, "job", job.name)
    own = _invoke(project, "job")
    assert explicit.returncode == own.returncode == result.returncode
    assert "更新出力" in own.stdout and "更新診断" in explicit.stderr
    assert pathlib.Path(environment["CALLS"]).read_text(encoding="utf-8").splitlines().count("self update") == 1


@pytest.mark.parametrize("arguments", [["logs"], ["--help"], ["--prune", "-h"]])
@_POSIX
def test_codex_logs_and_help_do_not_start_jobs(
    project: tuple[pathlib.Path, dict[str, str]],
    arguments: list[str],
) -> None:
    """Codexからの表示専用操作は更新jobとuv自己更新を実行しない。"""
    _root, environment = project
    result = _invoke(project, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "saved log or help" in result.stdout
    assert not pathlib.Path(environment["READY"]).exists()
    calls = pathlib.Path(environment["CALLS"]).read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and calls[0].startswith("run --no-project --script ")
    assert not (pathlib.Path(environment["XDG_STATE_HOME"]) / "agent-toolkit/update-jobs").exists()


@_POSIX
def test_process_loop_job_skips_uv_self_update(project: tuple[pathlib.Path, dict[str, str]]) -> None:
    """process-loopの更新jobは自己更新を省いて本体の終了状態を保存する。"""
    _root, environment = project
    environment["AGENT_TOOLKIT_PROCESS_LOOP_SESSION"] = "1"
    result = _invoke(project)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = pathlib.Path(environment["CALLS"]).read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3
    assert calls[0] == "python find 3.15" and calls[1].startswith("run --no-project --script ")
    assert calls[2] == "prune --prune-only-if-minimal"


@_POSIX
def test_python_upgrade_failure_stops_before_update(project: tuple[pathlib.Path, dict[str, str]]) -> None:
    """管理Pythonの更新失敗を伝え、更新本体と整理を開始しない。"""
    _root, environment = project
    environment["PYTHON_EXIT"] = "17"
    result = _invoke(project)
    assert result.returncode == 17, result.stdout + result.stderr
    calls = pathlib.Path(environment["CALLS"]).read_text(encoding="utf-8").splitlines()
    assert calls == ["python find 3.15", "self update", "python install --upgrade 3.15"]
    assert not pathlib.Path(environment["READY"]).exists()


@pytest.mark.parametrize("arguments", [[], ["--mode", "minimal"], ["--mode", "full", "--prune"]])
@_POSIX
def test_success_passes_mode_and_prune_to_outside_cleanup(
    project: tuple[pathlib.Path, dict[str, str]], arguments: list[str]
) -> None:
    """通常成功も整理の入口へ渡し、実効modeの判定に必要な元引数を保持する。"""
    result = _invoke(project, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = pathlib.Path(project[1]["CALLS"]).read_text(encoding="utf-8").splitlines()
    assert calls[-1] == " ".join(["prune", "--prune-only-if-minimal", *arguments])


@pytest.fixture(name="output_child")
def _output_child(project: tuple[pathlib.Path, dict[str, str]]) -> pathlib.Path:
    """親の終了後もpipeを持つ子を、外部から解放できる入力にする。"""
    root, environment = project
    child = root / "libexec/child.py"
    child.write_text(
        "import os, pathlib, sys, time\n"
        "ready = pathlib.Path(os.environ['CHILD_READY'])\n"
        "temporary = ready.with_suffix('.tmp')\n"
        "temporary.write_text(str(os.getpid()), encoding='utf-8')\n"
        "temporary.replace(ready)\n"
        "while not pathlib.Path(os.environ['RELEASE']).exists():\n"
        "    time.sleep(0.01)\n"
        "sys.stdout.write('後続出力' * 20000); sys.stdout.flush()\n"
        "sys.stderr.write('後続診断' * 20000); sys.stderr.flush()\n",
        encoding="utf-8",
    )
    (root / "libexec/update_dotfiles.py").write_text(
        "import os, pathlib, subprocess, sys\n"
        f"child_process = subprocess.Popen([sys.executable, {str(child)!r}], "
        "start_new_session=os.environ.get('CHILD_SESSION') == '1')\n"
        "ready = pathlib.Path(os.environ['READY'])\n"
        "temporary = ready.with_suffix('.tmp')\n"
        "temporary.write_text(str(os.getpid()), encoding='utf-8')\n"
        "temporary.replace(ready)\n"
        "raise SystemExit(int(os.environ.get('RUN_EXIT', '0')))\n",
        encoding="utf-8",
    )
    return pathlib.Path(environment["CHILD_READY"])


@contextlib.contextmanager
def _pending_output_job(
    project: tuple[pathlib.Path, dict[str, str]], output_child: pathlib.Path
) -> Generator[tuple[subprocess.Popen[str], pathlib.Path]]:
    """親終了と子の準備を確認し、終了時には出力の解放と結果保存を待つ。"""
    root, environment = project
    with subprocess.Popen(
        [str(root / "bin/update-dotfiles")],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    ) as controller:
        try:
            _wait_file(output_child)
            _wait_file(pathlib.Path(environment["READY"]))
            with contextlib.suppress(psutil.NoSuchProcess):
                psutil.Process(int(pathlib.Path(environment["READY"]).read_text(encoding="utf-8"))).wait(timeout=15)
            yield controller, _job(project)
        finally:
            pathlib.Path(environment["RELEASE"]).touch()
            controller.communicate(timeout=20)
            _wait_file(_job(project) / "result.json")


@pytest.mark.parametrize("run_exit", [0, 23])
@_POSIX
def test_public_job_collects_output_after_parent_exit(
    project: tuple[pathlib.Path, dict[str, str]], output_child: pathlib.Path, run_exit: int
) -> None:
    """親終了後の両pipe出力を初回から全量回収し、同じjobの再取得と一致させる。"""
    project[1]["RUN_EXIT"] = str(run_exit)
    with _pending_output_job(project, output_child) as (controller, job):
        pending = _invoke(project, "job", job.name)
        assert pending.returncode == 1 and "未完了" in pending.stdout
        pathlib.Path(project[1]["RELEASE"]).touch()
        stdout, stderr = controller.communicate(timeout=20)
        assert controller.returncode == run_exit, stdout + stderr
        assert "後続出力" * 20000 in stdout
        assert stderr == "後続診断" * 20000
        retrieved = _invoke(project, "job", job.name)
        assert retrieved.returncode == run_exit
        assert retrieved.stdout == (job / "stdout.log").read_text(encoding="utf-8") + f"job {job.name}: 終了 exit={run_exit}\n"
        assert retrieved.stderr == stderr == (job / "stderr.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("cancel", [False, True], ids=["deadline", "stop"])
@pytest.mark.skipif(sys.platform != "linux", reason="親終了後の子孫はLinux subreaperで所有する")
def test_public_job_limits_output_wait_after_parent_exit(
    project: tuple[pathlib.Path, dict[str, str]], output_child: pathlib.Path, cancel: bool
) -> None:
    """直接の親が終了しても、未閉鎖pipeの待機へ上限と停止要求を適用する。"""
    script = project[0] / "libexec/update_dotfiles_job.py"
    source = script.read_text(encoding="utf-8")
    script.write_text(source.replace("_TIMEOUT = 7200", "_TIMEOUT = 2"), encoding="utf-8")
    if cancel:
        environment = project[1]
        environment["CHILD_SESSION"] = "1"
        probe = project[0] / "pgid-probe"
        probe.mkdir()
        # 別sessionへ移った子だけが残る状態で、回収済みrootの数値PGIDを外部の群とみなす。
        # OS境界で拒否するため、実際の無関係なプロセス群へ信号を送らない。
        (probe / "sitecustomize.py").write_text(
            "import os, pathlib\n"
            "original_killpg = os.killpg\n"
            "def guarded_killpg(pid, sig):\n"
            "    ready = pathlib.Path(os.environ['READY'])\n"
            "    if ready.with_suffix('.reaped').exists() and pid == int(ready.read_text()):\n"
            "        raise RuntimeError('終了済み親のPGIDを操作した')\n"
            "    return original_killpg(pid, sig)\n"
            "os.killpg = guarded_killpg\n",
            encoding="utf-8",
        )
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(probe), environment.get("PYTHONPATH", ""))))
    with _pending_output_job(project, output_child) as (controller, job):
        if cancel:
            pathlib.Path(project[1]["READY"]).with_suffix(".reaped").touch()
            (job / "stop").touch()
        stdout, stderr = controller.communicate(timeout=20)
        assert controller.returncode == (130 if cancel else 124), stdout + stderr
        assert str(job / "stdout.log") in stderr
        assert str(job / "stderr.log") in stderr
        assert "update-dotfilesを再実行" in stderr
        assert not psutil.pid_exists(int(output_child.read_text(encoding="utf-8")))
        retrieved = _invoke(project, "job", job.name)
        assert retrieved.returncode == controller.returncode
        assert retrieved.stderr == stderr


@_POSIX
def test_public_job_preserves_service_with_closed_output(project: tuple[pathlib.Path, dict[str, str]]) -> None:
    """両出力を分離したサービスが存続したまま、公開jobが正常完了する。"""
    root, environment = project
    (root / "libexec/update_dotfiles.py").write_text(
        "import os, pathlib, subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', \"import os,pathlib,time; "
        "p=pathlib.Path(os.environ['CHILD_READY']); t=p.with_suffix('.tmp'); "
        't.write_text(str(os.getpid())); t.replace(p); time.sleep(60)"], '
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)\n",
        encoding="utf-8",
    )
    service: psutil.Process | None = None
    try:
        result = _invoke(project)
        assert result.returncode == 0, result.stdout + result.stderr
        _wait_file(pathlib.Path(environment["CHILD_READY"]))
        service = psutil.Process(int(pathlib.Path(environment["CHILD_READY"]).read_text(encoding="utf-8")))
        assert service.is_running() and service.status() != psutil.STATUS_ZOMBIE
    finally:
        if service is not None:
            service.kill()
            try:
                service.wait(timeout=1)
            except psutil.TimeoutExpired:
                # worker終了後のサービスはテストプロセスの子ではない。PID 1による回収前も区別する。
                assert service.status() == psutil.STATUS_ZOMBIE


@pytest.mark.parametrize(
    "scenario",
    [
        *[
            pytest.param(
                scenario,
                marks=[pytest.mark.windows, pytest.mark.skipif(sys.platform != "win32", reason="実Win32 APIの検証")],
            )
            for scenario in ("late", "deadline", "stop", "detached")
        ],
        "read-error",
    ],
)
def test_native_worker_output_contract(
    project: tuple[pathlib.Path, dict[str, str]], output_child: pathlib.Path, scenario: str
) -> None:
    """native workerの結果保存まで、実Win32所有とpipe回収、読取失敗を検証する。

    外部ツールの呼出だけを標準Pythonへ写し、uvの導入や実HOMEの更新を避ける。
    Linuxでの公開ランチャー試験と分け、WindowsではJob APIと生存サービスも実行する。
    """
    root, environment = project
    environment["AGENT_TOOLKIT_PROCESS_LOOP_SESSION"] = "1"
    state = pathlib.Path(environment["LOCALAPPDATA"] if sys.platform == "win32" else environment["XDG_STATE_HOME"])
    job = state / "agent-toolkit/update-jobs" / str(uuid.uuid4())
    job.mkdir(parents=True)
    (job / "input.json").write_text(json.dumps({"arguments": []}), encoding="utf-8")
    if scenario == "detached":
        parent = root / "libexec/update_dotfiles.py"
        source = parent.read_text(encoding="utf-8")
        original = (
            f"subprocess.Popen([sys.executable, {str(root / 'libexec/child.py')!r}], "
            "start_new_session=os.environ.get('CHILD_SESSION') == '1')"
        )
        assert source.count(original) == 1
        parent.write_text(
            source.replace(
                original,
                f"subprocess.Popen([sys.executable, {str(root / 'libexec/child.py')!r}], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
            ),
            encoding="utf-8",
        )
    driver = root / "libexec/native_worker_test_driver.py"
    driver.write_text(
        "import pathlib, subprocess, sys, unittest.mock\n"
        "import update_dotfiles_job as job\n"
        f"scenario = {scenario!r}\n"
        "job._TIMEOUT = 2 if scenario == 'deadline' else 20\n"
        "run = job._run_command\n"
        "def mapped(command, directory, deadline, env):\n"
        "    native = [sys.executable, str(pathlib.Path(__file__).with_name('update_dotfiles.py'))] "
        "if command[1:2] == ['run'] else [sys.executable, '-c', 'pass']\n"
        "    return run(native, directory, deadline, env)\n"
        "job._run_command = mapped\n"
        "if scenario == 'read-error':\n"
        "    popen = subprocess.Popen\n"
        "    def broken_read(*args, **kwargs):\n"
        "        process = popen(*args, **kwargs)\n"
        "        process.stdout = unittest.mock.Mock(wraps=process.stdout)\n"
        "        process.stdout.read.side_effect = OSError('injected pipe read failure')\n"
        "        return process\n"
        "    subprocess.Popen = broken_read\n"
        f"raise SystemExit(job.main(['--worker', {job.name!r}]))\n",
        encoding="utf-8",
    )
    child: psutil.Process | None = None
    with (
        (job / "stdout.log").open("wb") as stdout,
        (job / "stderr.log").open("wb") as stderr,
        subprocess.Popen(
            [sys.executable, str(driver)],
            env=environment,
            stdout=stdout,
            stderr=stderr,
        ) as worker,
    ):
        try:
            if scenario != "read-error":
                _wait_file(output_child)
                child = psutil.Process(int(output_child.read_text(encoding="utf-8")))
                _wait_file(pathlib.Path(environment["READY"]))
                with contextlib.suppress(psutil.NoSuchProcess):
                    psutil.Process(int(pathlib.Path(environment["READY"]).read_text(encoding="utf-8"))).wait(timeout=15)
                if scenario == "late":
                    pathlib.Path(environment["RELEASE"]).touch()
                if scenario == "stop":
                    (job / "stop").touch()
            code = worker.wait(timeout=25)
            expected = {"deadline": 124, "stop": 130, "read-error": 1}.get(scenario, 0)
            assert code == expected, (job / "stderr.log").read_text(encoding="utf-8")
            assert json.loads((job / "result.json").read_text(encoding="utf-8"))["exit_code"] == code
            if scenario == "late":
                assert (job / "stdout.log").read_text(encoding="utf-8") == "後続出力" * 20000
                assert (job / "stderr.log").read_text(encoding="utf-8") == "後続診断" * 20000
            if scenario == "detached":
                assert child is not None and child.is_running()
            if scenario in ("deadline", "stop"):
                assert child is not None and not child.is_running()
            if scenario == "read-error":
                assert "injected pipe read failure" in (job / "stderr.log").read_text(encoding="utf-8")
        finally:
            pathlib.Path(environment["RELEASE"]).touch()
            if worker.poll() is None:
                (job / "stop").touch()
            worker.wait(timeout=25)
            if child is not None:
                with contextlib.suppress(psutil.NoSuchProcess):
                    child.wait(timeout=15)


@pytest.mark.parametrize("cancel", [False, True], ids=["controller-killed", "ctrl-c"])
@_POSIX
def test_controller_lifetime_and_cancel_are_distinct(
    project: tuple[pathlib.Path, dict[str, str]],
    cancel: bool,
) -> None:
    """controller喪失後は継続し、Ctrl+Cでは自身のworker停止と結果回収を行う。"""
    root, environment = project
    environment["HOLD"] = "1"
    with subprocess.Popen(
        [str(root / "bin/update-dotfiles")],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        start_new_session=True,
    ) as controller:
        try:
            _wait_file(pathlib.Path(environment["READY"]))
            job = _job(project)
            pending = _invoke(project, "job", job.name)
            assert pending.returncode != 0 and "未完了" in pending.stdout
            if cancel:
                os.killpg(controller.pid, signal.SIGINT)
            else:
                # Bashの子native controllerも停止する。同じSIDから分離したworkerは対象外。
                os.killpg(controller.pid, signal.SIGKILL)
                pathlib.Path(environment["RELEASE"]).touch()
            controller.communicate(timeout=20)
            _wait_file(job / "result.json")
            result = _invoke(project, "job", job.name)
            assert result.returncode == (130 if cancel else 0), result.stdout + result.stderr
        finally:
            pathlib.Path(environment["RELEASE"]).touch()
            if controller.poll() is None:
                controller.kill()
            controller.communicate(timeout=20)
            if pathlib.Path(environment["READY"]).exists():
                _wait_file(_job(project) / "result.json")


@pytest.mark.parametrize(
    "ps_missing",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(sys.platform != "linux", reason="procfsで子孫を列挙するのはLinuxだけである"),
        ),
    ],
    ids=["default", "ps-missing"],
)
@_POSIX
def test_job_timeout_records_failure_and_reaps_child(
    project: tuple[pathlib.Path, dict[str, str]],
    tmp_path: pathlib.Path,
    ps_missing: bool,
) -> None:
    """短縮した全体上限で、停止した更新子の回収と終了記録を検証する。

    ps-missingでは、使われたら記録して失敗する`ps`をPATHの先頭に置き、
    procpsの無いLinuxでも停止経路が子孫を回収できることを確かめる。
    """
    root, environment = project
    ps_used = tmp_path / "ps-used"
    if ps_missing:
        fake_bin = tmp_path / "no-procps-bin"
        fake_bin.mkdir()
        (fake_bin / "ps").write_text(f'#!/bin/sh\ntouch "{ps_used}"\nexit 127\n', encoding="utf-8")
        (fake_bin / "ps").chmod(0o755)
        environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    script = root / "libexec/update_dotfiles_job.py"
    source = script.read_text(encoding="utf-8")
    assert source.count("_TIMEOUT = 7200") == 1
    script.write_text(source.replace("_TIMEOUT = 7200", "_TIMEOUT = 1"), encoding="utf-8")
    environment["HOLD"] = "1"
    environment["SPAWN_CHILD"] = "1"
    try:
        result = _invoke(project)
        assert result.returncode == 124, result.stdout + result.stderr
        record = json.loads((_job(project) / "result.json").read_text(encoding="utf-8"))
        assert record["exit_code"] == 124 and "未完了" in result.stderr
        assert str(_job(project) / "stdout.log") in result.stderr
        assert str(_job(project) / "stderr.log") in result.stderr
        assert "原因を解消してupdate-dotfilesを再実行" in result.stderr
        for marker in ("READY", "CHILD_READY"):
            pid = int(pathlib.Path(environment[marker]).read_text(encoding="utf-8"))
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert not ps_used.exists()
    finally:
        pathlib.Path(environment["RELEASE"]).touch()
        child_marker = pathlib.Path(environment["CHILD_READY"])
        if child_marker.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(child_marker.read_text(encoding="utf-8")), signal.SIGKILL)


def test_tree_listing_failure_still_reaps_direct_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """子孫の列挙失敗を保持し、直接の子の無期限waitも防ぐ。"""
    process = Mock(spec=subprocess.Popen)
    process.pid = 42
    process.poll.return_value = None
    monkeypatch.setattr(update_dotfiles_job.sys, "platform", "linux")
    kill_group = Mock()
    monkeypatch.setattr(update_dotfiles_job.os, "killpg", kill_group)

    def fail_listing() -> None:
        raise PermissionError("/proc")

    monkeypatch.setattr(update_dotfiles_job, "_procfs_parents", fail_listing)
    with pytest.raises(PermissionError):
        # 停止所有関数の失敗分岐へ故障を注入する。
        update_dotfiles_job._stop_tree(process)  # pylint: disable=protected-access
    kill_group.assert_called_once_with(42, signal.SIGKILL)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=30)


@pytest.mark.parametrize("identifier", ["../other", "not-a-uuid", "x; echo injected"])
def test_job_rejects_non_uuid_without_running_update(identifier: str, capsys: pytest.CaptureFixture[str]) -> None:
    """外部のjob指定を任意パスやshellとして解釈しない。"""
    assert update_dotfiles_job.main(["job", identifier]) == 1
    assert "job" in capsys.readouterr().err


@pytest.mark.parametrize(
    "platform,expected", [("linux", {"start_new_session": True}), ("win32", {"creationflags": 0x09000200})]
)
def test_worker_separation_is_platform_specific(platform: str, expected: dict[str, object]) -> None:
    """WindowsのJob分離とPOSIXのSID分離を各OS値から選ぶ。

    WindowsはDETACHED_PROCESS（0x8）を使わず、CREATE_NO_WINDOW（0x08000000）で子孫の可視ウィンドウを防ぐ。
    """
    assert update_dotfiles_job.separation_options(platform) == expected


@pytest.mark.parametrize("finished", [False, True])
def test_only_expired_completed_jobs_are_collected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    finished: bool,
) -> None:
    """保持期間を過ぎた完了jobだけ回収し、未完了出力を残す。"""
    monkeypatch.setattr(update_dotfiles_job, "state_root", lambda: tmp_path)
    old = tmp_path / str(uuid.uuid4())
    old.mkdir()
    (old / "input.json").write_text(json.dumps({"thread_id": "old", "started_at": "old"}), encoding="utf-8")
    if finished:
        (old / "result.json").write_text(
            json.dumps({"exit_code": 0, "finished_at": "2000-01-01T00:00:00+00:00"}), encoding="utf-8"
        )

    # 新規jobの起動だけを拒否し、mainの通常cleanupを通過させる。
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("test: worker unavailable")

    monkeypatch.setattr(update_dotfiles_job.subprocess, "Popen", unavailable)
    assert update_dotfiles_job.main([]) == 1
    assert old.exists() is not finished


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_state_root_stays_in_isolated_platform_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    """各OSの既存状態領域へjobを配置する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", str(tmp_path / "override"))
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "home")
    base = {
        "linux": tmp_path / "state",
        "win32": tmp_path / "override",
        "darwin": tmp_path / "home/Library/Application Support",
    }[platform]
    assert update_dotfiles_job.state_root(platform) == base / "agent-toolkit/update-jobs"
