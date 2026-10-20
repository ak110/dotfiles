"""install_codex_pluginsのテスト。"""

import dataclasses
import json
import logging
import os
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pytools import post_apply
from pytools._internal import (
    codex_processes,
    common,
    deferred_updates,
    host_roles,
    install_codex_plugins,
    plugin_warmup,
    post_apply_outcome,
)

from ._test_helpers import ResidentProcesses, _FakeResult

_REAL_CODEX_PROCESSES = codex_processes.codex_processes
# plugin実体を参照する通常のCodexセッション（`agents_server`の`codex app-server --stdio`に当たる）。
_SESSION = codex_processes.CodexProcess(pid=4242, label="codex app-server", role="session")
_REAL_HOOKS_LIST = install_codex_plugins._hooks_list  # pylint: disable=protected-access

_TOOLKIT_PREFIX = "agent-" + "toolkit"
# 生成器の許可表と同じ9イベント。install_codex_pluginsはこの集合が全て登録済みかを判定する。
_EXPECTED_HOOK_EVENTS = install_codex_plugins._EXPECTED_HOOK_EVENTS  # pylint: disable=protected-access


@pytest.fixture(autouse=True)
def _empty_unused_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """ローカルpluginのテストでは不要pluginの除去を無効にする。"""
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ())
    monkeypatch.delenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", raising=False)
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: False)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
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


@pytest.mark.parametrize("respond", [True, False])
def test_hooks_list_closes_real_stdout_after_response_or_eof(monkeypatch: pytest.MonkeyPatch, respond: bool) -> None:
    """短命app-serverの実pipeから応答またはEOFを読み、stdout・stdinと子を回収する。"""
    child_code = """
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if not int(sys.argv[1]):
        break
    if message.get('method') == 'initialize':
        print(json.dumps({'id': message['id'], 'result': {}}), flush=True)
    elif message.get('method') == 'hooks/list':
        print(json.dumps({'id': message['id'], 'result': {'data': []}}), flush=True)
"""
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[str]] = []

    def popen(_argv: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        # 被検証関数がcloseと子の回収を担うことを観測する。
        # app-serverはpipeの要求を待つため、返却前にwaitするPopen.__exit__を使わない。
        process = real_popen(  # pylint: disable=consider-using-with
            [sys.executable, "-c", child_code, str(int(respond))], **kwargs
        )
        children.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert _REAL_HOOKS_LIST() == ({"data": []} if respond else None)
    assert len(children) == 1
    child = children[0]
    assert child.poll() == 0
    assert child.stdout is not None and child.stdout.closed
    assert child.stdin is not None and child.stdin.closed


def test_registers_and_installs_with_official_cli(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未導入pluginは公式CLIへ導入を委譲し、legacy linkを除去する。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))
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

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(cache_entry)
        return True

    monkeypatch.setattr(install_codex_plugins, "_command", command)

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


