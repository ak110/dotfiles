"""pytools._internal.setup_cli_commonのテスト。"""

import os
import subprocess
from pathlib import Path

import psutil
import pytest

from pytools._internal import setup_cli_common


def test_prepend_path_moves_existing_entry_to_front(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first"
    canonical = tmp_path / "canonical"
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(canonical), str(first)]))

    setup_cli_common.prepend_path(canonical)

    assert os.environ["PATH"].split(os.pathsep) == [str(canonical), str(first), str(first)]


@pytest.mark.parametrize("uninstall_returncode", [0, 1])
def test_migrate_npm_launchers_removes_only_owned_symlink(monkeypatch, tmp_path: Path, uninstall_returncode: int) -> None:
    canonical_prefix = tmp_path / "canonical"
    canonical = canonical_prefix / "bin" / "codex"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("", encoding="utf-8")
    old_bin = tmp_path / "old" / "bin"
    package = tmp_path / "old" / "lib" / "node_modules" / "@openai" / "codex"
    package.mkdir(parents=True)
    entry = package / "bin" / "codex.js"
    entry.parent.mkdir()
    entry.write_text("", encoding="utf-8")
    launcher = old_bin / "codex"
    old_bin.mkdir(parents=True)
    launcher.symlink_to(entry)
    npm = old_bin / "npm"
    npm.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join([str(canonical.parent), str(old_bin)]))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{tmp_path / 'old'}\n", "")
        if command[1:3] == ["root", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{tmp_path / 'old/lib/node_modules'}\n", "")
        return subprocess.CompletedProcess(command, uninstall_returncode, "", "failed")

    monkeypatch.setattr(setup_cli_common.claude_common, "run_subprocess", fake_run)

    if uninstall_returncode:
        with pytest.raises(RuntimeError):
            setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical, canonical_prefix)
    else:
        assert setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical, canonical_prefix)
    assert calls[-1] == [str(npm), "uninstall", "--global", "@openai/codex"]


def test_migrate_npm_launchers_keeps_unknown_launcher(monkeypatch, tmp_path: Path) -> None:
    canonical_prefix = tmp_path / "canonical"
    canonical = canonical_prefix / "bin" / "codex"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("", encoding="utf-8")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    (old_bin / "codex").write_text("", encoding="utf-8")
    (old_bin / "npm").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(old_bin))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1] == "prefix":
            return subprocess.CompletedProcess(command, 0, f"{tmp_path / 'prefix'}\n", "")
        root = tmp_path / "prefix" / "lib" / "node_modules"
        (root / "@openai" / "codex").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(command, 0, f"{root}\n", "")

    monkeypatch.setattr(setup_cli_common.claude_common, "run_subprocess", fake_run)

    assert not setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", canonical, canonical_prefix)
    assert not any("uninstall" in call for call in calls)


def test_windows_node_command_line_is_detected(monkeypatch) -> None:
    class NodeProcess:
        def name(self) -> str:
            return "node.exe"

        def exe(self) -> str:
            return "C:/node/node.exe"

        def cmdline(self) -> list[str]:
            return ["node.exe", "C:/node_modules/@openai/codex/bin/codex.js"]

    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    monkeypatch.setattr(setup_cli_common.psutil, "process_iter", lambda: [NodeProcess()])

    assert setup_cli_common.is_windows_cli_running("codex", "@openai/codex")


@pytest.mark.parametrize("process_name", ["node.exe", "mise.exe", "codex.exe"])
def test_windows_access_denied_for_possible_cli_process_defers(monkeypatch, process_name: str) -> None:
    class InaccessibleProcess:
        def name(self) -> str:
            return process_name

        def exe(self) -> str:
            raise psutil.AccessDenied()

        def cmdline(self) -> list[str]:
            raise AssertionError("exeでAccessDeniedとなるため到達しない")

    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    monkeypatch.setattr(setup_cli_common.psutil, "process_iter", lambda: [InaccessibleProcess()])

    assert setup_cli_common.is_windows_cli_running("codex", "@openai/codex")


