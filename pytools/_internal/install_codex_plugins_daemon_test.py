# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""install_codex_pluginsのdaemonの試験。"""

# pylint: disable=protected-access

import logging
from pathlib import Path
from typing import Any

import pytest

from pytools import post_apply
from pytools._internal import (
    codex_processes,
    install_codex_plugins,
)
from pytools._internal.install_codex_plugins_test_helpers import (
    _SESSION,
    _hook_entry,
    _installed_state,
    _local_marketplace,
    _recording_success,
    _set_json_responses,
)
from pytools._internal.install_codex_plugins_test_helpers import _empty_unused_plugins as _empty_unused_plugins
from pytools._internal.install_codex_plugins_test_helpers import plugin_env_fixture as plugin_env_fixture

from ._test_helpers import _FakeResult


def test_plugin_update_keeps_hook_notice_when_daemon_is_stopped(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """daemon再起動が不要でもHook再信頼と新規SessionStartの検収を案内する。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert "SessionStart" in outcome.notices[0].message


@pytest.mark.parametrize("session_starts", [False, True])
def test_plugin_update_rechecks_sessions_before_auto_restart(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, session_starts: bool
) -> None:
    """稼働daemonは利用セッションの有無によらずpluginを反映する。"""
    calls: list[list[str]] = []
    restart_calls: list[list[str]] = []
    restart_timeouts: list[float] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    daemon = codex_processes.CodexProcess(4321, "codex app-server --managed-daemon", "managed-daemon")
    processes = [daemon]
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: tuple(processes))
    success = _recording_success(calls)

    def command(args: list[str], **_kwargs: Any) -> bool:
        if session_starts and args == ["app-server", "daemon", "version"]:
            processes.append(_SESSION)
        return success(args)

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    def run_subprocess(args: list[str], *, timeout: float, **_kwargs: object) -> _FakeResult:
        restart_calls.append(args)
        restart_timeouts.append(timeout)
        return _FakeResult(returncode=0)

    monkeypatch.setattr(install_codex_plugins.common, "run_subprocess", run_subprocess)

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert restart_calls == [["codex", "app-server", "daemon", "restart"]]
    assert restart_timeouts == [750.0]
    assert calls[-1] == ["app-server", "daemon", "version"]


def test_plugin_update_auto_restart_failure_keeps_notice_and_logs_exit_code(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """自動再起動の失敗は診断と次の操作を持つ工程失敗へ伝播する。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    monkeypatch.setattr(
        install_codex_plugins.common,
        "run_subprocess",
        lambda *_args, **_kwargs: _FakeResult(returncode=9, stderr="daemon restart detail"),
    )
    caplog.set_level(logging.WARNING, logger=install_codex_plugins.__name__)

    outcome = install_codex_plugins.run()

    assert [notice.command for notice in outcome.notices] == ["/hooks", "codex app-server daemon restart"]
    assert "自動再起動に失敗 (exit 9" in caplog.text
    assert "daemon restart detail" in caplog.text
    assert outcome.failure is not None and "daemon restart detail" in outcome.failure
    assert outcome.next_action is not None and "codex app-server daemon restart" in outcome.next_action
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", plugin_env.parent / "update-dotfiles.log")
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", plugin_env.parent / "sync-report.json")
    with pytest.raises(SystemExit) as failure:
        post_apply.main(runner=lambda: post_apply.run([("Codex plugins", lambda: outcome)]))
    assert failure.value.code == 1


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
