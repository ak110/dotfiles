"""minimalの整理で、npmのglobal導入物と旧npm版、mise管理のNodeを扱う判定を確かめる。"""

# pylint: disable=protected-access  # 対象分類・削除境界の内部関数を隔離環境で検証する。

import json
import logging
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pytools._internal import (
    common,
    prune_installation,
)
from pytools._internal.prune_installation_test_helpers import maintain, prepare


@pytest.mark.parametrize(("mode", "npm_fails"), [("minimal", False), ("minimal", True), ("full", False)])
def test_npm_prune_uses_installed_node_without_configured_version(tmp_path, monkeypatch, mode, npm_fails):
    """minimalの設定はNodeの版を持たないため、導入済みの版のnpmでNode削除前に整理し、失敗時は削除へ進まない。"""
    normal_lookup = prune_installation.shutil.which
    real_resolve = common.resolve_executable
    _, calls = prepare(tmp_path, monkeypatch, mode)
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
        maintain(monkeypatch, tmp_path)
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
    installed = {"node": [{"version": version, "install_path": str(tmp_path / version)} for version in versions]}
    _, calls = prepare(tmp_path, monkeypatch, "minimal", installed=installed)
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
    _, calls = prepare(tmp_path, monkeypatch, "minimal", installed={"actionlint": [{"version": "1"}]})
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
    _, calls = prepare(tmp_path, monkeypatch, "minimal")
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
        if Path(command[0]).parent == system and command[1:3] == ["uninstall", "--global"]:
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
    assert set(command[3:]) == {"opencode-ai", "@google/gemini-cli", "aws-cdk"}
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


@pytest.mark.parametrize("provider", ["mise", "external"])
def test_minimal_retains_old_npm_launchers(tmp_path, monkeypatch, caplog, provider):
    """minimalでも旧版と同名binを持つ別パッケージをnpm uninstallに渡さない。"""
    node_path = tmp_path / "mise-node"
    installed = {"node": [{"version": "24", "install_path": str(node_path)}]} if provider == "mise" else {}
    _, calls = prepare(tmp_path, monkeypatch, "minimal", installed=installed)
    npm = str(tmp_path / "npm") if provider == "external" else None
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: npm)
    prefix = node_path if provider == "mise" else Path.home() / ".local"
    modules = prefix / "lib" / "node_modules"
    for name, bins in {
        "@openai/codex": {"codex": "bin/codex.js"},
        "@anthropic-ai/claude-code": {"claude": "cli.js"},
        "aws-cdk": {"nested/codex.cmd": "bin/custom.js"},
        "opencode-ai": {"opencode": "bin/opencode"},
    }.items():
        manifest = modules / name / "package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": name, "bin": bins}), encoding="utf-8")
    launchers = [prefix / "bin" / name for name in ("codex", "claude")]
    launchers[0].parent.mkdir(parents=True)
    for launcher in launchers:
        launcher.write_text("user wrapper", encoding="utf-8")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: tuple(launchers))
    monkeypatch.setattr(prune_installation, "_remove", lambda path: path.is_file() and path.unlink() is None)
    monkeypatch.setattr(prune_installation.sys, "platform", "linux")
    original_run = common.run_subprocess

    def execute(command, **kwargs):
        if command[-2:] == ["root", "--global"]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, str(modules), "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)
    assert prune_installation.run().failure is None
    targets = [
        command[command.index("--global") + 1 :] for command in calls if "uninstall" in command and "--global" in command
    ]
    assert targets == [["opencode-ai"]]
    assert all(launcher.read_text(encoding="utf-8") == "user wrapper" for launcher in launchers)
    if provider == "mise":
        assert ["/fixture/mise", "uninstall", "--all", "node"] not in calls
        assert ["/fixture/mise", "prune", "-y"] not in calls
    assert "PATHで先に解決される間" in caplog.text
    assert "正規版はfullモードで導入する" in caplog.text
    assert "uninstall --global @openai/codex" in caplog.text