@pytest.mark.parametrize(
    ("old_version", "enabled", "first_transition"),
    [
        ("1.2.2", True, False),
        ("1.2.2", True, True),
        ("1.2.3", False, False),
    ],
)
def test_running_codex_updates_and_preserves_previous_caches(  # noqa: PLR0913 -- 更新前の条件を組で与える
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_version: str,
    enabled: bool,
    first_transition: bool,
) -> None:
    """Codexの稼働中も更新し、CLIが削除した全旧版を同じ絶対パスへ戻す。"""
    cache = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit"
    older = cache / "1.2.1"
    (older / "skills/older").mkdir(parents=True)
    (older / "skills/older/SKILL.md").write_text("older skill", encoding="utf-8")
    previous = cache / old_version
    (previous / "skills/coding").mkdir(parents=True)
    (previous / "skills/coding/SKILL.md").write_text("old skill", encoding="utf-8")
    (previous / "agent_toolkit").mkdir(exist_ok=True)
    (previous / "agent_toolkit/agents_server_mcp.py").write_text("old mcp", encoding="utf-8")
    if first_transition:
        (install_codex_plugins._hook_bin() / "atk-hook").unlink()  # pylint: disable=protected-access
    current: dict[str, Any] = {"version": old_version, "enabled": enabled}
    calls: list[list[str]] = []
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current)

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(cache)
            latest = cache / "1.2.3"
            (latest / "agent_toolkit").mkdir(parents=True, exist_ok=True)
            (latest / "agent_toolkit/hook.py").write_text("new hook", encoding="utf-8")
            (latest / "agent_toolkit/agents_server_mcp.py").write_text("new mcp", encoding="utf-8")
            (latest / "skills/coding").mkdir(parents=True, exist_ok=True)
            (latest / "skills/coding/SKILL.md").write_text("new skill", encoding="utf-8")
            current.update(version="1.2.3", enabled=True)
        return True

    def run_subprocess(args: list[str], **_kwargs: object) -> _FakeResult:
        assert args[1:] == ["plugin", "list", "--json"]
        return _FakeResult(stdout=json.dumps(_installed_state(**current)))

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(common, "run_subprocess", run_subprocess)

    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert outcome.deferred is False
    assert calls.count(["plugin", "add", "agent-toolkit@ak110-dotfiles"]) == 1
    assert current == {"version": "1.2.3", "enabled": True}
    assert (older / "skills/older/SKILL.md").read_text(encoding="utf-8") == "older skill"
    if old_version != "1.2.3":
        assert (previous / "skills/coding/SKILL.md").read_text(encoding="utf-8") == "old skill"
        assert (previous / "agent_toolkit/agents_server_mcp.py").read_text(encoding="utf-8") == "old mcp"
    latest = cache / "1.2.3"
    assert (latest / "skills/coding/SKILL.md").read_text(encoding="utf-8") == "new skill"
    assert (latest / "agent_toolkit/agents_server_mcp.py").read_text(encoding="utf-8") == "new mcp"
    assert (
        plugin_warmup.codex_plugin_script(
            plugin_id="agent-toolkit@ak110-dotfiles",
            plugin_name="agent-toolkit",
            relative_path=Path("agent_toolkit/agents_server_mcp.py"),
            tag="test",
        )
        == latest / "agent_toolkit/agents_server_mcp.py"
    )


@pytest.mark.skipif(sys.platform != "linux", reason="実行名をsymlinkで与える常駐プロセスはLinuxで起動する")
def test_running_codex_session_updates_without_removing_old_cache(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """実在するCodexセッションがあっても更新し、CLIが削除した旧cacheを復旧する。"""
    monkeypatch.setattr(codex_processes, "codex_processes", _REAL_CODEX_PROCESSES)
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", tmp_path / "state" / "sync-report.json")
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")
    cache = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit"
    previous = cache / "1.2.2"
    (previous / "agent_toolkit").mkdir(parents=True)
    (previous / "agent_toolkit/hook.py").write_text("old hook", encoding="utf-8")
    (previous / "skills/coding").mkdir(parents=True)
    (previous / "skills/coding/SKILL.md").write_text("old skill", encoding="utf-8")
    current: dict[str, Any] = {"version": "1.2.2", "enabled": True}
    calls: list[list[str]] = []

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current)

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(previous)
            (cache / "1.2.3/agent_toolkit").mkdir(parents=True, exist_ok=True)
            (cache / "1.2.3/agent_toolkit/hook.py").write_text("new hook", encoding="utf-8")
            current.update(version="1.2.3", enabled=True)
            return True
        return args != ["app-server", "daemon", "version"]

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    steps: list[tuple[str, Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
        ("Codex plugin のインストール", install_codex_plugins.run)
    ]
    dotfiles_root = common.find_dotfiles_root()
    assert dotfiles_root is not None
    record = deferred_updates.record_path(dotfiles_root)

    with ResidentProcesses(tmp_path, monkeypatch) as residents:
        residents.start("codex")
        with pytest.raises(SystemExit) as updated_exit:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert updated_exit.value.code == 0
        assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
        assert (previous / "agent_toolkit/hook.py").read_text(encoding="utf-8") == "old hook"
        assert (previous / "skills/coding/SKILL.md").read_text(encoding="utf-8") == "old skill"
        assert current == {"version": "1.2.3", "enabled": True}
        assert (cache / "1.2.3/agent_toolkit/hook.py").read_text(encoding="utf-8") == "new hook"
    assert not record.exists()


@pytest.mark.parametrize("auto_restart", [False, True], ids=["manual-daemon-restart", "auto-daemon-restart"])
def test_plugin_update_does_not_query_running_codex(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    auto_restart: bool,
) -> None:
    """旧cacheの復旧でセッションを守るため、更新可否をプロセス一覧へ依存させない。"""
    if auto_restart:
        monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", "1")
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: pytest.fail("更新時にプロセス一覧を参照しない"))
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))

    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert outcome.deferred is False


