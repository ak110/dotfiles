"""隔離入力と副作用の代替で、対象分類・使用中保護・cache周期の判定を確かめる。"""

# pylint: disable=protected-access  # 対象分類・削除境界の内部関数を隔離環境で検証する。

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
import types
import typing
from pathlib import Path

import filelock
import platformdirs
import psutil
import pytest
from agent_toolkit._common import dotfiles_mode, process_tree, windows_job

from pytools import post_apply
from pytools._internal import (
    _test_helpers,
    common,
    post_apply_outcome,
    prune_installation,
    setup_mise,
    update_log,
    warmup_pyfltr_mcp,
)

_REAL_RESOLVE_EXECUTABLE = common.resolve_executable
_REAL_RUN_SUBPROCESS = common.run_subprocess
_REAL_START_UV_CACHE_MAINTENANCE = prune_installation.start_cache_maintenance
_UV_CACHE_MAINTENANCE = ["uv-cache-maintenance"]
_HOLDER_SCRIPT = "import sys; print('ready', flush=True); sys.stdin.read()"


def _state(name: str) -> Path:
    """更新・整理・独立プロセスが共有する状態領域のパスを、公開の配置規則（platformdirs）で求める。"""
    return Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / name


def _prepare(tmp_path, monkeypatch, mode, installed=None):
    home = tmp_path / "isolated-home"
    home.mkdir()
    # HOMEだけでは、ホストから継承した共有ランタイムの明示配置が残る。
    for name, path in {
        "HOME": home,
        "USERPROFILE": home,
        "RUSTUP_HOME": home / ".rustup",
        "CARGO_HOME": home / ".cargo",
        "CODEX_HOME": home / ".codex",
        "CLAUDE_CONFIG_DIR": home / ".claude",
        "MISE_DATA_DIR": home / "mise",
        "MISE_DOTNET_ROOT": home / "mise/dotnet-root",
        "AGENT_TOOLKIT_RUNTIME_DATA": home / "runtime",
        "LOCALAPPDATA": home / "AppData/Local",
        "XDG_DATA_HOME": home / ".local/share",
        "XDG_STATE_HOME": home / ".local/state",
        "XDG_CACHE_HOME": home / ".cache",
    }.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setattr(Path, "home", lambda: home)
    root = tmp_path / "dotfiles"
    root.mkdir()
    (root / "mise.toml").write_text('[tools]\nprek="latest"\nrust="1"\nactionlint="latest"\n')
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, mode)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(prune_installation, "_stop_daemon", lambda: None)
    monkeypatch.setattr(prune_installation, "_active_users", lambda paths: [])
    monkeypatch.setattr(prune_installation, "_remove", lambda _path: False)
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: Path("/fixture/mise"))
    monkeypatch.setattr(common, "resolve_executable", lambda name: Path("/fixture") / name)
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: str(Path("/fixture") / name))
    calls = []

    def start_cache_maintenance() -> int:
        calls.append(_UV_CACHE_MAINTENANCE)
        return 4242

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", start_cache_maintenance)
    monkeypatch.setattr(warmup_pyfltr_mcp, "run", post_apply_outcome.PostApplyOutcome)

    def run(command, **_kwargs):
        calls.append(command)
        output = (
            str(tmp_path / "uv-cache")
            if command[1:] == ["cache", "dir"]
            else (
                json.dumps(
                    installed
                    if installed is not None
                    else {"node": [{"version": "24"}], "rust": [{"version": "1"}], "actionlint": [{"version": "1"}]}
                )
                if command[1:4] == ["ls", "--installed", "--json"]
                else ""
            )
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(common, "run_subprocess", run)
    return root, calls


def test_active_old_environment_survives_prune(tmp_path, monkeypatch):
    _, calls = _prepare(tmp_path, monkeypatch, "minimal")
    environment = tmp_path / "old-environment"
    environment.mkdir()
    (environment / "python").write_text("in use")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (environment,))
    monkeypatch.setattr(prune_installation, "_active_users", lambda paths: ["agents_server (pid 123)"])
    result = prune_installation.run()
    assert result.failure is not None
    assert result.next_action is not None
    assert "update-dotfiles --prune" in result.next_action
    assert (environment / "python").read_text() == "in use"
    assert not calls


