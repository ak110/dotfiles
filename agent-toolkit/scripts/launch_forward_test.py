import contextlib
import importlib.util
import io
import json
import os
import pathlib
import signal
import sys
import types
import typing

import psutil
import pytest
from agent_toolkit._agents_server import plugin_root, service_forward
from agent_toolkit._atk.wi import submit
from agent_toolkit._common import plugin_root_leases, process_identity, state_paths

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
    monkeypatch.setattr(launch.plugin_environment, "prepared_environment_python", fake_prepared_python)
    monkeypatch.setattr(launch.plugin_environment, "project_environment", fake_prepared_python)
    monkeypatch.setattr(launch.shutil, "which", fake_which)
    monkeypatch.setattr(sys, "argv", ["launch.py", "--project", str(root), "agent_toolkit/agents_server_mcp.py"])

    assert launch.main() == 0
    assert calls == (["forward"] if has_service else [f"direct:{target.resolve()}"])


@pytest.mark.parametrize("case", ["pre", "agy", "timeout", "cancel", "changed"])
def test_hook_starts_prepared_python_under_monitor(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, case: str) -> None:
    """再入せずにhookを起動し、監視と回収が終わるまで共有ロックを保つ。"""
    if case == "cancel" and os.name == "nt":
        pytest.skip("Windowsの取消は所有Jobが担う")
    launch = _load_launch()
    target = tmp_path / "agent_toolkit" / "hook.py"
    target.parent.mkdir()
    target.touch()
    subcommand = ["antigravity", "PreInvocation"] if case == "agy" else ["pretooluse"]
    payload = {"workspacePaths": [str(tmp_path)]} if case == "agy" else {"cwd": str(tmp_path)}
    raw = ("  " + json.dumps({**payload, "marker": "入力"}, ensure_ascii=False) + "\n").encode()
    input_path = tmp_path / "input"
    input_path.write_bytes(raw)
    direct_env = {"PYTHONPATH": str(tmp_path)}
    prepared_python = pathlib.Path(sys.executable)
    events: list[str] = []
    usage_fds: tuple[int, ...] = ()

    @contextlib.contextmanager
    def fake_lock(environment: pathlib.Path) -> typing.Generator[typing.BinaryIO]:
        nonlocal usage_fds
        assert environment == tmp_path
        with (tmp_path / "usage-lock").open("wb") as handle:
            usage_fds = () if os.name == "nt" else (handle.fileno(),)
            events.append("lock")
            try:
                yield handle
            finally:
                events.append("unlock")

    class Process:
        def wait(self, *, timeout: float) -> int:
            assert 0 < timeout <= 0.1
            return 17

    process = Process()

    @contextlib.contextmanager
    def fake_lifetime(
        arguments: list[str],
        environment: dict[str, str],
        received: list[dict[str, str]],
        tail: bytearray,
        *,
        stdin: typing.BinaryIO,
        cwd: pathlib.Path,
        pass_fds: tuple[int, ...],
    ) -> typing.Generator[Process]:
        assert arguments == [str(prepared_python), str(target), *subcommand]
        assert environment == direct_env
        assert cwd == tmp_path
        assert stdin.read() == raw
        assert pass_fds == usage_fds
        assert not tail
        events.append("spawn")
        if case == "cancel":
            received.append({"signal": "SIGTERM"})
        try:
            yield process
        finally:
            events.append("reclaim")

    def stop(child: Process, *, signum: int, graceful_timeout: float) -> None:
        assert child is process and signum == signal.SIGKILL and graceful_timeout == 0
        events.append("cancel")

    changed = tmp_path / "changed-by-update"
    keys = iter((tmp_path, changed if case == "changed" else tmp_path, changed))

    def python_for_key(selected: pathlib.Path) -> pathlib.Path:
        assert selected == tmp_path
        return prepared_python

    def environment_for_key(*_args, selected: pathlib.Path) -> dict[str, str]:
        assert selected == tmp_path
        return direct_env

    monkeypatch.setattr(launch.plugin_environment, "prepared_environment_python", python_for_key)
    monkeypatch.setattr(launch.plugin_environment, "project_environment", lambda *_: next(keys))
    monkeypatch.setattr(launch.plugin_environment, "direct_environment", environment_for_key)
    monkeypatch.setattr(launch.environment_usage, "lock", fake_lock)
    monkeypatch.setattr(launch.shutil, "which", lambda *_: "uv")
    monkeypatch.setattr(launch, "_server_lifetime", fake_lifetime)
    monkeypatch.setattr(launch, "_stop_server_process", stop)
    if case == "timeout":
        monkeypatch.setattr(launch, "_HOOK_TIMEOUT", 0)
    monkeypatch.setattr(sys, "argv", ["launch.py", "--project", str(tmp_path), str(target), *subcommand])
    with input_path.open("r", encoding="utf-8") as input_stream:
        monkeypatch.setattr(sys, "stdin", input_stream)
        assert launch.main() == {"pre": 17, "agy": 17, "timeout": 124, "cancel": 128 + signal.SIGTERM, "changed": 1}[case]
    assert next(keys) == changed
    if case == "changed":
        assert events == ["lock", "unlock"]
        return
    assert events == ["lock", "spawn", *(["cancel"] if case == "cancel" else []), "reclaim", "unlock"]


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse", "UserPromptSubmit"])
def test_unprepared_hook_returns_context(monkeypatch, tmp_path, capsys, event):
    launch = _load_launch()
    target = tmp_path / "agent_toolkit" / "hook.py"
    target.parent.mkdir()
    target.touch()
    raw = io.BytesIO(json.dumps({"hook_event_name": event}).encode())
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw))
    monkeypatch.setattr(sys, "argv", ["launch.py", "--project", str(tmp_path), str(target)])
    monkeypatch.setattr(launch.plugin_environment, "project_environment", lambda *_: tmp_path)
    monkeypatch.setattr(launch.plugin_environment, "prepared_environment_python", lambda *_: None)
    monkeypatch.setattr(launch.shutil, "which", lambda *_: "uv")

    assert launch.main() == 1
    captured = capsys.readouterr()
    response = json.loads(captured.out)["hookSpecificOutput"]
    assert response["hookEventName"] == event
    assert "未準備" in response["additionalContext"] and "準備状態" in response["additionalContext"]
    assert "未準備" in captured.err