def test_migrate_excludes_canonical_prefix_and_launcher(monkeypatch, tmp_path: Path) -> None:
    prefix = tmp_path / "node"
    launcher = prefix / "bin" / "codex"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    (launcher.parent / "alias" / "codex").parent.mkdir()
    (launcher.parent / "alias" / "codex").symlink_to(launcher)
    monkeypatch.setenv("PATH", os.pathsep.join([str(launcher.parent), str(launcher.parent / "alias")]))
    monkeypatch.setattr(
        setup_cli_common.claude_common,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )

    assert not setup_cli_common.migrate_npm_launchers("codex", "@openai/codex", launcher, prefix)


def test_migrate_keeps_launcher_without_adjacent_npm(monkeypatch, tmp_path: Path) -> None:
    launcher = tmp_path / "old" / "codex"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(launcher.parent))

    assert not setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/bin/codex", tmp_path / "canonical"
    )


@pytest.mark.parametrize("launcher_kind", ["cmd", "mise"])
def test_migrate_windows_owned_launchers(monkeypatch, tmp_path: Path, launcher_kind: str) -> None:
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    npm = old_bin / "npm.cmd"
    npm.write_text("", encoding="utf-8")
    root = tmp_path / "prefix" / "node_modules"
    package = root / "@openai" / "codex"
    package.mkdir(parents=True)
    if launcher_kind == "cmd":
        launcher = old_bin / "codex.cmd"
        entrypoint = package / "bin" / "codex.js"
        entrypoint.parent.mkdir()
        entrypoint.write_text("", encoding="utf-8")
        (package / "package.json").write_text('{"bin":{"codex":"bin/codex.js"}}', encoding="utf-8")
        launcher.write_text(f'@node "{entrypoint}"\n', encoding="utf-8")
    else:
        launcher = old_bin / "codex.exe"
        launcher.write_bytes(b"shim")
        monkeypatch.setattr(setup_cli_common.shutil, "which", lambda name: "C:/mise.exe" if name == "mise" else None)
    monkeypatch.setenv("PATH", str(old_bin))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{tmp_path / 'prefix'}\n", "")
        if command[1:3] == ["root", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{root}\n", "")
        if command[1:3] == ["which", "codex"]:
            shim_target = package / "npm-openai-codex" / "bin" / "codex.exe"
            shim_target.parent.mkdir(parents=True)
            shim_target.write_bytes(b"codex")
            return subprocess.CompletedProcess(command, 0, f"{shim_target}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_cli_common.claude_common, "run_subprocess", fake_run)

    assert setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


def test_migrate_windows_cmd_rejects_package_name_fragments(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    (old_bin / "npm.cmd").write_text("", encoding="utf-8")
    (old_bin / "codex.cmd").write_text("@echo @openai/codex\n", encoding="utf-8")
    root = tmp_path / "prefix" / "node_modules"
    (root / "@openai" / "codex").mkdir(parents=True)
    monkeypatch.setenv("PATH", str(old_bin))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        value = tmp_path / "prefix" if command[1] == "prefix" else root
        return subprocess.CompletedProcess(command, 0, f"{value}\n", "")

    monkeypatch.setattr(setup_cli_common.claude_common, "run_subprocess", fake_run)

    assert not setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


@pytest.mark.parametrize(
    ("bin_json", "referenced_name", "expected"),
    [
        ('"bin/codex.js"', "bin/codex.js", True),
        ('{"codex":"bin/codex.js"}', "bin/codex.js", True),
        ('{"other":"bin/codex.js"}', "bin/codex.js", False),
        ('{"codex":"bin/codex.js"}', "README.md", False),
        ('{"codex":"../outside.js"}', "../outside.js", False),
    ],
)
def test_migrate_windows_cmd_uses_package_json_bin(
    monkeypatch, tmp_path: Path, bin_json: str, referenced_name: str, expected: bool
) -> None:
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    (old_bin / "npm.cmd").write_text("", encoding="utf-8")
    launcher = old_bin / "codex.cmd"
    root = tmp_path / "prefix" / "node_modules"
    package = root / "@openai" / "codex"
    package.mkdir(parents=True)
    entrypoint = package / "bin" / "codex.js"
    entrypoint.parent.mkdir()
    entrypoint.write_text("", encoding="utf-8")
    (package / "README.md").write_text("documentation", encoding="utf-8")
    outside = package.parent / "outside.js"
    outside.write_text("", encoding="utf-8")
    (package / "package.json").write_text(f'{{"bin":{bin_json}}}', encoding="utf-8")
    launcher.write_text(f'@node "{package / referenced_name}"\n', encoding="utf-8")
    monkeypatch.setenv("PATH", str(old_bin))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1] == "prefix":
            output = tmp_path / "prefix"
        elif command[1] == "root":
            output = root
        else:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, f"{output}\n", "")

    monkeypatch.setattr(setup_cli_common.claude_common, "run_subprocess", fake_run)

    if expected:
        assert setup_cli_common.migrate_npm_launchers(
            "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
        )
    else:
        assert not setup_cli_common.migrate_npm_launchers(
            "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
        )


@pytest.mark.parametrize("mise_target", ["other_package", "outside"])
def test_migrate_windows_mise_shim_rejects_wrong_target(monkeypatch, tmp_path: Path, mise_target: str) -> None:
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    old_bin = tmp_path / "old"
    old_bin.mkdir()
    (old_bin / "npm.cmd").write_text("", encoding="utf-8")
    (old_bin / "codex.exe").write_bytes(b"shim")
    root = tmp_path / "prefix" / "node_modules"
    package = root / "@openai" / "codex"
    package.mkdir(parents=True)
    target = (
        root / "@openai" / "other-package" / "npm-openai-codex.exe"
        if mise_target == "other_package"
        else tmp_path / "outside" / "npm-openai-codex" / "codex.exe"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"target")
    monkeypatch.setenv("PATH", str(old_bin))
    monkeypatch.setattr(setup_cli_common.shutil, "which", lambda name: "C:/mise.exe")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1] == "prefix":
            output = tmp_path / "prefix"
        elif command[1] == "root":
            output = root
        else:
            output = target
        return subprocess.CompletedProcess(command, 0, f"{output}\n", "")

    monkeypatch.setattr(setup_cli_common.claude_common, "run_subprocess", fake_run)

    assert not setup_cli_common.migrate_npm_launchers(
        "codex", "@openai/codex", tmp_path / "canonical/codex.cmd", tmp_path / "canonical"
    )