def test_active_rustup_defers_minimal_cleanup(tmp_path, monkeypatch):
    """toolchain配置外のrustupも、外部CLIや削除を始める前に使用中と判定する。"""
    active_users = prune_installation._active_users
    _, calls = _prepare(tmp_path, monkeypatch, "minimal")
    executable = Path(os.environ["CARGO_HOME"]) / "bin/rustup"
    process = types.SimpleNamespace(
        pid=123,
        username=lambda: "fixture",
        as_dict=lambda _attributes: {
            "name": "rustup",
            "exe": str(executable),
            "cmdline": [str(executable), "component", "list"],
            "username": "fixture",
            "uids": types.SimpleNamespace(real=os.getuid() if os.name != "nt" else None),
            "status": psutil.STATUS_RUNNING,
        },
    )
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: [])
    monkeypatch.setattr(psutil, "process_iter", lambda: [process])
    monkeypatch.setattr(psutil, "Process", lambda _pid=None: process)
    monkeypatch.setattr(prune_installation, "_active_users", active_users)
    result = prune_installation.run()
    assert result.failure is not None and "rustup" in result.failure
    assert not calls


@pytest.mark.parametrize("legacy_uvx", [False, True])
@pytest.mark.parametrize("late_reference", [False, True])
def test_process_scan_detects_python_from_old_shared_environment(tmp_path, monkeypatch, legacy_uvx, late_reference):
    """実行ファイルの実体が共通Pythonでも、argvの仮想環境パスで参照を判定する。"""
    environment = tmp_path / "environments/old"
    process = types.SimpleNamespace(
        pid=123,
        info={
            "name": "python",
            "exe": "/runtime/python",
            "cmdline": (
                [str(tmp_path / "uv-cache/bin/pyfltr"), "mcp"]
                if legacy_uvx
                else (
                    ["/runtime/python", "-X", "utf8", "/wrapper/dispatch.py", "--target", str(environment / "bin/python")]
                    if late_reference
                    else [str(environment / "bin/python"), "/plugin/agent_toolkit/agents_server_mcp.py"]
                )
            ),
            "uids": types.SimpleNamespace(real=os.getuid()) if os.name != "nt" else None,
            "username": prune_installation.psutil.Process().username(),
            "status": "running",
        },
    )
    own = prune_installation.psutil.Process()
    current = types.SimpleNamespace(pid=process.pid, as_dict=lambda _attributes: process.info)
    monkeypatch.setattr(prune_installation.psutil, "process_iter", lambda: [process])
    monkeypatch.setattr(prune_installation.psutil, "Process", lambda pid=None: own if pid is None else current)
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: ())
    assert prune_installation._active_users((environment,)) == ["python (pid 123)"]


def test_full_keeps_project_development_tools_and_old_environments(tmp_path, monkeypatch):
    root, calls = _prepare(tmp_path, monkeypatch, "full")
    assert "actionlint" not in prune_installation.excluded_tools(root, minimal=False)
    result = prune_installation.run()
    assert result.failure is None
    assert all("uninstall" not in command for command in calls)
    assert _UV_CACHE_MAINTENANCE in calls
    assert not [command for command in calls if command[1:2] in (["cache"], ["store"])]
    _maintain(monkeypatch, tmp_path)
    assert ["/fixture/mise", "cache", "prune"] in calls
    assert ["/fixture/npm", "cache", "verify"] in calls
    assert ["/fixture/pnpm", "store", "prune"] in calls


@pytest.mark.parametrize("missing", ["exe", "cmdline", "both"])
@pytest.mark.parametrize("referenced", [False, True])
def test_minimal_cleanup_uses_available_process_references(tmp_path, monkeypatch, missing, referenced):
    """argvでexeの取得失敗を補い、argv不明の間接利用と実参照は保持する。"""
    active_users = prune_installation._active_users
    _, calls = _prepare(tmp_path, monkeypatch, "minimal")
    target = tmp_path / "old-environment"
    executable = str(target / "python") if referenced else str(tmp_path / "unrelated/program")
    process = types.SimpleNamespace(
        pid=123,
        username=lambda: "fixture",
        as_dict=lambda _attributes: {
            "name": "python",
            "exe": executable if missing == "cmdline" else None,
            "cmdline": [executable] if missing == "exe" else None,
            "username": "fixture",
            "uids": types.SimpleNamespace(real=os.getuid() if os.name != "nt" else None),
            "status": psutil.STATUS_RUNNING,
        },
    )
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (target,))
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: [])
    monkeypatch.setattr(psutil, "process_iter", lambda: [process])
    monkeypatch.setattr(psutil, "Process", lambda _pid=None: process)
    monkeypatch.setattr(prune_installation, "_active_users", active_users)
    result = prune_installation.run()
    if missing != "exe" or referenced:
        assert result.failure is not None
        assert not calls
    else:
        assert result.failure is None
        assert calls


def test_invalid_runtime_home_preserves_entire_worktree(tmp_path, monkeypatch):
    """ランタイム用の環境変数が作業ツリー全体を指した場合は削除前に止める。"""
    root, calls = _prepare(tmp_path, monkeypatch, "minimal")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (root,))
    result = prune_installation.run()
    assert result.failure is not None
    assert "作業ツリー全体" in result.failure
    assert (root / "mise.toml").is_file()
    assert not calls


