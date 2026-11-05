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
from collections.abc import Callable
from unittest.mock import Mock

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
        'if [[ "$1 $2" == "python install" ]]; then touch "$HOME/installed"; exit 0; fi\n'
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
    assert calls[4].startswith("run --no-project --script ")
    assert calls[5:] == ([] if run_exit else ["prune --prune-only"])
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
    assert len(calls) == 2
    assert calls[0] == "python find 3.15" and calls[1].startswith("run --no-project --script ")


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
    "platform,expected", [("linux", {"start_new_session": True}), ("win32", {"creationflags": 0x01000208})]
)
def test_worker_separation_is_platform_specific(platform: str, expected: dict[str, object]) -> None:
    """WindowsのJob分離とPOSIXのSID分離を各OS値から選ぶ。"""
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
