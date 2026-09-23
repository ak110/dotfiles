"""install_codex_pluginsのテスト。"""

import json
import logging
import runpy
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pytools._internal import claude_common, install_codex_plugins

from ._test_helpers import _FakeResult

_TOOLKIT_PREFIX = "agent-" + "toolkit"
_EXPECTED_HOOK_EVENTS = {
    "sessionStart",
    "subagentStart",
    "preToolUse",
    "postToolUse",
    "permissionRequest",
    "userPromptSubmit",
    "subagentStop",
    "sessionEnd",
}


@pytest.fixture(autouse=True)
def _empty_unused_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """ローカルpluginのテストでは不要pluginの除去を無効にする。"""
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ())
    monkeypatch.delenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", raising=False)
    monkeypatch.setattr(
        install_codex_plugins,
        "_hooks_list",
        lambda: {
            "data": [
                {
                    "hooks": [
                        {"eventName": event, "enabled": True, "trustStatus": "untrusted"} for event in _EXPECTED_HOOK_EVENTS
                    ],
                    "warnings": [],
                    "errors": [],
                }
            ]
        },
    )


@pytest.fixture(name="plugin_env")
def plugin_env_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Codexの設定とdotfilesを一時ディレクトリへ分離する。"""
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
    (root / "bin").mkdir()
    (root / "bin/atk-hook").write_text("hook wrapper", encoding="utf-8")
    (root / "bin/atk-hook.cmd").write_text("hook wrapper cmd", encoding="utf-8")
    current_hook = tmp_path / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3/agent_toolkit/hook.py"
    current_hook.parent.mkdir(parents=True)
    current_hook.write_text("hook", encoding="utf-8")
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(install_codex_plugins.claude_common, "resolve_executable", lambda _name: Path("codex"))
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


def test_registers_and_installs_with_official_cli(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未導入pluginは公式CLIへ導入を委譲し、legacy linkを除去する。"""
    destination = _legacy_link(plugin_env)
    cache_entry = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    cache_entry.mkdir(parents=True)
    (cache_entry / "marker").write_text("keep", encoding="utf-8")
    calls: list[list[str]] = []
    _set_json_responses(
        monkeypatch,
        [
            {"marketplaces": []},
            {"installed": []},
            _installed_state(),
        ],
    )
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks", "codex app-server daemon restart"]
    assert ["plugin", "marketplace", "add", str(plugin_env)] in calls
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert not destination.exists()
    assert (cache_entry / "marker").read_text(encoding="utf-8") == "keep"


def test_first_hook_transition_preserves_previous_cache(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """初回切替では入口を先に置き、CLIが除去した旧hook実体を復元する。"""
    wrapper = install_codex_plugins._hook_bin() / "atk-hook"  # pylint: disable=protected-access
    wrapper.unlink()
    previous = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    previous.mkdir(parents=True)
    (previous / "hook.py").write_text("previous", encoding="utf-8")
    _set_json_responses(
        monkeypatch,
        [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()],
    )

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            assert wrapper.read_text(encoding="utf-8") == "hook wrapper"
            (previous / "hook.py").unlink()
            previous.rmdir()
        return True

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert (previous / "hook.py").read_text(encoding="utf-8") == "previous"


def test_hook_wrapper_forwards_event_and_exit(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """入口は現行版を解決してイベントと子hookの終了状態を渡す。"""
    del plugin_env
    wrapper = Path(__file__).resolve().parents[2] / "bin/atk-hook"
    calls: list[list[str]] = []
    monkeypatch.setenv("CODEX_HOME", str(install_codex_plugins.CODEX_HOME))
    monkeypatch.setattr(sys, "argv", [str(wrapper), "pretooluse"])

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "installed": [
                        {
                            "pluginId": "agent-toolkit@ak110-dotfiles",
                            "name": "agent-toolkit",
                            "marketplaceName": "ak110-dotfiles",
                            "version": "1.2.3",
                            "enabled": True,
                        }
                    ]
                }
            ),
            "",
        )

    def call(args: list[str]) -> int:
        calls.append(args)
        return 7

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "call", call)

    with pytest.raises(SystemExit, match="7"):
        runpy.run_path(str(wrapper), run_name="__main__")

    assert calls[0] == ["codex", "plugin", "list", "--json"]
    assert calls[1][-1] == "pretooluse"
    assert calls[1][-2].endswith("/agent_toolkit/hook.py")


