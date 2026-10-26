"""commitと更新後のhook起動をまたぐCodex plugin利用シナリオを検証する。

生成、Codexの自動導入、cache削除およびhook消費が複数領域にまたがるため、
公開操作に最も近い本ファイルをCodex plugin導入実装の隣へ置く。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_toolkit._testing import git_repository

from pytools import post_apply
from pytools._internal import common, host_roles, install_codex_plugins

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libexec"))
import update_dotfiles  # noqa: E402  # pyright: ignore[reportMissingImports]  # pylint: disable=wrong-import-position,wrong-import-order,import-error


def _write_plugin(root: Path, version: str) -> None:
    """Claude Code側に保持される最小の完全なplugin配布物を作成する。"""
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "agent-toolkit", "version": version}), encoding="utf-8")
    (root / "agent_toolkit").mkdir()
    (root / "agent_toolkit/hook.py").write_text("# hook\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts/launch.py").write_text(
        """import os, pathlib, sys
pathlib.Path(os.environ["HOOK_RESULT"]).write_text("|".join(sys.argv[1:]), encoding="utf-8")
""",
        encoding="utf-8",
    )
    (root / "version-marker").write_text(version, encoding="utf-8")


@pytest.mark.parametrize("failure", [None, "disabled", "ambiguous", "missing", "cli-failure"])
def test_external_hook_uses_effective_scope_and_reports_failed_resolution(tmp_path: Path, failure: str | None) -> None:
    """単独複製shimから公開CLIを経由し、project/scopeを反映した実launcherを起動する。"""
    wrapper = tmp_path / "bin/atk-hook"
    wrapper.parent.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook", wrapper)
    projects = [tmp_path / "project-a", tmp_path / "project-b"]
    for project in projects:
        project.mkdir()
    installed = []
    for scope in ("user", "project", "local", "unrelated"):
        root = tmp_path / scope
        _write_plugin(root, scope)
        (root / "scripts/launch.py").write_text(
            "import os,pathlib,sys\n"
            f"pathlib.Path(os.environ['HOOK_RESULT']).write_text({scope!r} + '|' + '|'.join(sys.argv[1:]), encoding='utf-8')\n",
            encoding="utf-8",
        )
        if not (failure == "missing" and scope == "local"):
            (root / "skills").mkdir()
        # versionや更新日時では選ばないことを、別projectの新しい記録で検証する。
        installed.append(
            {
                "id": "agent-toolkit@ak110-dotfiles",
                "scope": "local" if scope == "unrelated" else scope,
                "enabled": not (failure == "disabled" and scope == "local"),
                "installPath": str(root),
                "projectPath": str(tmp_path / "unrelated" if scope == "unrelated" else projects[0]),
                "lastUpdated": "2099-01-01T00:00:00Z" if scope == "unrelated" else "2026-01-01T00:00:00Z",
            }
        )
    if failure == "ambiguous":
        installed.append(dict(installed[2]))
    cli = wrapper.parent / "claude"
    cli.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        "assert sys.argv[1:] == ['plugin', 'list', '--json']\n"
        f"print({json.dumps(installed)!r})\nraise SystemExit({1 if failure == 'cli-failure' else 0})\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    if os.name == "nt":
        (wrapper.parent / "claude.cmd").write_text(f'@"{sys.executable}" "{cli}" %*\r\n', encoding="utf-8")
    output = tmp_path / "hook-output"
    environment = {**os.environ, "PATH": str(wrapper.parent), "HOOK_RESULT": str(output)}
    environment.pop("PLUGIN_ROOT", None)
    for index, project in enumerate(projects):
        result = subprocess.run(
            [sys.executable, str(wrapper), "pretooluse"],
            cwd=project,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=15,
        )
        if failure and index == 0 or failure == "cli-failure":
            assert result.returncode == 1
            assert {"disabled": "無効", "ambiguous": "一意", "missing": "実体またはskills", "cli-failure": "取得失敗"}[
                failure
            ] in result.stderr
        else:
            assert result.returncode == 0, result.stderr
            # launcher自身の実行結果と、execへ渡された絶対パスの版を確認する。
            assert (
                output.read_text(encoding="utf-8") == ("local" if index == 0 else "user") + "|agent_toolkit/hook.py|pretooluse"
            )


def test_commit_time_auto_refresh_recovers_the_live_hook_from_claude_cache(tmp_path: Path) -> None:
    """commitを契機とする別processの自動導入後も、live sessionのhookを同版配布物から復旧する。"""
    version = "1.2.2"
    sync_script = """import json, pathlib
