"""hook_inventoryの契約テスト。"""

import json
import pathlib
import shutil
import subprocess

import pytest

from agent_toolkit import hook as _hook
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import plugin_roots as resource_roots
from agent_toolkit._common.plugin_roots import _inventories, _plugin_inventory, _reusable
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli
from agent_toolkit._testing.hook_support import _call_hook_in_process, _registered_subcommands, _run_with_module_record
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SUBCOMMANDS = _registered_subcommands()


_UNUSED_BRANCH_MODULES = (
    "agent_toolkit._agents_server.state",
    "agent_toolkit._hooks.agents_server_observations",
    "agent_toolkit._hooks.background_tasks",
    "agent_toolkit._hooks.pretooluse.shell_checks",
)


class TestLazyDependencyLoading:
    """公開hookが処理分岐で使わない依存を読み込まず、遅延を無効化した場合と同じ応答を返すことを検証する。"""

    @pytest.mark.parametrize("subcommand", _SUBCOMMANDS)
    def test_each_hook_loads_fewer_modules_with_same_response(self, tmp_path: pathlib.Path, subcommand: str) -> None:
        lazy, lazy_modules = _run_with_module_record(tmp_path, "lazy", subcommand, b"{}")
        eager, eager_modules = _run_with_module_record(tmp_path, "eager", subcommand, b"{}")

        assert (lazy.returncode, lazy.stdout, lazy.stderr) == (eager.returncode, eager.stdout, eager.stderr)
        assert len(lazy_modules) < len(eager_modules)
        # session_idを持たない入力はどのhookでも委譲・バックグラウンドタスク・報告解析へ到達しない。
        assert not set(_UNUSED_BRANCH_MODULES) & lazy_modules

    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "used_modules"),
        [
            ("Read", {"file_path": "read-target.txt"}, ()),
            ("Bash", {"command": "ls"}, ("agent_toolkit._hooks.pretooluse.shell_checks",)),
        ],
        ids=["normal", "bash-pass"],
    )
    def test_pretooluse_skips_dependencies_of_unused_branches(
        self,
        tmp_path: pathlib.Path,
        tool_name: str,
        tool_input: dict[str, str],
        used_modules: tuple[str, ...],
    ) -> None:
        target = tmp_path / "read-target.txt"
        directory = managed_temp.resolve_session_temp("lazy-session")
        (directory / "work-contract.md").write_text("目的: 依存読込の検証\n", encoding="utf-8")
        target.write_text("内容\n", encoding="utf-8")
        resolved_input = {key: str(target) if key == "file_path" else value for key, value in tool_input.items()}
        payload = json.dumps(
            {"session_id": "lazy-session", "cwd": str(tmp_path), "tool_name": tool_name, "tool_input": resolved_input}
        ).encode("utf-8")

        lazy, lazy_modules = _run_with_module_record(tmp_path, "lazy", "pretooluse", payload)
        eager, eager_modules = _run_with_module_record(tmp_path, "eager", "pretooluse", payload)

        assert (lazy.returncode, lazy.stdout, lazy.stderr) == (0, b"", b"")
        assert (eager.returncode, eager.stdout, eager.stderr) == (0, b"", b"")
        assert len(lazy_modules) < len(eager_modules)
        unused = set(_UNUSED_BRANCH_MODULES) - set(used_modules)
        assert unused <= eager_modules
        assert not unused & lazy_modules


@pytest.mark.parametrize("host", ["claude", "codex", "agy", "opencode"])
@pytest.mark.parametrize("command", ["pretooluse", "posttooluse", "stop", "permissionrequest"])
def test_public_hook_owns_one_inventory_scope_per_session(tmp_path, monkeypatch, command, host):
    """各handlerの消費側で一覧を共有し、別sessionと例外後の要求を混ぜない。"""
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: str(executable))
    monkeypatch.setattr(_hook, "_record_activity", lambda *_args, **_kwargs: None)
    acquired = []
    observed = []
    fail = False

    # 設定済みmarketplaceが無いCodexでは、marketplace一覧の取得だけで導入情報の取得を終える。
    codex_cli = codex_plugin_cli([], marketplaces=[])

    def inventory(args, **kwargs):
        acquired.append(args)
        if host == "codex":
            return codex_cli(args, **kwargs)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    def native(_host):
        acquired.append(_host)
        return []

    def consume(_module, _text, payload, _args):
        _plugin_inventory(host, tmp_path)
        _plugin_inventory(host, tmp_path)
        cache = _reusable.get()
        observed.append((payload["session_id"], cache is not None))
        if fail:
            raise RuntimeError("消費側の失敗")
        return 0

    monkeypatch.setattr(resource_roots.process_tree, "run_with_timeout", inventory)
    monkeypatch.setattr(resource_roots, "_native_inventory", native)
    monkeypatch.setattr(_hook, "_invoke_module", consume)
    for session_id in ("one", "one", "two"):
        _call_hook_in_process(monkeypatch, command, {"session_id": session_id, "hook_event_name": "PostToolUse"})
    assert observed == [("one", True), ("one", True), ("two", True)]
    assert len(acquired) == (2 if host == "claude" else 3)
    fail = True
    _call_hook_in_process(monkeypatch, command, {"session_id": "three", "hook_event_name": "PostToolUse"})
    assert _inventories.get() is None and _reusable.get() is None
    fail = False
    _call_hook_in_process(monkeypatch, command, {"session_id": "four", "hook_event_name": "PostToolUse"})
    assert len(acquired) == (4 if host == "claude" else 5)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"session_id": ""},
        {"session_id": 1},
        {"session_id": "../other"},
        {"session_id": "one", "hook_event_name": "SessionStart"},
    ],
)
def test_public_hook_does_not_attach_persistent_inventory_to_invalid_or_start_payload(monkeypatch, payload):
    seen = []
    monkeypatch.setattr(_hook, "_record_activity", lambda *_args, **_kwargs: None)

    def consume(*_args):
        seen.append(_reusable.get())
        return 0

    monkeypatch.setattr(_hook, "_invoke_module", consume)
    result = _run_hook_in_process("pretooluse", payload)
    assert result.returncode == 0
    assert seen == [None]
    assert _inventories.get() is None and _reusable.get() is None