@pytest.mark.parametrize("name", ["RUSTUP_HOME", "CARGO_HOME"])
def test_shared_runtime_root_guard_applies_before_child_cleanup(tmp_path, monkeypatch, name):
    root, calls = _prepare(tmp_path, monkeypatch, "minimal")
    monkeypatch.setenv(name, str(root))
    result = prune_installation.run()
    assert result.failure is not None
    assert not calls


@pytest.mark.parametrize(("mode", "npm_fails"), [("minimal", False), ("minimal", True), ("full", False)])
def test_npm_prune_uses_installed_node_without_configured_version(tmp_path, monkeypatch, mode, npm_fails):
    """minimalの設定はNodeの版を持たないため、導入済みの版のnpmでNode削除前に整理し、失敗時は削除へ進まない。"""
    normal_lookup = prune_installation.shutil.which
    real_resolve = common.resolve_executable
    _, calls = _prepare(tmp_path, monkeypatch, mode)
    shims = tmp_path / "mise-data" / "shims"
    shims.mkdir(parents=True)
    npm = shims / ("npm.cmd" if os.name == "nt" else "npm")
    npm.write_text("shim", encoding="utf-8")
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(shims))
    monkeypatch.setenv("MISE_DATA_DIR", str(shims.parent))
    monkeypatch.setattr(prune_installation.shutil, "which", normal_lookup)
    monkeypatch.setattr(common, "resolve_executable", lambda name: Path("/fixture/uv") if name == "uv" else real_resolve(name))
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: ())
    original_run = common.run_subprocess
    uninstall = ["/fixture/mise", "exec", "node@24", "--", "npm", "uninstall", "--global"]
    node_removal = ["/fixture/mise", "uninstall", "--all", "node"]

    def execute(command, **kwargs):
        if command[0] == str(npm) and mode == "minimal":
            # aws.tqzh.tkのminimalで観測したshimの応答。設定がNodeの版を持たないため、どの引数でも失敗する。
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, "", "mise ERROR No version is set for shim: npm")
        if command[: len(uninstall)] == uninstall and npm_fails:
            calls.append(command)
            return subprocess.CompletedProcess(command, 3, "", "npm uninstall failed")
        if command[1:] == ["reshim", "--force"] and node_removal in calls:
            # miseはNodeを削除した後のreshimでnpmのshimを除く。
            npm.unlink()
        return original_run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    result = prune_installation.run()
    uninstalls = [index for index, command in enumerate(calls) if command[: len(uninstall)] == uninstall]
    if mode == "full":
        _maintain(monkeypatch, tmp_path)
        assert [str(npm), "cache", "verify"] in calls
        assert result.failure is None
        assert not uninstalls
    elif npm_fails:
        assert result.failure is not None
        assert "npm uninstall failed" in result.failure
        assert len(uninstalls) == 1
        assert node_removal not in calls
    else:
        assert result.failure is None
        assert len(uninstalls) == 1
        assert uninstalls[0] < calls.index(node_removal)


def test_minimal_prunes_npm_of_every_installed_node_version(tmp_path, monkeypatch):
    """miseに複数の版のNodeが残っていれば、どの版のnpmからもNode削除前に外す。"""
    versions = ["20.19.5", "22.21.1", "24.11.1"]
    _, calls = _prepare(tmp_path, monkeypatch, "minimal", installed={"node": [{"version": version} for version in versions]})
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: ())
    result = prune_installation.run()
    assert result.failure is None
    node_removal = calls.index(["/fixture/mise", "uninstall", "--all", "node"])
    for version in versions:
        command = ["/fixture/mise", "exec", f"node@{version}", "--", "npm", "uninstall", "--global"]
        uninstalls = [index for index, called in enumerate(calls) if called[: len(command)] == command]
        assert len(uninstalls) == 1, version
        assert uninstalls[0] < node_removal


def test_minimal_without_node_skips_npm_prune(tmp_path, monkeypatch):
    """miseにもPATHにもNodeが無いminimalでは、npmの整理を行わずに成功する。"""
    _, calls = _prepare(tmp_path, monkeypatch, "minimal", installed={"actionlint": [{"version": "1"}]})
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: ())
    result = prune_installation.run()
    assert result.failure is None
    assert not [command for command in calls if "npm" in command or Path(command[0]).stem == "npm"]
    assert ["/fixture/mise", "uninstall", "--all", "actionlint"] in calls


