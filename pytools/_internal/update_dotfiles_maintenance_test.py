"""更新工程の内容一致による省略と、Windowsの報告出力に対する診断を検証する。"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import (
    claude_common,
    claude_marketplace,
    claude_plugin_references,
    common,
    install_claude_plugins,
    install_codex_plugins,
    plugin_warmup,
    post_apply_cache,
    setup_mise,
    warmup_agents_server,
)


@pytest.fixture(name="mise")
def _mise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], dict[str, Any], Path]:
    binary = tmp_path / "mise"
    binary.write_bytes(b"mise")
    config = tmp_path / "mise.toml"
    config.write_text('[tools]\nnode = "24"\n', encoding="utf-8")
    data = tmp_path / "data"
    (data / "shims").mkdir(parents=True)
    monkeypatch.setenv("MISE_DATA_DIR", str(data))
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: binary)
    monkeypatch.setattr(setup_mise, "_is_windows", lambda: False)
    calls: list[list[str]] = []
    responses: dict[str, Any] = {}

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        args = command[1:]
        output = ""
        if args[:2] == ["config", "ls"]:
            output = json.dumps([{"path": str(config), "tools": ["node"], "override": False}])
        elif args[:1] == ["ls"]:
            output = json.dumps({"node": [{"version": responses.get("installed", "24"), "installed": True}]})
        elif args == ["--version"]:
            output = "2026.10.19 windows-x64 (2026-10-24)"
        elif command[:2] == ["winget", "show"]:
            # show --versionsの版一覧。数値行だけを解析し、ローカライズされた見出しに依存しない。
            output = "見つかりました mise-en-place [jdx.mise]\nバージョン\n----------\n" + responses.get(
                "candidate", "2026.10.12"
            )
        result = responses.get(" ".join(args))
        return (
            result if isinstance(result, subprocess.CompletedProcess) else subprocess.CompletedProcess(command, 0, output, "")
        )

    monkeypatch.setattr(common, "run_subprocess", run)
    return calls, responses, config


@pytest.mark.parametrize("candidate", ["2026.10.12", "2026.10.19"])
def test_winget_skips_equal_or_older_candidate(mise, monkeypatch: pytest.MonkeyPatch, candidate: str) -> None:
    calls, responses, _config = mise
    responses["candidate"] = candidate
    monkeypatch.setattr(setup_mise, "_is_windows", lambda: True)
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: Path(r"C:\Microsoft\WinGet\Links\mise.exe"))
    setup_mise.run()
    setup_mise.run()
    assert not [call for call in calls if call[:2] == ["winget", "upgrade"]]
    assert len([call for call in calls if call[:2] == ["winget", "show"]]) == 1


def test_winget_access_denied_identifies_holding_processes(mise, monkeypatch, caplog) -> None:
    calls, responses, _config = mise
    responses["candidate"] = "2026.11.1"
    # Windowsでの報告出力から削除時の診断を抜粋し、パスを短縮した。
    responses[
        "upgrade --id jdx.mise --exact --accept-package-agreements --accept-source-agreements --disable-interactivity"
    ] = subprocess.CompletedProcess([], 0x8A150003, 'remove_all: Access is denied.: "C:\\mise"', "")
    monkeypatch.setattr(setup_mise, "_is_windows", lambda: True)
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: Path(r"C:\Microsoft\WinGet\Links\mise.exe"))
    setup_mise.run()
    assert any(call[:2] == ["winget", "upgrade"] for call in calls)
    assert "ターミナルやエディターを終了" in caplog.text
    assert "remove_all: Access is denied" in caplog.text


def test_install_privilege_failure_preserves_error_and_guides_developer_mode(mise, caplog) -> None:
    _calls, responses, _config = mise
    responses["install"] = subprocess.CompletedProcess(
        [],
        1,
        "",
        "mise ERROR failed to extract google-cloud-sdk-590.0.1-windows-x86_64-bundled-python.zip: "
        "クライアントは要求された特権を保有していません。 (os error 1314)",
    )
    setup_mise.run()
    assert "開発者モード" in caplog.text
    assert "os error 1314" in caplog.text
    assert "ネットワーク到達" not in caplog.text
    # 失敗を成功として記録すると、次回の再試行が省かれる。
    assert not post_apply_cache.record_path("mise-tools").exists()


@pytest.mark.parametrize("change", ["config", "lock", "installed", "shim"])
def test_mise_reuses_only_unchanged_success(mise, change: str) -> None:
    calls, responses, config = mise
    setup_mise.run()
    calls.clear()
    setup_mise.run()
    assert not [call for call in calls if call[1] in {"install", "prune", "reshim"}]
    if change == "config":
        original = config.stat()
        config.write_text('[tools]\nnode = "25"\n', encoding="utf-8")
        os.utime(config, ns=(original.st_atime_ns, original.st_mtime_ns))
    elif change == "lock":
        (config.parent / "mise.lock").write_text("version=1", encoding="utf-8")
    elif change == "installed":
        responses["installed"] = "25"
    else:
        (Path(os.environ["MISE_DATA_DIR"]) / "shims" / "orphan").write_bytes(b"orphan")
    calls.clear()
    setup_mise.run()
    assert [call for call in calls if call[1] == "install"]
    assert [call for call in calls if call[1] == "prune"]
    assert [call for call in calls if call[1] == "reshim"]


def test_claude_content_match_skips_install_but_reflects_source_edits(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    source = root / "sample"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"name":"sample","version":"1"}', encoding="utf-8")
    (source / "skill.md").write_text("原本", encoding="utf-8")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "sample", "version": "1", "source": "./sample"}]}), encoding="utf-8"
    )
    installed = tmp_path / "plugins/installed_plugins.json"
    cache = installed.parent / "cache/ak110-dotfiles/sample/1"
    shutil.copytree(source, cache)
    installed.write_text(
        json.dumps({"plugins": {"sample@ak110-dotfiles": [{"scope": "user", "version": "1", "installPath": str(cache)}]}}),
        encoding="utf-8",
    )
    settings = tmp_path / "settings.json"
    settings.write_text('{"enabledPlugins":{"sample@ak110-dotfiles":true}}', encoding="utf-8")
    monkeypatch.setattr(claude_common, "INSTALLED_PLUGINS_PATH", installed)
    monkeypatch.setattr(claude_common, "SETTINGS_JSON_PATH", settings)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_executable", lambda *args, **kwargs: Path("cli"))
    monkeypatch.setattr(claude_marketplace, "ensure_marketplace", lambda: True)
    monkeypatch.setattr(claude_marketplace, "is_directory_type_registered", lambda: True)
    monkeypatch.setattr(claude_plugin_references, "scan", lambda: [])
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ["plugin", "install"]:
            shutil.copytree(source, cache, dirs_exist_ok=True)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(claude_common, "run_claude", run)
    assert not install_claude_plugins.run().changed
    assert not [call for call in calls if call[:2] == ["plugin", "install"]]
    (cache / ".venv").mkdir()
    assert install_claude_plugins.run().changed
    assert not (cache / ".venv").exists()
    assert not [call for call in calls if call[:2] == ["plugin", "install"]]
    (source / "skill.md").write_text("編集", encoding="utf-8")
    assert install_claude_plugins.run().changed
    assert (cache / "skill.md").read_text(encoding="utf-8") == "編集"
    (cache / "skill.md").unlink()
    assert install_claude_plugins.run().changed
    assert (cache / "skill.md").is_file()


def test_warmup_rechecks_code_and_dependencies(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "server.py"
    script.write_text("# MCP", encoding="utf-8")
    commands = [["python", str(script)]]
    monkeypatch.setattr(plugin_warmup, "prepare_targets", lambda *args, **kwargs: commands)
    startup: list[list[str]] = []
    dependency_ok = True

    async def dependency(command):
        return plugin_warmup.CommandOutcome(command, subprocess.CompletedProcess(command, 0 if dependency_ok else 1, "", ""), 0)

    async def check(command):
        startup.append(command)
        # MCP接続のテストダブルも、本番が受け取る起動結果の型で返す。
        return warmup_agents_server._StartupOutcome(True, "ok")  # noqa: SLF001  # pylint: disable=protected-access

    monkeypatch.setattr(plugin_warmup, "check_command", dependency)
    monkeypatch.setattr(warmup_agents_server, "_check_startup", check)
    assert warmup_agents_server.run().failure is None
    monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "next-update")
    assert warmup_agents_server.run().failure is None
    assert len(startup) == 1
    script.write_text("# changed", encoding="utf-8")
    assert warmup_agents_server.run().failure is None
    assert len(startup) == 2
    dependency_ok = False
    post_apply_cache.record_path("agents-server-startup").unlink()
    assert warmup_agents_server.run().failure is None
    assert not post_apply_cache.record_path("agents-server-startup").exists()


def test_untrusted_codex_hook_explains_user_operations(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    manifest = root / ".agents/plugins/marketplace.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "name": "ak110-dotfiles",
                "plugins": [
                    {"name": name, "source": {"source": "local", "path": f"./{name}"}}
                    for name in ("agent-basics", "agent-toolkit")
                ],
            }
        ),
        encoding="utf-8",
    )
    for name in ("agent-basics", "agent-toolkit"):
        plugin = root / name / ".codex-plugin/plugin.json"
        plugin.parent.mkdir(parents=True)
        plugin.write_text(json.dumps({"name": name, "version": "1"}), encoding="utf-8")
    wrapper = root / "agent-toolkit/scripts/atk-hook"
    wrapper.parent.mkdir()
    wrapper.write_bytes(b"# wrapper")
    (wrapper.parent / "atk-hook.cmd").write_bytes(b"rem wrapper")
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_executable", lambda *args, **kwargs: Path("codex"))

    def codex(args):
        if args[:3] == ["plugin", "marketplace", "list"]:
            return {"marketplaces": [{"name": "ak110-dotfiles", "root": str(root)}]}
        return {
            "installed": [
                {"pluginId": f"{name}@ak110-dotfiles", "version": "1", "enabled": True}
                for name in ("agent-basics", "agent-toolkit")
            ]
        }

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex)
    monkeypatch.setattr(
        install_codex_plugins,
        "_hooks_list",
        lambda: {
            "data": [
                {
                    "hooks": [
                        {
                            "pluginId": "agent-toolkit@ak110-dotfiles",
                            "source": "plugin",
                            "enabled": True,
                            "isManaged": False,
                            "trustStatus": "modified",
                        }
                    ]
                }
            ]
        },
    )
    notice = next(notice for notice in install_codex_plugins.run().notices if notice.command == "/hooks")
    assert "Codexの入力欄" in notice.message
    assert "新しいセッション" in notice.message
    assert "エージェント" in notice.message
    assert "稼働中のdaemonはplugin更新時に自動で再起動" in notice.message