@pytest.mark.parametrize("changes_before_sync", [False, True])
def test_prepare_does_not_publish_changed_environment(monkeypatch, tmp_path, capsys, changes_before_sync):
    launch = _load_launch()
    target = tmp_path / "selected"
    changed = tmp_path / "changed-by-update"
    keys = iter((changed,) if changes_before_sync else (target, changed))
    syncs = []

    @contextlib.contextmanager
    def sync(*_args, **kwargs):
        syncs.append(kwargs["environment"]["VIRTUAL_ENV"])
        yield types.SimpleNamespace(wait=lambda **_kwargs: 0)

    monkeypatch.setattr(launch, "_server_lifetime", sync)
    monkeypatch.setattr(launch.plugin_environment, "launch_environment", lambda *_: {"UV_PROJECT_ENVIRONMENT": str(target)})
    monkeypatch.setattr(launch.plugin_environment, "project_environment", lambda *_: next(keys))
    monkeypatch.setattr(launch.plugin_environment, "prepared_environment_python", lambda *_: None)
    monkeypatch.setattr(
        launch.plugin_environment, "publish_prepared_environment", lambda *_args, **_kwargs: pytest.fail("公開してはならない")
    )

    assert launch._prepare(tmp_path, "uv", {}) == 1  # pylint: disable=protected-access
    assert syncs == ([] if changes_before_sync else [str(target)])
    assert "依存の固定内容が変更された" in capsys.readouterr().err


@pytest.mark.parametrize("entry", ["launcher", "bootstrap", "submit"])
@pytest.mark.parametrize("registered", [False, True])
def test_exit_paths_release_own_lease_and_keep_services_copy(tmp_path, monkeypatch, entry, registered):
    source = tmp_path / "1.0.0"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
    path = source / "agent_toolkit/_agents_server/service.py"
    path.parent.mkdir(parents=True)
    path.touch()
    root = plugin_root.resolve_stable_plugin_root(source, independent=True)
    leases = state_paths.state_dir() / "plugin-root-leases"
    (own,) = leases.glob(f"*-{os.getpid()}.json")
    if registered:
        other = psutil.Process(os.getppid())
        record = json.loads(own.read_text(encoding="utf-8"))
        record.update(pid=other.pid, created_at=other.create_time(), start_id=process_identity.start_id(other))
        other_record = own.with_name(f"{own.stem.rsplit('-', 1)[0]}-{other.pid}.json")
        with plugin_root_leases.plugin_root_lock(root.parent):
            other_record.write_text(json.dumps(record), encoding="utf-8")
        monkeypatch.setattr(plugin_root_leases, "_current_processes", lambda: [])
    else:
        service = psutil.Process()
        monkeypatch.setattr(service, "cmdline", lambda: ["python", str(root / path.relative_to(source))])
        monkeypatch.setattr(plugin_root_leases, "_current_processes", lambda: [service])

    if entry == "launcher":
        launch = _load_launch()
        monkeypatch.setattr(plugin_root, "SERVER_PLUGIN_ROOT", root)
        monkeypatch.setattr(launch.environment_usage, "lock", contextlib.nullcontext)
        monkeypatch.setattr(launch.shutil, "which", lambda _name: "uv")
        monkeypatch.setattr(launch.plugin_environment, "project_environment", lambda *_args: tmp_path)
        monkeypatch.setattr(
            launch.plugin_environment, "prepared_environment_python", lambda *_args: pathlib.Path(sys.executable)
        )
        monkeypatch.setattr(launch.plugin_environment, "direct_environment", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(launch, "_run_agents_server", lambda *_args, **_kwargs: 17)
        monkeypatch.setattr(
            sys, "argv", ["launch.py", "--project", str(source), "--agents-service", str(path), str(tmp_path), "G1"]
        )
        assert launch.main() == 17
    elif entry == "bootstrap":
        monkeypatch.setattr(plugin_root, "resolve_stable_plugin_root", lambda **_kwargs: root)
        monkeypatch.setattr(plugin_root.plugin_environment, "prepared_python", lambda *_args: pathlib.Path(sys.executable))
        monkeypatch.setattr(plugin_root, "os", types.SimpleNamespace(name="nt", environ=os.environ))
        monkeypatch.setattr(plugin_root.subprocess, "run", lambda *_args, **_kwargs: types.SimpleNamespace(returncode=17))
        assert plugin_root.main(["--wi-submit-worker", "test"]) == 17
    else:
        monkeypatch.setattr(submit, "__file__", str(root / "agent_toolkit/_atk/wi/submit.py"))
        monkeypatch.setattr(submit, "worker", lambda _target: 17)
        assert submit._main(["worker", "test"]) == 17  # pylint: disable=protected-access

    assert not own.exists()
    assert (root / path.relative_to(source)).is_file()
