# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""install_codex_pluginsのdaemonの試験。"""

# pylint: disable=protected-access

from pathlib import Path
from typing import Any

import pytest

from pytools._internal import (
    install_codex_plugins,
)
from pytools._internal.install_codex_plugins_test_helpers import _empty_unused_plugins as _empty_unused_plugins
from pytools._internal.install_codex_plugins_test_helpers import (
    _hook_entry,
    _installed_state,
    _local_marketplace,
    _recording_success,
    _set_json_responses,
)
from pytools._internal.install_codex_plugins_test_helpers import plugin_env_fixture as plugin_env_fixture


def test_plugin_update_keeps_hook_notice_when_daemon_is_stopped(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """daemon再起動が不要でもHook再信頼と新規SessionStartの検収を案内する。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert "SessionStart" in outcome.notices[0].message


def test_plugin_update_leaves_running_daemon_and_reports_restart(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """共有daemonが稼働中なら、更新は完了して明示的な再起動を案内する。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    monkeypatch.setattr(
        install_codex_plugins.common,
        "run_subprocess",
        lambda *_args, **_kwargs: pytest.fail("稼働daemonを自動再起動してはいけない"),
    )

    outcome = install_codex_plugins.run()

    assert outcome.changed and outcome.failure is None
    assert [notice.command for notice in outcome.notices] == ["/hooks", "codex app-server daemon restart"]
    assert calls[-1] == ["app-server", "daemon", "version"]


def test_plugin_update_does_not_restart_stopped_daemon(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """daemonが停止中なら再起動コマンドを発行しない。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))
    monkeypatch.setattr(
        install_codex_plugins.common,
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


@pytest.mark.parametrize(
    ("hooks", "expected"),
    [
        pytest.param([_hook_entry()], True, id="untrusted"),
        pytest.param([_hook_entry(trustStatus="modified")], True, id="modified"),
        pytest.param([_hook_entry(trustStatus="trusted"), _hook_entry(trustStatus="modified")], True, id="trusted-modified"),
        pytest.param([_hook_entry(trustStatus="trusted"), _hook_entry()], True, id="trusted-untrusted"),
        pytest.param([_hook_entry(eventName="interrupt")], True, id="additional-event"),
        pytest.param([_hook_entry(trustStatus="trusted")], False, id="trusted"),
        pytest.param([_hook_entry(trustStatus="managed", isManaged=True)], False, id="managed"),
        pytest.param([_hook_entry(enabled=False)], False, id="disabled"),
        pytest.param([_hook_entry(pluginId="other@market")], False, id="other-plugin"),
        pytest.param([_hook_entry(source="user", pluginId=None)], False, id="user-hook"),
        pytest.param([_hook_entry(trustStatus=[])], False, id="invalid-status"),
        pytest.param([_hook_entry(pluginId="other@market"), _hook_entry(trustStatus="modified")], True, id="other-with-target"),
        pytest.param(
            [_hook_entry(trustStatus="trusted"), _hook_entry(trustStatus="modified", enabled=False)],
            False,
            id="trusted-disabled-modified",
        ),
    ],
)
def test_plugin_hook_trust_notice_filters_registered_hooks(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, hooks: list[dict[str, Any]], expected: bool
) -> None:
    """対象pluginの有効な未信頼・変更済みhookが1件でもあれば正規承認を案内する。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))
    monkeypatch.setattr(
        install_codex_plugins,
        "_hooks_list",
        lambda: {"data": [{"cwd": str(plugin_env), "hooks": hooks, "warnings": [], "errors": []}]},
    )

    outcome = install_codex_plugins.run()

    assert [notice.command for notice in outcome.notices] == (["/hooks"] if expected else [])
    assert outcome.changed


@pytest.mark.parametrize("version", [None, "1.2.2", "1.2.3"], ids=["initial", "update", "same-version"])
def test_hook_trust_notice_for_each_install_state(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, version: str | None
) -> None:
    """初回・更新・同版起動で、承認済みと変更済みの混在を同じ案内へ接続する。"""
    before = {"installed": []} if version is None else _installed_state(version=version)
    responses: list[dict[str, Any] | None] = [_local_marketplace(plugin_env), before]
    if version != "1.2.3":
        responses.append(_installed_state())
    _set_json_responses(monkeypatch, responses)
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))
    monkeypatch.setattr(
        install_codex_plugins,
        "_hooks_list",
        lambda: {
            "data": [
                {
                    "cwd": str(plugin_env),
                    "hooks": [_hook_entry(trustStatus="trusted"), _hook_entry(trustStatus="modified")],
                    "warnings": [],
                    "errors": [],
                }
            ]
        },
    )

    outcome = install_codex_plugins.run()

    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert outcome.changed is (version != "1.2.3")
    assert calls == (
        [["plugin", "add", "agent-toolkit@ak110-dotfiles"], ["app-server", "daemon", "version"]] if version != "1.2.3" else []
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param(None, False, id="unavailable"),
        pytest.param({"data": []}, False, id="empty"),
        pytest.param({"data": {}}, False, id="invalid-data"),
        pytest.param({"data": [None, {"hooks": None}]}, False, id="invalid-entries"),
        pytest.param(
            {
                "data": [
                    {"cwd": "/first", "hooks": [_hook_entry(trustStatus="trusted")], "warnings": [], "errors": []},
                    {
                        "cwd": "/second",
                        "hooks": [None, _hook_entry(trustStatus="modified")],
                        "warnings": ["別hookの警告"],
                        "errors": ["別hookの登録エラー"],
                    },
                ]
            },
            True,
            id="multiple-cwds-with-diagnostics",
        ),
    ],
)
def test_hook_trust_notice_handles_response_entries(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, result: dict[str, Any] | None, expected: bool
) -> None:
    """取得不能を未承認扱いせず、登録診断と同居する対象の案内は維持する。"""
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_hooks_list", lambda: result)

    outcome = install_codex_plugins.run()

    assert [notice.command for notice in outcome.notices] == (["/hooks"] if expected else [])
    assert outcome.changed is False


__all__ = ["plugin_env_fixture"]