@pytest.mark.parametrize(
    ("name", "exe", "cmdline", "expected"),
    [
        ("codex.exe", "C:/node/codex.exe", [], True),
        ("node.exe", "C:/node/node.exe", ["node.exe", "C:/bin/codex.cmd"], True),
        ("node.exe", "C:/node/node.exe", ["node.exe", r"C:\node_modules\@openai\codex\vendor\codex.exe"], True),
        ("python.exe", "C:/python/python.exe", ["python.exe", "codex"], False),
    ],
)
def test_windows_process_classification(
    monkeypatch, name: str, exe: str, cmdline: list[str], expected: bool, tmp_path: Path
) -> None:
    class Process:
        def name(self) -> str:
            return name

        def exe(self) -> str:
            return exe

        def cmdline(self) -> list[str]:
            return cmdline

    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    monkeypatch.setattr(setup_cli_common.psutil, "process_iter", lambda: [Process()])

    assert setup_cli_common.is_windows_cli_running("codex", "@openai/codex", [tmp_path / "bin" / "codex.cmd"]) is expected


@pytest.mark.parametrize("error", [psutil.NoSuchProcess(1), psutil.ZombieProcess(2)])
def test_windows_process_races_are_ignored(monkeypatch, error: Exception) -> None:
    class VanishedProcess:
        def name(self) -> str:
            raise error

    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    monkeypatch.setattr(setup_cli_common.psutil, "process_iter", lambda: [VanishedProcess()])

    assert not setup_cli_common.is_windows_cli_running("codex", "@openai/codex")