@pytest.mark.parametrize("enabled", [True, False])
def test_post_apply_update_continues_with_new_warmup_version(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """既定の工程順で更新後の有効版をwarmupへ渡す。"""
    monkeypatch.setattr(post_apply.sys, "platform", "linux")
    current: dict[str, Any] = {"version": "1.2.2", "enabled": enabled}
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))
    target_script = (
        install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3/agent_toolkit/agents_server_mcp.py"
    )

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current)

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            target_script.parent.mkdir(parents=True, exist_ok=True)
            target_script.write_text("new mcp", encoding="utf-8")
            current.update(version="1.2.3", enabled=True)
        return args != ["app-server", "daemon", "version"]

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(
        common, "run_subprocess", lambda *_args, **_kwargs: _FakeResult(stdout=json.dumps(_installed_state(**current)))
    )
    reached: list[str] = []
    warmup_targets: list[Path | None] = []

    def record_step(name: str) -> Callable[[], post_apply_outcome.PostApplyOutcome]:
        def run() -> post_apply_outcome.PostApplyOutcome:
            reached.append(name)
            return post_apply_outcome.PostApplyOutcome()

        return run

    def warmup() -> post_apply_outcome.PostApplyOutcome:
        warmup_targets.append(
            plugin_warmup.codex_plugin_script(
                plugin_id="agent-toolkit@ak110-dotfiles",
                plugin_name="agent-toolkit",
                relative_path=Path("agent_toolkit/agents_server_mcp.py"),
                tag="test",
            )
        )
        reached.append("warmup")
        return post_apply_outcome.PostApplyOutcome()

    def install() -> post_apply_outcome.PostApplyOutcome:
        assert "Codex CLI の導入と更新" in reached
        assert "Codex の Claude MCP 登録削除" in reached
        assert "warmup" not in reached
        return install_codex_plugins.run()

    steps = [
        dataclasses.replace(step, run=record_step(step.name), host_resources=False)
        for step in post_apply._DEFAULT_STEPS  # pylint: disable=protected-access
    ]
    for index, step in enumerate(steps):
        if step.name == "Codex plugin のインストール":
            steps[index] = dataclasses.replace(step, run=install)
        if step.name == "agents_serverのuv環境ウォームアップ":
            steps[index] = dataclasses.replace(step, run=warmup)
    results, _ = post_apply.run(steps)
    assert all(result.ok for result in results)
    codex_result = next(result for result in results if result.name == "Codex plugin のインストール")
    assert codex_result.changed is True
    assert codex_result.deferred is False
    assert warmup_targets == [target_script]
    assert "Codex plugin snapshot の生成" in reached
    assert "Claude Code plugin のインストール" in reached
    assert "旧Codex User scope MCP登録の移行" in reached
    assert "atk serve 自動起動セットアップ (Linux)" in reached


@pytest.mark.parametrize("placement", ["bundled", "installed-new", "installed-legacy"])
def test_hook_wrapper_forwards_event_and_exit(plugin_env: Path, monkeypatch: pytest.MonkeyPatch, placement: str) -> None:
    """配布内は自身の版、外側の旧呼出しは導入版を使い、イベントと終了状態を渡す。"""
    del plugin_env
    source = Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook"
    root = source.parent.parent
    wrapper = source
    if placement != "bundled":
        wrapper = install_codex_plugins._hook_bin() / "atk-hook"  # pylint: disable=protected-access
        shutil.copyfile(source, wrapper)
        root = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3"
        if placement == "installed-new":
            (root / "scripts").mkdir()
            (root / "scripts/launch.py").write_text("# launcher\n", encoding="utf-8")
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

    def execute(_executable: str, args: list[str]) -> None:
        calls.append(args)
        raise SystemExit(7)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "call", call)
    monkeypatch.setattr(os, "execv", execute)

    with pytest.raises(SystemExit, match="7"):
        runpy.run_path(str(wrapper), run_name="__main__")

    if placement == "bundled":
        assert len(calls) == 1
    else:
        assert calls[0] == ["codex", "plugin", "list", "--json"]
        assert len(calls) == 2
    if placement == "installed-legacy":
        assert calls[-1] == [
            "uv",
            "run",
            "--project",
            str(root),
            "--locked",
            "--no-default-groups",
            str(root / "agent_toolkit/hook.py"),
            "pretooluse",
        ]
    else:
        assert calls[-1] == [sys.executable, str(root / "scripts/launch.py"), "agent_toolkit/hook.py", "pretooluse"]


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

    monkeypatch.setattr(install_codex_plugins.common, "run_subprocess", run_subprocess)

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
        install_codex_plugins.common,
        "run_subprocess",
        lambda *_args, **_kwargs: _FakeResult(returncode=9, stderr="daemon restart detail"),
    )
    caplog.set_level(logging.WARNING, logger=install_codex_plugins.__name__)

    outcome = install_codex_plugins.run()

    assert [notice.command for notice in outcome.notices] == ["/hooks", "codex app-server daemon restart"]
    assert "自動再起動に失敗 (exit 9" in caplog.text
    assert "daemon restart detail" in caplog.text


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