root = pathlib.Path(__file__).parent
source = json.loads((root / "agent-toolkit/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
target = root / "agent-toolkit-codex/.codex-plugin/plugin.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"version": source["version"]}), encoding="utf-8")
"""
    refresh_script = """import json, pathlib, shutil, sys
manifest = pathlib.Path(sys.argv[1])
cache = pathlib.Path(sys.argv[2])
version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
for child in tuple(cache.iterdir()):
    if child.name != version:
        shutil.rmtree(child)
(cache / version).mkdir(parents=True, exist_ok=True)
"""
    repository = git_repository.init_repository(
        tmp_path / "dotfiles",
        files={
            "agent-toolkit/.claude-plugin/plugin.json": json.dumps({"version": version}),
            "agent-toolkit-codex/.codex-plugin/plugin.json": json.dumps({"version": version}),
            "sync_manifest.py": sync_script,
            "fake_app_server.py": refresh_script,
        },
        commit_message="initial plugin",
    )
    pre_commit = repository / ".git/hooks/pre-commit"
    pre_commit.write_text(
        f'#!/bin/sh\n"{sys.executable}" sync_manifest.py\ngit add agent-toolkit-codex/.codex-plugin/plugin.json\n',
        encoding="utf-8",
    )
    pre_commit.chmod(0o755)
    source_manifest = repository / "agent-toolkit/.claude-plugin/plugin.json"
    source_manifest.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    git_repository.commit_all(repository, "bump plugin")
    manifest = repository / "agent-toolkit-codex/.codex-plugin/plugin.json"

    home = tmp_path / "home"
    wrapper = home / ".local/bin/atk-hook"
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook", wrapper)
    claude_root = home / ".claude/plugins/cache/ak110-dotfiles/agent-toolkit" / version
    _write_plugin(claude_root, version)
    pinned = home / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit" / version
    pinned.mkdir(parents=True)
    (pinned / "deleted-by-app-server").write_text("old session", encoding="utf-8")

    # Codex 0.163.0のApp Server plugin/listで観測した非同期refreshを、別processと共有cacheで模す。
    subprocess.run(
        [sys.executable, str(repository / "fake_app_server.py"), str(manifest), str(pinned.parent)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result_path = tmp_path / "hook-result.txt"
    environment = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "PLUGIN_ROOT": str(pinned),
        "HOOK_RESULT": str(result_path),
    }

    result = subprocess.run(
        [sys.executable, str(wrapper), "pretooluse"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert result_path.read_text(encoding="utf-8") == "agent_toolkit/hook.py|pretooluse"
    assert (pinned / "version-marker").read_text(encoding="utf-8") == version
    assert json.loads(git_repository.git_output(repository, "show", "HEAD:agent-toolkit-codex/.codex-plugin/plugin.json")) == {
        "version": "1.2.3"
    }


def test_update_dotfiles_public_entry_restores_all_previous_plugin_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """update-dotfilesからpost-applyを完遂し、外部CLIが削除した旧cacheを復旧する。"""
    home = tmp_path / "home"
    root = tmp_path / "dotfiles"
    marketplace = root / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps(
            {
                "name": "ak110-dotfiles",
                "plugins": [
                    {"name": "agent-basics", "source": {"source": "local", "path": "./agent-basics"}},
                    {
                        "name": "agent-toolkit",
                        "source": {"source": "local", "path": "./agent-toolkit-codex"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    plugin_manifest = root / "agent-toolkit-codex/.codex-plugin/plugin.json"
    plugin_manifest.parent.mkdir(parents=True)
    plugin_manifest.write_text(json.dumps({"name": "agent-toolkit", "version": "1.2.3"}), encoding="utf-8")
    basics_manifest = root / "agent-basics/.codex-plugin/plugin.json"
    basics_manifest.parent.mkdir(parents=True)
    basics_manifest.write_text(json.dumps({"name": "agent-basics", "version": "1.0.0"}), encoding="utf-8")
    wrapper_source = root / "agent-toolkit/scripts/atk-hook"
    wrapper_source.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook", wrapper_source)
    cache = home / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit"
    for version in ("1.2.1", "1.2.2"):
        marker = cache / version / "version-marker"
        marker.parent.mkdir(parents=True)
        marker.write_text(version, encoding="utf-8")
    state: dict[str, object] = {"version": "1.2.2", "enabled": True}

    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_executable", Path)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr(install_codex_plugins, "CODEX_HOME", home / ".codex")
    monkeypatch.setattr(install_codex_plugins, "_hook_bin", lambda: home / ".local/bin")
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ())
    monkeypatch.setattr(install_codex_plugins, "_hooks_list", lambda: None)
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: False)

    def codex_json(args: list[str]) -> dict[str, object]:
        if args[1:3] == ["marketplace", "list"]:
            return {"marketplaces": [{"name": "ak110-dotfiles", "root": str(root)}]}
        return {
            "installed": [
                {"pluginId": "agent-basics@ak110-dotfiles", "version": "1.0.0", "enabled": True},
                {
                    "pluginId": "agent-toolkit@ak110-dotfiles",
                    "name": "agent-toolkit",
                    "marketplaceName": "ak110-dotfiles",
                    **state,
                },
            ]
        }

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(cache)
            current_hook = cache / "1.2.3/agent_toolkit/hook.py"
            current_hook.parent.mkdir(parents=True)
            current_hook.write_text("# current hook\n", encoding="utf-8")
            state.update(version="1.2.3", enabled=True)
        return args != ["app-server", "daemon", "version"]

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(update_dotfiles, "_DOTFILES_ROOT", root)
    monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "state/update.lock")
    monkeypatch.setattr(update_dotfiles, "_LOG_PATH", tmp_path / "state/update-dotfiles.log")
    monkeypatch.setattr(update_dotfiles.sync_report, "REPORT_PATH", tmp_path / "state/sync-report.json")
    monkeypatch.setattr(update_dotfiles, "_update_git_with_recovery", lambda *_args, **_kwargs: 0)

    def no_daemon_pause() -> None:
        return None

    monkeypatch.setattr(update_dotfiles, "_pause_codex_daemon", no_daemon_pause)

    def run_step(_step: int, _total: int, _title: str, command_line: list[str], **_kwargs: object) -> tuple[int, str]:
        if command_line == ["chezmoi", "apply", "--force"]:
            results, _ = post_apply.run(
                [
                    (
                        "Codex plugin のインストール",
                        install_codex_plugins.run,
                    )
                ]
            )
            return (0 if all(result.ok for result in results) else 1), ""
        return 0, ""

    monkeypatch.setattr(update_dotfiles, "_run_step", run_step)

    assert update_dotfiles.main() == 0
    for version in ("1.2.1", "1.2.2"):
        assert (cache / version / "version-marker").read_text(encoding="utf-8") == version
    assert (cache / "1.2.3/agent_toolkit/hook.py").is_file()
    assert (home / ".local/bin/atk-hook").is_file()
