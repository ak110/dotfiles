"""隔離入力と副作用の代替で、対象分類・使用中保護・cache周期の判定を確かめる。"""

# pylint: disable=protected-access  # 対象分類・削除境界の内部関数を隔離環境で検証する。

import json
import os
import stat
import subprocess
import types
import typing
from pathlib import Path

import psutil
import pytest
from agent_toolkit._common import dotfiles_mode

from pytools._internal import _test_helpers, common, prune_installation, setup_mise


def _prepare(tmp_path, monkeypatch, mode):
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

    def run(command, **_kwargs):
        calls.append(command)
        output = (
            str(tmp_path / "uv-cache")
            if command[1:] == ["cache", "dir"]
            else (
                json.dumps({"node": [{"version": "24"}], "rust": [{"version": "1"}], "actionlint": [{"version": "1"}]})
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
    assert ["/fixture/uv", "cache", "clean"] in calls


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
    assert prune_installation.run().failure is None
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 1
    assert prune_installation.run().failure is None
    now += 29 * 86400
    assert prune_installation.run().failure is None
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 1
    assert calls.count(["/fixture/uv", "cache", "prune"]) == 2
    now += 86400
    assert prune_installation.run().failure is None
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 2
    assert ["/fixture/npm", "cache", "verify"] in calls
    assert ["/fixture/pnpm", "store", "prune"] in calls
    assert not any("--force" in command for command in calls if "uv" in command[0])


@pytest.mark.parametrize("locked", [False, True])
def test_uv_cleanup_failure_preserves_success_time_and_continues_other_tools(tmp_path, monkeypatch, locked):
    """競合・通常失敗で成功日時を記録せず、他ツールの整理と次回再試行を保つ。"""
    _, calls = _prepare(tmp_path, monkeypatch, "full")
    normal = common.run_subprocess
    failing = True

    def execute(command, **kwargs):
        if command[1:] == ["cache", "clean"] and failing:
            calls.append(command)
            return subprocess.CompletedProcess(command, 2, "", "when waiting for lock on cache" if locked else "failed")
        return normal(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    result = prune_installation.run()
    assert result.deferred is locked
    assert (result.failure is None) is locked
    assert ["/fixture/mise", "cache", "prune"] in calls
    assert ["/fixture/npm", "cache", "verify"] in calls
    failing = False
    assert prune_installation.run().failure is None
    assert calls.count(["/fixture/uv", "cache", "clean"]) == 2