@pytest.mark.parametrize("npm_fails", [False, True])
def test_minimal_prunes_npm_outside_mise_without_shims(tmp_path, monkeypatch, npm_fails):
    """fullで導入した先がmise以外のnpm（nvm、OSのNodeなど）でも、minimalでNode削除前に外し、失敗時は削除へ進まない。"""
    normal_lookup = prune_installation.shutil.which
    _, calls = _prepare(tmp_path, monkeypatch, "minimal")
    shims = tmp_path / "mise-data" / "shims"
    system = tmp_path / "system-node" / "bin"
    for directory in (shims, system):
        directory.mkdir(parents=True)
        npm = directory / ("npm.cmd" if os.name == "nt" else "npm")
        npm.write_text("npm", encoding="utf-8")
        npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    # miseのshimがOSのNodeより前にある（aws.tqzh.tkのPATHと同じ順序）。
    monkeypatch.setenv("PATH", os.pathsep.join([str(shims), str(system)]))
    monkeypatch.setenv("MISE_DATA_DIR", str(shims.parent))
    monkeypatch.setattr(prune_installation.shutil, "which", normal_lookup)
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: ())
    original_run = common.run_subprocess
    environments = {}

    def execute(command, **kwargs):
        if Path(command[0]).parent == system:
            calls.append(command)
            environments[len(calls) - 1] = kwargs.get("env_overrides") or {}
            return subprocess.CompletedProcess(command, 3 if npm_fails else 0, "", "npm uninstall failed" if npm_fails else "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    result = prune_installation.run()
    system_uninstalls = [
        index
        for index, command in enumerate(calls)
        if Path(command[0]).parent == system and command[1:3] == ["uninstall", "--global"]
    ]
    assert len(system_uninstalls) == 1
    command = calls[system_uninstalls[0]]
    assert {"opencode-ai", "@anthropic-ai/claude-code", "@openai/codex", "@google/gemini-cli", "aws-cdk"} <= set(command)
    # OSのnpmは`#!/usr/bin/env node`で起動するため、PATHにshimが残るとnodeのshimが失敗する。
    child_path = environments[system_uninstalls[0]]["PATH"].split(os.pathsep)
    assert str(system) in child_path
    assert str(shims) not in child_path
    node_removal = ["/fixture/mise", "uninstall", "--all", "node"]
    if npm_fails:
        assert result.failure is not None
        assert "npm uninstall failed" in result.failure
        assert node_removal not in calls
    else:
        assert result.failure is None
        assert system_uninstalls[0] < calls.index(node_removal)


def _inject_previous_generation(monkeypatch: pytest.MonkeyPatch, pid: int, *, name: str, exe: str) -> None:
    """列挙が再利用するProcessだけを以前の世代の実行名・実行ファイルにし、走査を対象のPIDへ限る。

    PIDの自然な再利用や実行ファイルの置き換えを待たずに、列挙キャッシュへ以前の値が残った状態を作る。
    """
    stale = psutil.Process(pid)
    monkeypatch.setattr(stale, "_name", name)
    monkeypatch.setattr(stale, "_exe", exe)
    monkeypatch.setitem(vars(psutil)["_pmap"], pid, stale)
    real_process_iter = psutil.process_iter

    def own_process(*args: typing.Any, **kwargs: typing.Any) -> list[psutil.Process]:
        return [item for item in real_process_iter(*args, **kwargs) if item.pid == pid]

    monkeypatch.setattr(psutil, "process_iter", own_process)


@pytest.mark.skipif(
    os.name == "nt", reason="仮想環境のpython.exeは本体を子として起動するため、起動したPIDだけに走査を限定できない"
)
def test_active_users_reads_current_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """列挙キャッシュに対象実体を指す以前の世代の実行ファイルが残っていても、現在の属性で参照を判定する。"""
    environment = tmp_path / "environments/old"
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: ())
    with _test_helpers.ResidentProcesses(tmp_path, monkeypatch) as resident:
        child = resident.start("python")
        # 起動完了の観測後に古いキャッシュを注入し、起動途中の空cmdlineと区別する。
        _inject_previous_generation(monkeypatch, child.pid, name="python", exe=str(environment / "bin/python"))
        active = prune_installation._active_users((environment,))
    assert not active


