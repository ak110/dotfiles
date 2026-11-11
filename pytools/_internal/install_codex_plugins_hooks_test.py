# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""install_codex_pluginsのhooksの試験。"""

# pylint: disable=protected-access

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import (
    install_codex_plugins,
)
from pytools._internal.install_codex_plugins_test_helpers import _REAL_HOOKS_LIST, _write_claude_plugin_root
from pytools._internal.install_codex_plugins_test_helpers import _empty_unused_plugins as _empty_unused_plugins
from pytools._internal.install_codex_plugins_test_helpers import plugin_env_fixture as plugin_env_fixture


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


def test_hooks_list_reports_unreclaimed_app_server_instead_of_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """短命app-serverの子孫を回収できなければ、案内取得不能（None）と区別して回収不能を送出する。"""
    real_popen = subprocess.Popen

    def popen(_argv: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        return real_popen([sys.executable, "-c", "import sys; sys.stdin.read()"], **kwargs)  # pylint: disable=consider-using-with

    def stop_root_only(process: subprocess.Popen[str], **_kwargs: Any) -> tuple[list[str], list[object]]:
        # 実際の停止と同じく直接の子は終了・回収し、子孫だけが残った結果を返す。
        # 起動した実プロセスを残すと、Popenの解放時のResourceWarningが別の試験へ帰属し得る。
        process.kill()
        process.wait()
        return ["1234: app-server child"], []

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(install_codex_plugins, "_TIMEOUT", 0.1)
    monkeypatch.setattr(install_codex_plugins.common, "stop_owned_process", stop_root_only)
    with pytest.raises(install_codex_plugins.common.ProcessReclaimError):
        _REAL_HOOKS_LIST()


@pytest.mark.parametrize(
    "failure",
    [
        "marketplaceName",
        "name",
        "version",
        "not-list",
        "not-item",
        "null-version",
        "path-version",
        "missing-root",
        "drive-marketplace",
        "wrong-name",
        "os-error",
        "timeout",
    ],
)
def test_external_hook_codex_inventory_failure_identifies_host_and_command(tmp_path, monkeypatch, capsys, failure):
    """Claude側に対象が無い旧shimの公開入口でも、Codexの不足要素を診断できる。"""
    script = Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook"
    shim = runpy.run_path(str(script))
    monkeypatch.setitem(shim["main"].__globals__, "__file__", str(tmp_path / "atk-hook"))
    monkeypatch.setattr(sys, "argv", ["atk-hook", "PreToolUse"])
    monkeypatch.delenv("PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    item = {
        "pluginId": "agent-toolkit@ak110-dotfiles",
        "enabled": True,
        "marketplaceName": "ak110-dotfiles",
        "name": "agent-toolkit",
        "version": "1.0",
    }
    if failure in item:
        item.pop(failure)
    if failure == "null-version":
        item["version"] = None
    elif failure == "path-version":
        item["version"] = "../outside"
    elif failure == "drive-marketplace":
        item["marketplaceName"] = "C:"
    elif failure == "wrong-name":
        item["name"] = "other"
    installed: object = {} if failure == "not-list" else [None] if failure == "not-item" else [item]

    def run(args, **_kwargs):
        if args[0] == "codex" and failure == "os-error":
            raise OSError("取得失敗")
        if args[0] == "codex" and failure == "timeout":
            raise subprocess.TimeoutExpired(args, 15)
        return subprocess.CompletedProcess(args, 0, json.dumps([] if args[0] == "claude" else {"installed": installed}), "")

    monkeypatch.setattr(subprocess, "run", run)
    assert shim["main"]() == 1
    message = capsys.readouterr().err
    assert "Codex" in message
    assert "codex plugin list --json" in message


@pytest.mark.parametrize("failure", ["acquisition", "json", "scope", "disabled", "ambiguous", "missing"])
def test_external_hook_reports_the_claude_cause_and_command(tmp_path, monkeypatch, capsys, failure):
    """外部shimがClaude側で失敗した場合に、原因側の操作をそのまま示す。"""
    script = Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook"
    shim = runpy.run_path(str(script))
    monkeypatch.setitem(shim["main"].__globals__, "__file__", str(tmp_path / "atk-hook"))
    monkeypatch.setattr(sys, "argv", ["atk-hook", "PreToolUse"])
    monkeypatch.delenv("PLUGIN_ROOT", raising=False)
    item = {
        "id": "agent-toolkit@ak110-dotfiles",
        "scope": "user",
        "enabled": failure != "disabled",
        "installPath": str(tmp_path / "missing"),
    }
    if failure == "scope":
        item.update(scope="project", projectPath=str(tmp_path / "other"))
    installed = [item, item.copy()] if failure == "ambiguous" else [item]

    def run(args, **_kwargs):
        assert args == ["claude", "plugin", "list", "--json"]
        return subprocess.CompletedProcess(
            args, 1 if failure == "acquisition" else 0, "not-json" if failure == "json" else json.dumps(installed), ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert shim["main"]() == 1
    message = capsys.readouterr().err
    assert "Claude Code" in message
    assert "claude plugin list --json" in message
    assert "Codexのplugin一覧" not in message
    assert "同じhookを再実行" in message


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
        if args[0] == "claude":
            return subprocess.CompletedProcess(args, 0, "[]", "")
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
        assert calls[0] == ["codex", "plugin", "list", "--json", "--marketplace", "ak110-dotfiles"]
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


def test_hook_wrapper_restores_missing_pinned_root_from_same_claude_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commit中の自動更新で旧cacheが消えても、独立した同版cacheからhook起動コマンドを復旧する。"""
    home = tmp_path / "home"
    wrapper = home / ".local/bin/atk-hook"
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook", wrapper)
    version = "1.2.2"
    claude_root = home / ".claude/plugins/cache/ak110-dotfiles/agent-toolkit" / version
    _write_claude_plugin_root(claude_root, version)
    (claude_root / ".venv").mkdir()
    pinned = home / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit" / version
    calls: list[list[str]] = []
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("PLUGIN_ROOT", str(pinned))
    monkeypatch.setattr(sys, "argv", [str(wrapper), "pretooluse"])

    def execute(_executable: str, args: list[str]) -> None:
        calls.append(args)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", execute)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("同版Claude cacheで復旧できる"))

    with pytest.raises(SystemExit, match="0"):
        runpy.run_path(str(wrapper), run_name="__main__")

    assert (pinned / "marker").read_text(encoding="utf-8") == version
    assert not (pinned / ".venv").exists()
    assert calls == [[sys.executable, str(pinned / "scripts/launch.py"), "agent_toolkit/hook.py", "pretooluse"]]


def test_hook_wrapper_uses_current_claude_install_when_same_version_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同版cacheが無い場合も、編集中の作業ツリーを避けて現行Claude配布物からhookを起動する。"""
    home = tmp_path / "home"
    wrapper = home / ".local/bin/atk-hook"
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "agent-toolkit/scripts/atk-hook", wrapper)
    current = home / ".claude/plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3"
    _write_claude_plugin_root(current, "1.2.3")
    installed = [{"id": "agent-toolkit@ak110-dotfiles", "scope": "user", "enabled": True, "installPath": str(current)}]
    pinned = home / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    calls: list[list[str]] = []
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("PLUGIN_ROOT", str(pinned))
    monkeypatch.setattr(sys, "argv", [str(wrapper), "pretooluse"])

    def execute(_executable: str, args: list[str]) -> None:
        calls.append(args)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", execute)

    def run(args, **_kwargs):
        assert args == ["claude", "plugin", "list", "--json"]
        return subprocess.CompletedProcess(args, 0, json.dumps(installed), "")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(SystemExit, match="0"):
        runpy.run_path(str(wrapper), run_name="__main__")

    assert not pinned.exists()
    assert calls == [[sys.executable, str(current / "scripts/launch.py"), "agent_toolkit/hook.py", "pretooluse"]]


__all__ = ["plugin_env_fixture"]