def test_same_version_enabled_is_unchanged(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版数一致かつ有効なpluginは導入せず変更しない。"""
    calls: list[list[str]] = []
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setenv("DOTFILES_CODEX_DAEMON_AUTO_RESTART", "1")
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    monkeypatch.setattr(
        install_codex_plugins.common,
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


def test_plugin_add_failure_keeps_legacy_link(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """plugin addの失敗は旧cacheを復旧し、導入失敗として扱ってlegacy linkを除去しない。"""
    destination = _legacy_link(plugin_env)
    previous = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    previous.mkdir(parents=True)
    (previous / "marker").write_text("previous", encoding="utf-8")
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}])

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(previous.parent)
        return False

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    with caplog.at_level("WARNING"):
        outcome = install_codex_plugins.run()

    assert outcome.failure is None
    assert "plugin addに失敗" in caplog.text

    assert destination.is_symlink()
    assert (previous / "marker").read_text(encoding="utf-8") == "previous"
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_cache_restore_failure_is_reported(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """CLI失敗後に旧cacheも復旧できない場合は、両方の失敗を診断する。"""
    previous = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    previous.mkdir(parents=True)
    (previous / "marker").write_text("previous", encoding="utf-8")
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2")])

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(previous.parent)
            return False
        return True

    original_copytree = shutil.copytree

    def copytree(*args: Any, **kwargs: Any) -> Path:
        destination = Path(args[1])
        if destination.is_relative_to(previous.parent):
            raise OSError("injected restore failure")
        return original_copytree(*args, **kwargs)

    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(install_codex_plugins.shutil, "copytree", copytree)

    with caplog.at_level("WARNING"):
        outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert "plugin addと旧cacheの復旧に失敗" in caplog.text
    assert "injected restore failure" in caplog.text


def test_post_install_verification_failure_keeps_legacy_link(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """導入後の状態検証に失敗した場合はスキップと数え、legacy linkを除去しない。"""
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}, None])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    with caplog.at_level("WARNING"):
        outcome = install_codex_plugins.run()

    assert outcome.failure is None
    assert "更新後の状態が期待値と一致しない" in caplog.text

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


def test_running_codex_session_defers_unused_plugin_removal(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不要pluginも稼働中のCodexセッションが参照し得る間は除去せず、延期として案内する。

    除去は旧版の回収と同じくplugin実体を消すため、更新と同じ参照の判定（`session`の役割）に従う。
    """
    unused_state = {
        "installed": [
            {"pluginId": "compact-plus@compact-plus", "version": "1.3.2", "enabled": True},
            *cast("list[dict[str, object]]", _installed_state()["installed"]),
        ]
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    monkeypatch.setattr(
        codex_processes, "codex_processes", lambda: (codex_processes.CodexProcess(4321, "codex (pid 4321)", "session"),)
    )
    _set_json_responses(monkeypatch, [unused_state, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert ["plugin", "remove", "compact-plus@compact-plus"] not in calls
    assert outcome.deferred is True
    assert [notice for notice in outcome.notices if "compact-plus@compact-plus" in notice.message and "延期" in notice.message]


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


def test_running_codex_with_current_plugin_reports_no_deferral(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版が一致して有効な導入は更新すべき差が無く、Codexの稼働中でも延期の案内を返さない。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))
    monkeypatch.setattr(
        install_codex_plugins,
        "_codex_json",
        lambda args: _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(),
    )
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _args: pytest.fail("差が無い導入へaddを呼ばない"))

    outcome = install_codex_plugins.run()

    assert outcome.deferred is False
    assert not [notice for notice in outcome.notices if "を延期しました" in notice.message]
