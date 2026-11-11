"""install_codex_plugins試験の共通fixture・入力と補助関数。"""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import (
    codex_processes,
    common,
    host_roles,
    install_codex_plugins,
)

from ._test_helpers import _FakeResult

_REAL_CODEX_PROCESSES = codex_processes.codex_processes

_SESSION = codex_processes.CodexProcess(pid=4242, label="codex app-server", role="session")

_REAL_HOOKS_LIST = install_codex_plugins._hooks_list  # pylint: disable=protected-access

_TOOLKIT_PREFIX = "agent-" + "toolkit"


def _hook_entry(**overrides: Any) -> dict[str, Any]:
    """Codex 0.166.0のHooksListResponse・HookMetadataの公開schemaに沿う登録を返す。"""
    # 出所: codex app-server generate-json-schemaのv2/HooksListResponse.json。
    hook: dict[str, Any] = {
        "handlerType": "command",
        "command": "atk-hook pretooluse",
        "currentHash": "declaration/content",
        "displayOrder": 0,
        "enabled": True,
        "eventName": "preToolUse",
        "isManaged": False,
        "key": "plugin-hook",
        "pluginId": "agent-toolkit@ak110-dotfiles",
        "source": "plugin",
        "sourcePath": str(Path.cwd() / "hooks/hooks.codex.json"),
        "timeoutSec": 30,
        "trustStatus": "untrusted",
    }
    hook.update(overrides)
    return hook


@pytest.fixture(autouse=True)
def _empty_unused_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """ローカルpluginのテストでは不要pluginの除去を無効にする。"""
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ())
    # 既存のtoolkit固有シナリオは単一対象へ限定し、2件の導入契約は結合シナリオで検証する。
    monkeypatch.setattr(install_codex_plugins, "_MANAGED_PLUGINS", ("agent-toolkit",))
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: False)
    # 実daemonへの作用を防ぎ、更新後のrestartだけを成功する外部境界へ差し替える。
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))
    real_run = common.run_subprocess

    def safe_run(args: list[str], **kwargs: Any) -> Any:
        if args[1:] == ["app-server", "daemon", "restart"]:
            return _FakeResult(returncode=0)
        return real_run(args, **kwargs)

    monkeypatch.setattr(common, "run_subprocess", safe_run)
    monkeypatch.setattr(
        install_codex_plugins,
        "_hooks_list",
        lambda: {
            "data": [
                {
                    "cwd": str(Path.cwd()),
                    "hooks": [_hook_entry()],
                    "warnings": [],
                    "errors": [],
                }
            ]
        },
    )


@pytest.fixture(name="plugin_env")
def plugin_env_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Codexの設定とdotfilesを一時ディレクトリへ分離する。"""
    monkeypatch.setattr(install_codex_plugins, "_MANAGED_PLUGINS", ("agent-toolkit",))
    root = tmp_path / "dotfiles"
    (root / ".agents/plugins").mkdir(parents=True)
    (root / "agent-toolkit-codex/.codex-plugin").mkdir(parents=True)
    (root / ".agents/plugins/marketplace.json").write_text(
        json.dumps(
            {
                "name": "ak110-dotfiles",
                "plugins": [
                    {
                        "name": "agent-toolkit",
                        "source": {"source": "local", "path": "./agent-toolkit-codex"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "agent-toolkit-codex/.codex-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-toolkit", "version": "1.2.3", "hooks": "./hooks/hooks.codex.json"}),
        encoding="utf-8",
    )
    (root / "agent-toolkit/agent_toolkit").mkdir(parents=True)
    (root / "agent-toolkit/agent_toolkit/hook.py").write_text("source", encoding="utf-8")
    (root / "agent-toolkit/skills").mkdir()
    (root / "agent-toolkit/plugin-note.txt").write_text("source-file", encoding="utf-8")
    (root / "agent-toolkit/scripts").mkdir()
    (root / "agent-toolkit/scripts/atk-hook").write_text("hook wrapper", encoding="utf-8")
    (root / "agent-toolkit/scripts/atk-hook.cmd").write_text("hook wrapper cmd", encoding="utf-8")
    current_hook = tmp_path / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3/agent_toolkit/hook.py"
    current_hook.parent.mkdir(parents=True)
    current_hook.write_text("hook", encoding="utf-8")
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(install_codex_plugins.common, "resolve_executable", lambda _name: Path("codex"))
    monkeypatch.setattr(install_codex_plugins, "CODEX_HOME", tmp_path / ".codex")
    monkeypatch.setattr(install_codex_plugins, "_hook_bin", lambda: tmp_path / ".local/bin")
    hook_bin = tmp_path / ".local/bin"
    hook_bin.mkdir(parents=True)
    (hook_bin / "atk-hook").write_text("hook wrapper", encoding="utf-8")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return root


def _legacy_link(plugin_env: Path, name: str = "coding") -> Path:
    source = plugin_env / f"agent-toolkit/skills/{name}"
    source.mkdir(parents=True)
    destination = install_codex_plugins.CODEX_HOME / f"skills/{name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source, target_is_directory=True)
    return destination


def _broken_legacy_link(plugin_env: Path, name: str = "removed") -> Path:
    source = plugin_env / f"agent-toolkit/skills/{name}"
    destination = install_codex_plugins.CODEX_HOME / f"skills/{name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source, target_is_directory=True)
    return destination


def _installed_state(*, version: str = "1.2.3", enabled: bool = True) -> dict[str, object]:
    return {
        "installed": [
            {
                "pluginId": "agent-toolkit@ak110-dotfiles",
                "version": version,
                "enabled": enabled,
            }
        ]
    }


def _recording_success(calls: list[list[str]], *, daemon_running: bool = True) -> Callable[[list[str]], bool]:
    """引数を記録して成功を返すコマンドスタブを作成する。"""

    def command(args: list[str]) -> bool:
        calls.append(args)
        return daemon_running if args == ["app-server", "daemon", "version"] else True

    return command


def _local_marketplace(plugin_env: Path) -> dict[str, object]:
    return {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}


def _set_json_responses(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any] | None],
) -> None:
    iterator: Iterator[dict[str, Any] | None] = iter(responses)
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(iterator))


def _write_claude_plugin_root(root: Path, version: str) -> None:
    """安定した起動コマンドの復旧元になる最小のClaude Code plugin cacheを作成する。"""
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "agent-toolkit", "version": version}), encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts/launch.py").write_text("# launcher\n", encoding="utf-8")
    (root / "agent_toolkit").mkdir()
    (root / "agent_toolkit/hook.py").write_text("# hook\n", encoding="utf-8")
    (root / "marker").write_text(version, encoding="utf-8")
    (root / "skills").mkdir()


def _git_marketplace_config() -> Path:
    home = install_codex_plugins.CODEX_HOME
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        '[marketplaces.ak110-dotfiles]\nsource_type="git"\n'
        'source="https://github.com/ak110/dotfiles.git"\nref="master"\n'
        'sparse_paths=[".agents", "agent-toolkit-codex"]\n',
        encoding="utf-8",
    )
    cache = home / ".tmp/marketplaces/ak110-dotfiles"
    cache.mkdir(parents=True)
    return cache