def _write_npm_package(modules: Path, key: str, metadata: dict[str, object] | None) -> Path:
    package = modules.joinpath(*key.split("/"))
    package.mkdir(parents=True)
    if metadata is not None:
        (package / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
    return package


def _answer_npm_root(monkeypatch, calls, answer: Callable[[list[str]], subprocess.CompletedProcess[str]]) -> None:
    original_run = common.run_subprocess

    def execute(command, **kwargs):
        if command[-2:] == ["root", "--global"]:
            calls.append(command)
            return answer(command)
        return original_run(command, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", execute)


@pytest.mark.parametrize(
    "layout",
    ["alias", "previous-prefix", "missing-manifest", "launcher-only", "unreadable", "nested-prefix", "absent", "user-global"],
)
def test_minimal_removes_node_only_when_old_npm_cli_is_absent(tmp_path, monkeypatch, caplog, layout):
    """Node本体の配下に旧npm版が無いと確かめられた場合だけ、mise uninstallとmise pruneへ進む。

    照会時点のnpm rootと既知のパッケージキーだけでは、以前のprefixに残った導入、別名導入、
    package.jsonの欠けた導入を見落とす。
    """
    node = tmp_path / "mise-node"
    _, calls = prepare(tmp_path, monkeypatch, "minimal", {"node": [{"version": "24", "install_path": str(node)}]})
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: None)
    modules = node / "lib" / "node_modules"
    _write_npm_package(modules, "npm", {"name": "npm", "bin": {"npm": "bin/npm-cli.js"}})
    if layout == "alias":
        _write_npm_package(modules, "codex-legacy", {"name": "@openai/codex", "bin": {"codex": "bin/codex.js"}})
    elif layout == "previous-prefix":
        _write_npm_package(modules, "@openai/codex", {"name": "@openai/codex", "bin": {"codex": "bin/codex.js"}})
    elif layout == "missing-manifest":
        _write_npm_package(modules, "@openai/codex", None)
        (node / "bin").mkdir()
        (node / "bin" / "codex").write_text("user wrapper", encoding="utf-8")
    elif layout == "launcher-only":
        (node / "bin").mkdir()
        (node / "bin" / "claude").write_text("user wrapper", encoding="utf-8")
    elif layout == "unreadable":
        (_write_npm_package(modules, "codex-legacy", None) / "package.json").write_text("{", encoding="utf-8")
    elif layout == "nested-prefix":
        # Node実体の中に以前のnpm prefixを置いた配置。固定の場所だけを調べると見落とす。
        nested = node / "previous-prefix"
        _write_npm_package(
            nested / "lib" / "node_modules", "@openai/codex", {"name": "@openai/codex", "bin": {"codex": "cli.js"}}
        )
        (nested / "bin").mkdir()
        (nested / "bin" / "codex").write_text("launcher", encoding="utf-8")
    elif layout == "user-global":
        # 旧npm版でないと確認できたglobalパッケージと、そのパッケージを指す起動ファイルは削除してよい。
        package = _write_npm_package(modules, "typescript", {"name": "typescript", "bin": {"tsc": "bin/tsc"}})
        (package / "bin").mkdir()
        (package / "bin" / "tsc").write_text("#!/usr/bin/env node\n", encoding="utf-8")
        (node / "bin").mkdir()
        try:
            (node / "bin" / "tsc").symlink_to(package / "bin" / "tsc")
        except OSError:
            pytest.skip("symlinkを作成できない環境")
    elif layout not in ("absent",):
        raise AssertionError(layout)
    # 以前のprefixに残った導入では、照会時点のnpm rootはNodeの外を指す。
    queried = tmp_path / "npm-prefix" / "lib" / "node_modules" if layout == "previous-prefix" else modules
    _answer_npm_root(monkeypatch, calls, lambda command: subprocess.CompletedProcess(command, 0, str(queried), ""))

    assert prune_installation.run().failure is None
    removes_node = ["/fixture/mise", "uninstall", "--all", "node"] in calls
    prunes = ["/fixture/mise", "prune", "-y"] in calls
    assert removes_node == prunes == (layout in ("absent", "user-global"))
    assert not [command for command in calls if "uninstall" in command and "@openai/codex" in command]
    if layout not in ("absent", "user-global"):
        retention = next(message for message in caplog.messages if "mise管理のNodeを保持" in message)
        assert "`update-dotfiles`を再実行" in retention
        # 他の警告が出ない配置（起動ファイルだけなど）でも、この警告だけで次の操作を選べる。
        assert "上の警告" not in retention


@pytest.mark.parametrize("manifest", ["readable", "unreadable"])
def test_minimal_keeps_every_launcher_of_retained_package(tmp_path, monkeypatch, manifest):
    """npmのprefixが`~/.local`のときは、`~/.local/bin`の起動ファイルを削除しない。

    旧npm版のパッケージが`opencode`のbinも持つ配置や、パッケージ情報を読めない配置を含む。
    """
    _, calls = prepare(tmp_path, monkeypatch, "minimal", {})
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: str(tmp_path / "npm"))
    home = Path.home()
    modules = home / ".local" / "lib" / "node_modules"
    package = _write_npm_package(
        modules, "@openai/codex", {"name": "@openai/codex", "bin": {"codex": "bin/codex.js", "opencode": "bin/codex.js"}}
    )
    if manifest == "unreadable":
        (package / "package.json").write_text("{", encoding="utf-8")
    user_bin = home / ".local" / "bin"
    user_bin.mkdir(parents=True)
    for name in ("opencode", "agy"):
        (user_bin / name).write_text("user wrapper", encoding="utf-8")
    removed: list[Path] = []

    def record_removal(path: Path) -> bool:
        removed.append(path)
        return True

    monkeypatch.setattr(prune_installation, "_remove", record_removal)
    _answer_npm_root(monkeypatch, calls, lambda command: subprocess.CompletedProcess(command, 0, str(modules), ""))

    assert prune_installation.run().failure is None
    assert user_bin / "opencode" not in removed
    assert user_bin / "agy" not in removed
    assert not [command for command in calls if "uninstall" in command and "@openai/codex" in command]