def test_full_cache_cleanup_tracks_success_per_root_and_30_day_boundary(tmp_path, monkeypatch):
    """初回・30日境界だけ全消去し、同日と29日目はpruneする。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    now = 10000000.0
    monkeypatch.setattr(prune_installation.time, "time", lambda: now)
    completed = (prune_installation.MaintenanceResult.COMPLETED, "")
    assert prune_installation.clean_uv_cache() == completed
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 1
    assert prune_installation.clean_uv_cache() == completed
    now += 29 * 86400
    assert prune_installation.clean_uv_cache() == completed
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 1
    assert calls.count(["/fixture/uv", "cache", "prune"]) == 2
    now += 86400
    assert prune_installation.clean_uv_cache() == completed
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 2
    assert not any("--force" in command for command in calls if "uv" in command[0])


@pytest.mark.parametrize("locked", [False, True])
def test_uv_cleanup_failure_preserves_success_time(tmp_path, monkeypatch, locked):
    """競合は保留、それ以外は失敗として区別し、どちらも成功日時を記録せず次回に全消去を再試行する。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    normal = common.run_subprocess
    failing = True

    def execute(command, **kwargs):
        if command[1:] == ["cache", "clean"] and failing:
            calls.append(command)
            return subprocess.CompletedProcess(command, 2, "", "when waiting for lock on cache" if locked else "failed")
        return normal(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    results = prune_installation.MaintenanceResult
    result, _detail = prune_installation.clean_uv_cache()
    assert result is (results.DEFERRED if locked else results.FAILED)
    failing = False
    assert prune_installation.clean_uv_cache()[0] is results.COMPLETED
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 2


@pytest.mark.windows
def test_update_does_not_wait_for_uv_cache_in_use(tmp_path, monkeypatch, host_environ, request):
    """他のuvが使用中でも整理を待たずに戻り、使用の終了後にuvの全消去と成功記録を完了する。"""
    native_path = os.pathsep.join(part for part in host_environ()["PATH"].split(os.pathsep) if Path(part).name != "shims")
    uv = shutil.which("uv", path=native_path)
    if uv is None:
        pytest.skip("uvが無い環境では実際のキャッシュロックを再現できない")
    which = shutil.which
    _prepare(tmp_path, monkeypatch, "full")
    # 独立プロセスは回収の前に自然に終了し得る。Windowsは終了後のPIDの世代を後から確かめられないため、
    # 起動直後から回収まで同じ世代のhandleを保持し、終了の前後どちらでも同じ所有として回収する。
    pins = process_tree.ProcessIdentityPins()
    request.addfinalizer(pins.close)
    maintenance: list[psutil.Process] = []

    def start() -> int:
        pid = _REAL_START_UV_CACHE_MAINTENANCE()
        maintenance.extend(process_tree.collect_descendants(pid, pins=pins, include_root=True))
        return pid

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", start)
    # 隔離PATHはmise shimを別名で置くため、整理側が解決するuvをshimを経由しない本体にそろえる。
    monkeypatch.setenv("PATH", os.pathsep.join((str(Path(uv).parent), os.environ["PATH"])))
    monkeypatch.setattr(common, "resolve_executable", _REAL_RESOLVE_EXECUTABLE)
    monkeypatch.setattr(common, "run_subprocess", _REAL_RUN_SUBPROCESS)
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None if name in {"npm", "pnpm"} else which(name, path=path))
    cache = tmp_path / "uv-cache"
    marker = cache / "archive-v0" / "previous-entry"
    marker.mkdir(parents=True)
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    # 実行中の`uv run`はキャッシュの共有ロックを終了まで保持する。
    # 時間上限なし: 標準入力の終了で止まる保持役であり、終了時のcloseで止まる。
    with subprocess.Popen(
        [uv, "run", "--no-project", "--python", sys.executable, "python", "-c", _HOLDER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        started = time.monotonic()
        result = prune_installation.run()
        elapsed = time.monotonic() - started
    assert elapsed < 10, f"使用中のuvキャッシュの整理を{elapsed:.1f}秒待った"
    assert result.failure is None
    record = _state("uv-cache-maintenance.json")
    # 試験全体の時間上限（`--timeout=60`）より先に期限を迎え、未完了を期限切れでなく診断付きの失敗で示す。
    deadline = time.monotonic() + 30
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert record.exists(), "独立プロセスが30秒以内に結果を記録しなかった"
    assert json.loads(record.read_text(encoding="utf-8"))["result"] == "completed"
    assert os.path.normcase(str(cache.resolve())) in json.loads(_state("uv-cache-clean.json").read_text(encoding="utf-8"))
    assert not marker.exists()
    # 独立プロセスは記録の後もuv以外の整理とMCPの事前構築を続ける。どちらもこの試験の対象外で、
    # 所要は実行環境の負荷と導入済みのツールで変わる（Windows CIで記録の後20秒を超えた）。
    # 自然な終了を待たずに回収し、独立プロセスを試験の外へ残さない。
    assert maintenance, "起動直後の独立プロセスの世代を保持できなかった"
    stopped = process_tree.stop_tree(None, known=maintenance, pins=pins)
    assert not stopped.residual and not stopped.unresolved, stopped.descriptions()


def _uv_clean_failure(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]) -> dict[str, str | None]:
    """uvの全消去・pruneを、返す辞書の`stderr`の診断で失敗させる。`None`の間は成功させる。"""
    normal = common.run_subprocess
    failure: dict[str, str | None] = {"stderr": None}

    def execute(command, **kwargs):
        stderr = failure["stderr"]
        if Path(command[0]).stem == "uv" and command[1:] in (["cache", "clean"], ["cache", "prune"]) and stderr is not None:
            calls.append(command)
            return subprocess.CompletedProcess(command, 2, "", stderr)
        return normal(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    return failure


def _maintain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """独立プロセスの公開入口を実行し、永続ログの本文を返す。"""
    log = tmp_path / "update-dotfiles.log"
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", log)
    post_apply.main(["--internal-uv-cache-maintenance"])
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_failed_maintenance_fails_following_updates_until_resolved(tmp_path, monkeypatch):
    """独立プロセスの失敗は、解消するまで次の更新の失敗として診断・次の操作・永続ログのパスとともに返す。

    保留は未解消の失敗を上書きしない。修正前は失敗が永続ログにだけ残り、更新の画面と終了コードから消えていた。
    """
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    failure = _uv_clean_failure(monkeypatch, calls)
    failure["stderr"] = "error: fixture disk failure"
    _maintain(monkeypatch, tmp_path)
    failed = prune_installation.run()
    assert failed.failure is not None and "fixture disk failure" in failed.failure
    assert failed.next_action is not None and str(update_log.LOG_PATH) in failed.next_action
    failure["stderr"] = "error: Timeout (30s) when waiting for lock on cache"
    _maintain(monkeypatch, tmp_path)
    still_failed = prune_installation.run()
    assert still_failed.failure is not None and "fixture disk failure" in still_failed.failure
    # 保留は整理を試していないため失敗を残し、最後の試行が保留だったことと、その診断を添える。
    assert "保留" in still_failed.failure and "when waiting for lock" in still_failed.failure
    failure["stderr"] = None
    _maintain(monkeypatch, tmp_path)
    assert prune_installation.run().failure is None


def test_npm_failure_is_reported_next_update_even_when_uv_defers(tmp_path, monkeypatch, caplog):
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    _uv_clean_failure(monkeypatch, calls)["stderr"] = "when waiting for lock on cache"
    run = common.run_subprocess

    def fail_verify(command, **kwargs):
        if command[1:] == ["cache", "verify"]:
            return subprocess.CompletedProcess(command, 1, "", "fixture verify failed")
        return run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", fail_verify)
    assert prune_installation.run().failure is None
    _maintain(monkeypatch, tmp_path)
    failed = prune_installation.run()
    assert failed.failure and "fixture verify failed" in failed.failure
    assert ["/fixture/pnpm", "store", "prune"] in calls
    monkeypatch.setattr(common, "run_subprocess", run)
    _maintain(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO):
        assert prune_installation.run().failure is None
    assert "mise・npm・pnpmのキャッシュ整理は完了" in caplog.text


def test_corrupt_clean_record_is_reported_with_its_path(tmp_path, monkeypatch):
    """全消去の成功記録が壊れると整理は毎回失敗するため、次の更新は記録のパスを示して失敗を返す。"""
    _prepare(tmp_path, monkeypatch, "full")
    state = _state("uv-cache-clean.json")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{", encoding="utf-8")
    _maintain(monkeypatch, tmp_path)
    result = prune_installation.run()
    assert result.failure is not None and str(state) in result.failure


def test_deferred_maintenance_is_announced_with_log_path(tmp_path, monkeypatch, caplog):
    """使用中で保留した独立プロセスの結果は、次の更新が失敗にせず、永続ログのパスとともに案内する。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    _uv_clean_failure(monkeypatch, calls)["stderr"] = "error: Timeout (30s) when waiting for lock on cache"
    _maintain(monkeypatch, tmp_path)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = prune_installation.run()
    assert result.failure is None
    notices = [record.getMessage() for record in caplog.records if "保留" in record.getMessage()]
    assert notices and "when waiting for lock" in notices[0] and str(update_log.LOG_PATH) in notices[0]


def test_unexpected_maintenance_exception_is_logged_and_reported(tmp_path, monkeypatch):
    """独立プロセスの想定外の例外は、出力を持たないプロセスから永続ログと次の更新の失敗へ届く。"""
    _prepare(tmp_path, monkeypatch, "full")

    def explode(_uv: Path) -> None:
        raise KeyError("fixture-unexpected")

    monkeypatch.setattr(prune_installation, "_clean_uv_cache", explode)
    log = _maintain(monkeypatch, tmp_path)
    assert "Traceback" in log and "KeyError: 'fixture-unexpected'" in log
    result = prune_installation.run()
    assert result.failure is not None and "fixture-unexpected" in result.failure


def test_slow_verify_does_not_block_next_update_or_start_duplicate_cleanup(tmp_path, monkeypatch):
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    run = common.run_subprocess

    def check_while_verifying(command, **kwargs):
        if command[1:] == ["cache", "verify"]:
            update_lock = filelock.FileLock(str(_state("locks/update-dotfiles.lock")), timeout=0, preserve_lock_file=True)
            assert prune_installation.run(lock=update_lock).failure is None
            prune_installation.maintain_caches()
        return run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", check_while_verifying)
    _maintain(monkeypatch, tmp_path)
    assert calls.count(["/fixture/npm", "cache", "verify"]) == 1
    assert calls.count(["/fixture/mise", "cache", "prune"]) == 1
    assert json.loads(_state("tools-cache-maintenance.json").read_text(encoding="utf-8"))["result"] == "completed"


def test_maintenance_defers_while_update_is_running(tmp_path, monkeypatch):
    """更新が更新ロックを保持する間、独立プロセスはロックを待たずに整理を保留し、uvの排他操作を重ねない。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    lock = _state("locks/update-dotfiles.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with filelock.FileLock(str(lock), timeout=0, preserve_lock_file=True):
        _maintain(monkeypatch, tmp_path)
    assert time.monotonic() - started < 5
    assert not [command for command in calls if command[1:3] in (["cache", "clean"], ["cache", "prune"])]
    record = json.loads(_state("uv-cache-maintenance.json").read_text(encoding="utf-8"))
    assert record["result"] == "deferred"


def test_prune_after_update_starts_maintenance_after_releasing_update_lock(tmp_path, monkeypatch):
    """更新後の整理は、更新ロックを解放してから独立プロセスを開始し、独立プロセスがロックを取得できる。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")
    acquirable: list[bool] = []

    def start() -> int:
        try:
            with filelock.FileLock(str(_state("locks/update-dotfiles.lock")), timeout=0, preserve_lock_file=True):
                acquirable.append(True)
        except filelock.Timeout:
            acquirable.append(False)
        return 4242

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", start)
    post_apply.main(["--prune-only-if-minimal"], runner=lambda: pytest.fail("通常の更新工程へ到達した"))
    assert acquirable == [True]
    assert ["/fixture/mise", "cache", "prune"] not in calls


@pytest.mark.parametrize("cause", ["installation", "start"])
def test_mcp_environment_is_prepared_without_maintenance_process(tmp_path, monkeypatch, cause):
    """独立プロセスを開始しない・できない場合も、キャッシュ内のMCP環境をこのプロセスで事前構築する。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    warmups: list[str] = []

    def warmup() -> post_apply_outcome.PostApplyOutcome:
        warmups.append("warmup")
        return post_apply_outcome.PostApplyOutcome()

    monkeypatch.setattr(warmup_pyfltr_mcp, "run", warmup)
    if cause == "installation":
        monkeypatch.setattr(common, "find_dotfiles_root", lambda: None)
    else:

        def refuse() -> int:
            raise PermissionError("fixture")

        monkeypatch.setattr(prune_installation, "start_cache_maintenance", refuse)
    result = prune_installation.run()
    assert result.failure is not None
    assert warmups == ["warmup"]
    if cause == "installation":
        # 導入実体の整理に失敗した場合は、従来どおり取得キャッシュを整理しない。
        assert not calls
    else:
        assert "独立プロセスを起動できません" in result.failure
        assert not [command for command in calls if command[1:2] in (["cache"], ["store"])]


def test_missing_uv_still_prepares_mcp_environment_in_maintenance_process(tmp_path, monkeypatch):
    """uvを解決できない更新でも独立プロセスを開始し、独立プロセスは対象なしを記録してMCP環境を事前構築する。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    monkeypatch.setattr(common, "resolve_executable", lambda name: None)
    assert prune_installation.run().failure is None
    assert _UV_CACHE_MAINTENANCE in calls
    warmups: list[str] = []

    def warmup() -> post_apply_outcome.PostApplyOutcome:
        warmups.append("warmup")
        return post_apply_outcome.PostApplyOutcome()

    monkeypatch.setattr(warmup_pyfltr_mcp, "run", warmup)
    _maintain(monkeypatch, tmp_path)
    assert warmups == ["warmup"]
    assert json.loads(_state("uv-cache-maintenance.json").read_text(encoding="utf-8"))["result"] == "none"


def test_breakaway_refusal_is_reported(tmp_path, monkeypatch, caplog):
    """起動元のWindows Jobに離脱を拒まれ、離脱なしで独立プロセスを開始した場合は警告で記録する。"""
    _prepare(tmp_path, monkeypatch, "full")
    started: list[int] = []

    def popen(_command, **options):
        started.append(options["creationflags"])
        if options["creationflags"] & windows_job.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "アクセスが拒否されました")
        return types.SimpleNamespace(pid=4242, wait=lambda: 0)

    monkeypatch.setattr(prune_installation, "start_cache_maintenance", _REAL_START_UV_CACHE_MAINTENANCE)
    monkeypatch.setattr(prune_installation, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(prune_installation, "subprocess", types.SimpleNamespace(Popen=popen, DEVNULL=subprocess.DEVNULL))
    with caplog.at_level(logging.WARNING):
        assert prune_installation.start_cache_maintenance() == 4242
    assert len(started) == 2 and not started[1] & windows_job.CREATE_BREAKAWAY_FROM_JOB
    assert any("離脱を許可しない" in record.getMessage() for record in caplog.records)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Jobの終了が所属プロセスを止める条件を実APIで確かめる")
@pytest.mark.timeout(240)
def test_maintenance_outlives_killing_job_of_its_launcher(tmp_path, monkeypatch, host_environ, request):
    """離脱を許すKILL_ON_JOB_CLOSEのJobの中から開始したuvの整理は、そのJobの終了後も続いて完了を記録する。

    更新jobとprocess-loopは、起動した子孫をこの種類のJobで所有し、終了時にまとめて止める。
    """
    native_path = os.pathsep.join(part for part in host_environ()["PATH"].split(os.pathsep) if Path(part).name != "shims")
    uv = shutil.which("uv", path=native_path)
    assert uv is not None
    _prepare(tmp_path, monkeypatch, "minimal")
    cache = tmp_path / "uv-cache"
    marker = cache / "archive-v0" / "previous-entry"
    marker.mkdir(parents=True)
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("PATH", os.pathsep.join((str(Path(uv).parent), os.environ["PATH"])))
    record = _state("uv-cache-maintenance.json")
    starter = "from pytools._internal import prune_installation as p; print(p.start_cache_maintenance(), flush=True)"
    pins = process_tree.ProcessIdentityPins()
    request.addfinalizer(pins.close)
    # 実行中の`uv run`はキャッシュの共有ロックを終了まで保持し、独立プロセスの全消去をJobの終了後まで待たせる。
    # 時間上限なし: 標準入力の終了で止まる保持役であり、終了時のcloseで止まる。
    with subprocess.Popen(
        [uv, "run", "--no-project", "--python", sys.executable, "python", "-c", _HOLDER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        members: list[subprocess.Popen[str]] = []
        try:
            with windows_job.WindowsJob() as job:

                def member(command: list[str]) -> subprocess.Popen[str]:
                    # 時間上限なし: Jobの終了で止まる所属プロセスであり、終了はfinallyで回収する。
                    process = subprocess.Popen(  # pylint: disable=consider-using-with
                        command, stdout=subprocess.PIPE, text=True, creationflags=windows_job.CREATE_SUSPENDED
                    )
                    members.append(process)
                    # _handleはWindows版CPythonの属性であり、Linux上の型検査からは解決できない。
                    job.assign_suspended(
                        int(typing.cast(typing.Any, process)._handle),  # pylint: disable=protected-access,no-member
                        process.pid,
                    )
                    return process

                launcher = member([sys.executable, "-c", starter])
                unbroken = member([sys.executable, "-c", "import time; time.sleep(600)"])
                output, _ = launcher.communicate(timeout=60)
                assert launcher.returncode == 0, output
                maintenance = psutil.Process(int(output.strip()))
                # 保持役のロックで整理を待っている間に独立プロセスの世代を保持し、記録の後に同じ所有として回収する。
                owned = process_tree.collect_descendants(maintenance.pid, pins=pins, include_root=True)
            # Jobの終了が所属プロセスへ届いたことを、離脱していない所属プロセスの終了で確かめる。
            unbroken.wait(timeout=30)
            assert maintenance.is_running(), "Jobの終了とともに独立プロセスが止まった"
            assert not record.exists(), "独立プロセスがJobの終了前に整理を終え、Jobの終了後の生存を確かめられない"
        finally:
            for process in members:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
    deadline = time.monotonic() + 60
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert record.exists(), "独立プロセスが60秒以内に結果を記録しなかった"
    assert json.loads(record.read_text(encoding="utf-8"))["result"] == "completed"
    assert not marker.exists()
    # 独立プロセスは記録の後もuv以外の整理を続け、一時領域のロックを保持する。この試験の対象外で、所要は負荷で変わる
    # （Windows CIで試験の終了から55秒後もロックを保持し、pytestの一時領域の回収が失敗した）。自然な終了を待たずに回収する。
    assert owned, "独立プロセスの世代を保持できなかった"
    stopped = process_tree.stop_tree(None, known=owned, pins=pins)
    assert not stopped.residual and not stopped.unresolved, stopped.descriptions()
