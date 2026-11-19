"""launcherがagents_serverのMCP起動を管理サービスへ転送するか直接起動するかの分岐を検証する。"""

import contextlib
import importlib.util
import pathlib
import sys
import typing

import pytest
from agent_toolkit._agents_server import service_forward

_LAUNCH_PATH = pathlib.Path(__file__).resolve().with_name("launch.py")


def _load_launch() -> typing.Any:
    spec = importlib.util.spec_from_file_location("launch_under_test", _LAUNCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("has_service", [True, False], ids=["shared-service-root", "legacy-root"])
def test_agents_server_forwards_only_when_root_has_shared_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, has_service: bool
) -> None:
    """管理サービスを持つrootだけを転送し、持たない旧版のrootは旧版のlauncherを呼ばずに直接起動する。

    旧版のrootへ転送すると、そのrootのlauncherが`--agents-service`を受け付けず管理サービスの起動が失敗する
    （2026-11-18のupdate-dotfilesの旧版更新検証で観測）。
    """
    launch = _load_launch()
    root = tmp_path / "root"
    target = root / "agent_toolkit" / "agents_server_mcp.py"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    if has_service:
        service = root / "agent_toolkit" / "_agents_server" / "service.py"
        service.parent.mkdir(parents=True)
        service.write_text("", encoding="utf-8")
    calls: list[str] = []

    def fake_forward(**kwargs: typing.Any) -> int:
        del kwargs
        calls.append("forward")
        return 0

    def fake_run(arguments: list[str], environment: dict[str, str], **kwargs: typing.Any) -> int:
        del environment, kwargs
        calls.append(f"direct:{arguments[-1]}")
        return 0

    def fake_lock(*args: typing.Any, **kwargs: typing.Any) -> contextlib.AbstractContextManager[None]:
        del args, kwargs
        return contextlib.nullcontext()

    def fake_environment(*args: typing.Any, **kwargs: typing.Any) -> dict[str, str]:
        del args, kwargs
        return {}

    def fake_prepared_python(*args: typing.Any, **kwargs: typing.Any) -> pathlib.Path:
        del args, kwargs
        return pathlib.Path(sys.executable)

    def fake_which(*args: typing.Any, **kwargs: typing.Any) -> str:
        del args, kwargs
        return "uv"

    monkeypatch.setattr(service_forward, "forward", fake_forward)
    monkeypatch.setattr(launch, "_run_agents_server", fake_run)
    monkeypatch.setattr(launch.environment_usage, "lock", fake_lock)
    monkeypatch.setattr(launch.plugin_environment, "launch_environment", fake_environment)
    monkeypatch.setattr(launch.plugin_environment, "direct_environment", fake_environment)
    monkeypatch.setattr(launch.plugin_environment, "prepared_python", fake_prepared_python)
    monkeypatch.setattr(launch.plugin_environment, "project_environment", fake_prepared_python)
    monkeypatch.setattr(launch.shutil, "which", fake_which)
    monkeypatch.setattr(sys, "argv", ["launch.py", "--project", str(root), "agent_toolkit/agents_server_mcp.py"])

    assert launch.main() == 0
    assert calls == (["forward"] if has_service else [f"direct:{target.resolve()}"])