def test_minimal_unconfirmed_npm_warns_how_to_recheck_without_removal_command(tmp_path, monkeypatch, caplog):
    """導入先やpackage.jsonを確認できない警告は、削除を案内せず確認方法と再実行の条件を示す。"""
    node = tmp_path / "mise-node"
    _, calls = prepare(tmp_path, monkeypatch, "minimal", {"node": [{"version": "24", "install_path": str(node)}]})
    external = tmp_path / "external-npm"
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: str(external))
    modules = tmp_path / "external-prefix" / "lib" / "node_modules"
    (_write_npm_package(modules, "@openai/codex", None) / "package.json").write_text("{", encoding="utf-8")

    def answer(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == str(external):
            return subprocess.CompletedProcess(command, 0, str(modules), "")
        return subprocess.CompletedProcess(command, 3, "", "fixture npm root failure")

    _answer_npm_root(monkeypatch, calls, answer)

    assert prune_installation.run().failure is None
    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    query = next(message for message in messages if "npmの導入先を確認できない" in message)
    assert "npm root --global" in query
    assert "`update-dotfiles`を再実行" in query
    unreadable = next(message for message in messages if "パッケージ情報を確認できない" in message)
    assert "package.json" in unreadable
    assert "`update-dotfiles`を再実行" in unreadable
    assert all("uninstall" not in message for message in (query, unreadable))
    assert ["/fixture/mise", "uninstall", "--all", "node"] not in calls


def test_minimal_keeps_user_launchers_when_local_is_npm_prefix_without_npm(tmp_path, monkeypatch):
    """npmを見つけられなくても、`~/.local`がnpmの導入先なら`~/.local/bin`の起動ファイルを削除しない。

    旧npm版の別名導入（キーとnameが既知の名前と異なる）が持つ追加の起動ファイル`opencode`を含む。
    """
    _, calls = prepare(tmp_path, monkeypatch, "minimal", {})
    monkeypatch.setattr(prune_installation.shutil, "which", lambda name, path=None: None)
    home = Path.home()
    _write_npm_package(
        home / ".local" / "lib" / "node_modules",
        "legacy-codex",
        {"name": "legacy-codex", "bin": {"codex": "cli.js", "opencode": "cli.js"}},
    )
    user_bin = home / ".local" / "bin"
    user_bin.mkdir(parents=True)
    for name in ("codex", "opencode"):
        (user_bin / name).write_text("launcher", encoding="utf-8")
    removed: list[Path] = []

    def record_removal(path: Path) -> bool:
        removed.append(path)
        return True

    monkeypatch.setattr(prune_installation, "_remove", record_removal)

    assert prune_installation.run().failure is None
    assert user_bin / "opencode" not in removed
    assert not [command for command in calls if "uninstall" in command and "legacy-codex" in command]