@pytest.mark.parametrize(
    ("before", "case"),
    [
        ({"installed": []}, "未導入"),
        (_installed_state(enabled=False), "無効"),
        (_installed_state(version="1.2.2"), "版数不一致"),
    ],
)
def test_reinstalls_when_state_requires_it(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: dict[str, Any],
    case: str,
) -> None:
    """導入状態が要件を満たさない場合は公式CLIを呼び出して検証する。"""
    del case
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), before, _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert calls.count(["plugin", "add", "agent-toolkit@ak110-dotfiles"]) == 1
    assert [notice.command for notice in outcome.notices] == ["/hooks", "codex app-server daemon restart"]


def test_plugin_update_keeps_hook_notice_when_daemon_is_stopped(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """daemon再起動が不要でもHook再信頼と新規SessionStartの検収を案内する。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert "SessionStart" in outcome.notices[0].message


def test_plugin_update_auto_restarts_running_daemon(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """明示設定時は更新検証後に稼働中daemonを1回だけ再起動する。"""
    calls: list[list[str]] = []
    restart_calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", "1")
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    def run_subprocess(args: list[str], **_kwargs: object) -> _FakeResult:
        restart_calls.append(args)
        return _FakeResult(returncode=0)

    monkeypatch.setattr(install_codex_plugins.claude_common, "run_subprocess", run_subprocess)

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert restart_calls == [["codex", "app-server", "daemon", "restart"]]
    assert calls[-1] == ["app-server", "daemon", "version"]


def test_plugin_update_auto_restart_failure_keeps_notice_and_logs_exit_code(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """自動再起動の失敗は終了コードを記録して手動案内へ戻す。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", "1")
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    monkeypatch.setattr(
        install_codex_plugins.claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: _FakeResult(returncode=9),
    )
    caplog.set_level(logging.WARNING, logger=install_codex_plugins.__name__)

    outcome = install_codex_plugins.run()

    assert [notice.command for notice in outcome.notices] == ["/hooks", "codex app-server daemon restart"]
    assert "自動再起動に失敗 (exit 9)" in caplog.text


def test_plugin_update_does_not_restart_stopped_daemon_when_enabled(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """明示設定時もdaemonが停止中なら再起動コマンドを発行しない。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", "1")
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))
    monkeypatch.setattr(
        install_codex_plugins.claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: pytest.fail("停止中daemonを再起動してはいけない"),
    )

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks"]


def test_plugin_update_omits_trust_notice_when_hooks_are_not_registered(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登録0件を信頼不足として案内しない。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))
    monkeypatch.setattr(
        install_codex_plugins,
        "_hooks_list",
        lambda: {"data": [{"hooks": [], "warnings": [], "errors": []}]},
    )

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert not outcome.notices


def test_same_version_enabled_is_unchanged(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版数一致かつ有効なpluginは導入せず変更しない。"""
    calls: list[list[str]] = []
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", "1")
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    monkeypatch.setattr(
        install_codex_plugins.claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: pytest.fail("無変更時にdaemonを再起動してはいけない"),
    )

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices
    assert not calls


@pytest.mark.parametrize(
    "plugin_state",
    [
        None,
        {},
        {"installed": None},
        {"installed": "x"},
        {"installed": [{"version": "1.2.2", "enabled": True}]},
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": 122, "enabled": True}]},
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": "true"}]},
    ],
)
def test_invalid_before_state_stops_before_plugin_add(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    plugin_state: dict[str, Any] | None,
) -> None:
    """更新前状態を取得できない場合はpluginを変更しない。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), plugin_state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls


def test_plugin_add_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}])

    def command(args: list[str]) -> bool:
        calls.append(args)
        return False

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    with pytest.raises(RuntimeError, match="plugin addに失敗"):
        install_codex_plugins.run()

    assert destination.is_symlink()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_post_install_verification_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入後の状態検証に失敗した場合はlegacy linkを除去しない。"""
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}, None])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    with pytest.raises(RuntimeError, match="更新後の状態が期待値と一致しない"):
        install_codex_plugins.run()

    assert destination.is_symlink()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_legacy_removal_failure_does_not_restore_links(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入済みpluginのlegacy link除去失敗時も削除済みlinkを復元しない。"""
    destination = _legacy_link(plugin_env)
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))
    original_remove = install_codex_plugins._remove_legacy_links  # pylint: disable=protected-access

    def remove_then_fail(root: Path) -> bool:
        original_remove(root)
        raise OSError("injected legacy removal failure")

    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", remove_then_fail)

    with pytest.raises(OSError, match="injected legacy removal failure"):
        install_codex_plugins.run()

    assert not destination.exists()


def test_removes_broken_legacy_link_and_keeps_unrelated_entries(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _broken_legacy_link(plugin_env)
    unrelated_source = plugin_env / "unrelated"
    unrelated_source.mkdir()
    unrelated = install_codex_plugins.CODEX_HOME / "skills/unrelated"
    unrelated.symlink_to(unrelated_source, target_is_directory=True)
    regular = install_codex_plugins.CODEX_HOME / "skills/regular"
    regular.mkdir(parents=True)
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert not expected.exists()
    assert unrelated.is_symlink()
    assert regular.is_dir()


def test_codex_home_environment_controls_legacy_cleanup(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CODEX_HOME指定時は指定先のlegacy linkを除去する。"""
    codex_home = plugin_env.parent / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    source = plugin_env / f"{_TOOLKIT_PREFIX}/skills/coding"
    source.mkdir(parents=True)
    destination = codex_home / "skills/coding"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(source, target_is_directory=True)
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert not destination.exists()


def test_rejects_mismatched_marketplace_root(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        install_codex_plugins,
        "_codex_json",
        lambda _: {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env / "other")}]},
    )

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices


def test_removes_installed_unused_plugin_and_returns_notice(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入済みの不要pluginを公式CLIで除去し、daemon再起動を案内する。"""
    unused_state = {
        "installed": [
            {"pluginId": "compact-plus@compact-plus", "version": "1.3.2", "enabled": True},
            *cast("list[dict[str, object]]", _installed_state()["installed"]),
        ]
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    _set_json_responses(monkeypatch, [unused_state, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert ["plugin", "remove", "compact-plus@compact-plus"] in calls
    assert [notice.command for notice in outcome.notices] == ["codex app-server daemon restart"]


def test_skips_uninstalled_unused_plugin(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未導入の不要pluginは除去せず、ローカルpluginも変更しない。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    _set_json_responses(monkeypatch, [{"installed": []}, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices
    assert ["plugin", "remove", "compact-plus@compact-plus"] not in calls


def test_skips_unused_plugin_when_list_fails(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不要pluginの一覧取得失敗時は除去せず、ローカルplugin処理を継続する。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    _set_json_responses(monkeypatch, [None, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices
    assert ["plugin", "remove", "compact-plus@compact-plus"] not in calls


def test_continues_when_unused_plugin_remove_fails(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """不要pluginの除去失敗時もローカルplugin処理を継続する。"""
    unused_state = {
        "installed": [
            {"pluginId": "compact-plus@compact-plus", "version": "1.3.2", "enabled": True},
            *cast("list[dict[str, object]]", _installed_state()["installed"]),
        ]
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    _set_json_responses(monkeypatch, [unused_state, _local_marketplace(plugin_env), unused_state])

    def command(args: list[str]) -> bool:
        calls.append(args)
        return args != ["plugin", "remove", "compact-plus@compact-plus"]

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices
    assert ["plugin", "remove", "compact-plus@compact-plus"] in calls
    assert "plugin除去に失敗したため続行" in caplog.text


def test_windows_junction_detection_and_removal_use_rmdir() -> None:
    """Windows junction相当ではis_junction判定後にrmdirを使う。"""

    class JunctionPath:
        def __init__(self) -> None:
            self.removed = False

        def is_symlink(self) -> bool:
            return False

        def is_junction(self) -> bool:
            return True

        def rmdir(self) -> None:
            self.removed = True

    junction = JunctionPath()
    path = cast("Path", junction)

    assert install_codex_plugins._is_link(path) is True  # pylint: disable=protected-access
    install_codex_plugins._unlink(path)  # pylint: disable=protected-access
    assert junction.removed is True
