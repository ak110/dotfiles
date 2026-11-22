"""hook_inventoryの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._testing.hook_support import _registered_subcommands, _run_with_module_record

_SUBCOMMANDS = _registered_subcommands()


_UNUSED_BRANCH_MODULES = (
    "agent_toolkit._agents_server.state",
    "agent_toolkit._hooks.agents_server_observations",
    "agent_toolkit._hooks.background_tasks",
    "agent_toolkit._hooks.pretooluse.shell_checks",
)


class TestLazyDependencyLoading:
    @pytest.mark.parametrize("subcommand", _SUBCOMMANDS)
    def test_each_hook_loads_fewer_modules_with_same_response(self, tmp_path: pathlib.Path, subcommand: str) -> None:
        lazy, lazy_modules = _run_with_module_record(tmp_path, "lazy", subcommand, b"{}")
        eager, eager_modules = _run_with_module_record(tmp_path, "eager", subcommand, b"{}")

        assert (lazy.returncode, lazy.stdout, lazy.stderr) == (eager.returncode, eager.stdout, eager.stderr)
        assert len(lazy_modules) < len(eager_modules)

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
