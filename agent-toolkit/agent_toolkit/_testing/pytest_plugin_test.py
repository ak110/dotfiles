"""共通pytestの収集と隔離を確認する。"""

import asyncio
import contextlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from typing import Any, cast

import claude_agent_sdk
import psutil
import pytest

from agent_toolkit._agents_server import antigravity, backends
from agent_toolkit._testing import late_descendant, pytest_plugin
from agent_toolkit._testing.pytest_plugin_support import _config

# pylint: disable=protected-access


@pytest.mark.parametrize("engine", sorted(backends.SUPPORTED_ENGINES))
@pytest.mark.asyncio
async def test_forbid_backend_process_rejects_every_supported_backend(
    tmp_path: pathlib.Path, _forbid_backend_process: None, engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全engineのbackendを生成して、実CLIへ届く前に起動が遮断されることを確かめる。"""

    def unexpected_client(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("遮断fixtureがSDKへ到達した")

    async def unexpected_process(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("遮断fixtureがイベントループの子プロセス生成へ到達した")

    # 検証対象のguardが壊れた場合も、さらに下層で実SDK/CLIへの到達を止める。
    # 期待するAssertionErrorと異なる例外にして、guardの欠落を成功と数えない。
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", unexpected_client)
    monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", unexpected_process)
    if engine == "agy":
        monkeypatch.setattr(antigravity, "_gemini_delegation_binary", lambda _environment: "agy")
    backend = backends.create_backend(
        engine,
        {},
        asyncio.Condition(),
        expire_session=lambda _session_id: None,
        root_session_id=None,
        log_directory=tmp_path,
    )
    try:
        with pytest.raises(AssertionError, match="実backendの子プロセス"):
            await backend.start("遮断確認", str(tmp_path), model="test/model", effort="low" if engine == "codex" else None)
    finally:
        await backend.close()


def test_target_plugin_root_limits_import_check_to_public_target(tmp_path: pathlib.Path) -> None:
    """外部fixtureのrootは比較せず、dotfilesとplugin rootは対象を一意にする。"""
    plugin = tmp_path / "dotfiles" / "agent-toolkit"
    (plugin / "agent_toolkit").mkdir(parents=True)
    (plugin / "agent_toolkit" / "__init__.py").touch()
    assert pytest_plugin._target_plugin_root(_config(plugin)) == plugin
    assert pytest_plugin._target_plugin_root(_config(plugin.parent)) == plugin
    assert pytest_plugin._target_plugin_root(_config(tmp_path / "external")) is None


def test_target_plugin_root_accepts_renamed_standalone_package(tmp_path: pathlib.Path) -> None:
    """standalone checkoutはディレクトリ名に関係なく実在するパッケージで判定する。"""
    standalone = tmp_path / "cached-agent-toolkit-vnext"
    (standalone / "agent_toolkit").mkdir(parents=True)
    (standalone / "agent_toolkit" / "__init__.py").touch()
    assert pytest_plugin._target_plugin_root(_config(standalone)) == standalone


@pytest.mark.parametrize("target_kind", ["plugin", "dotfiles"])
def test_validate_import_root_accepts_public_target_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    """plugin rootとdotfiles rootのどちらからも、対象内の読込を受理する。"""
    plugin = tmp_path / "dotfiles" / "agent-toolkit"
    loaded = plugin / "agent_toolkit" / "__init__.py"
    loaded.parent.mkdir(parents=True)
    loaded.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(loaded))
    pytest_plugin._validate_import_root(_config(plugin if target_kind == "plugin" else plugin.parent))


@pytest.mark.parametrize("target_kind", ["plugin", "dotfiles"])
def test_validate_import_root_rejects_other_plugin(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    """公開対象とは異なる配布rootの読込を、双方のパスを含む診断で拒否する。"""
    target = tmp_path / "dotfiles" / "agent-toolkit"
    foreign = tmp_path / "foreign" / "agent_toolkit" / "__init__.py"
    (target / "agent_toolkit").mkdir(parents=True)
    (target / "agent_toolkit" / "__init__.py").touch()
    foreign.parent.mkdir(parents=True)
    foreign.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(foreign))
    root = target if target_kind == "plugin" else target.parent
    with pytest.raises(pytest.UsageError) as error:
        pytest_plugin._validate_import_root(_config(root))
    message = str(error.value)
    assert "loaded=" in message and "target=" in message
    assert "PYTHONPATH と UV_PYTHON を解除し" in message
    assert "対象worktreeをカレントディレクトリにして" in message
    assert "uv run --frozen pytest <対象試験>" in message


def test_validate_import_root_skips_external_fixture_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """外部fixtureのpytestはagent-toolkit読込元の比較対象にしない。"""
    foreign = tmp_path / "foreign" / "agent_toolkit" / "__init__.py"
    foreign.parent.mkdir(parents=True)
    foreign.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(foreign))
    pytest_plugin._validate_import_root(_config(tmp_path / "fixture"))


def test_validate_import_root_skips_name_only_external_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """名前だけがagent-toolkitの外部fixtureはパッケージ実体がなければ対象外にする。"""
    external = tmp_path / "agent-toolkit"
    external.mkdir()
    foreign = tmp_path / "foreign" / "agent_toolkit" / "__init__.py"
    foreign.parent.mkdir(parents=True)
    foreign.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(foreign))
    pytest_plugin._validate_import_root(_config(external))


@pytest.mark.parametrize("mode", ["pass", "fail", "interrupt", "xdist", "explicit"])
def test_pytest_owns_fixture_directory_and_reclaims_children(tmp_path: pathlib.Path, mode: str) -> None:
    """公開pytestの正常・失敗・中断とworker終了で専用領域を回収し、証拠と明示領域を保つ。"""
    evidence = tmp_path / "evidence.json"
    probe = tmp_path / "probe_test.py"
    probe.write_text(
        textwrap.dedent(f"""\
            import json, pathlib, subprocess, sys
            import pytest
            from pyfltr.command import execution_slot

            def test_probe(tmp_path, pytestconfig):
                owner = execution_slot.read_owner()
                assert owner is not None
                child = subprocess.Popen([sys.executable, '-c', {late_descendant.held_source(tmp_path / "probe.release")!r}])
                pathlib.Path({str(evidence)!r}).write_text(json.dumps({{
                    'basetemp': str(pytestconfig.option.basetemp),
                    'fixture': str(tmp_path), 'pid': child.pid,
                }}), encoding='utf-8')
                {"raise KeyboardInterrupt" if mode == "interrupt" else "assert False" if mode == "fail" else "assert True"}
        """),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYFLTR_CACHE_DIR"] = str(tmp_path / "pyfltr")
    argv = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-p", pytest_plugin.__name__]
    if mode == "xdist":
        argv.extend(["-p", "xdist.plugin", "-n", "2"])
    explicit = tmp_path / "caller-owned"
    if mode == "explicit":
        argv.extend(["--basetemp", str(explicit)])
    result = subprocess.run(
        [*argv, "--junitxml", str(tmp_path / "junit.xml"), str(probe)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == {"fail": 1, "interrupt": 2}.get(mode, 0), result.stdout + result.stderr
    observed = json.loads(evidence.read_text(encoding="utf-8"))
    assert evidence.exists() and (tmp_path / "junit.xml").exists()
    assert pathlib.Path(observed["basetemp"]).exists() == (mode == "explicit")
    if mode != "explicit":
        base = pathlib.Path(observed["basetemp"])
        # xdist側ではworkerの領域が記録される。controllerが所有する最上位領域も回収される。
        directory = next(path for path in (base, *base.parents) if path.name == "fixtures")
        assert not directory.parent.exists()
    with contextlib.suppress(psutil.NoSuchProcess):
        assert psutil.Process(observed["pid"]).status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)


@pytest.mark.parametrize("workers", [0, 2])
def test_concurrent_controllers_keep_other_fixtures_and_saved_outputs(tmp_path: pathlib.Path, workers: int) -> None:
    """別controllerが待機中の領域を残し、自分の領域だけを回収して両出力を保持する。"""
    probe = tmp_path / "concurrent_test.py"
    probe.write_text(
        textwrap.dedent("""\
            import json, os, socket

            def test_probe(tmp_path, pytestconfig):
                (tmp_path / 'payload').write_text('実行中', encoding='utf-8')
                if os.environ.get('PROBE_PORT'):
                    with socket.create_connection(('127.0.0.1', int(os.environ['PROBE_PORT'])), timeout=30) as peer:
                        peer.sendall((json.dumps({'base': str(pytestconfig.option.basetemp)}) + '\\n').encode())
                        assert peer.recv(1) == b'x'
                print('保存する標準出力')
                import sys
                print('保存する標準エラー', file=sys.stderr)
        """),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # この検証は所有領域の独立性を対象とするため、検証枠だけをcontrollerごとに分離する。
    environment["PYFLTR_CACHE_DIR"] = str(tmp_path / "second-slot")
    argv = [sys.executable, "-m", "pytest", "-q", "-s", "-o", "addopts=", "-p", pytest_plugin.__name__]
    if workers:
        argv.extend(["-p", "xdist.plugin", "-n", str(workers)])
    argv.append(str(probe))
    saved_paths = [(tmp_path / f"controller-{number}.stdout", tmp_path / f"controller-{number}.stderr") for number in (0, 1)]
    with contextlib.ExitStack() as stack, socket.socket() as listener:
        streams = [
            (stack.enter_context(output.open("w", encoding="utf-8")), stack.enter_context(error.open("w", encoding="utf-8")))
            for output, error in saved_paths
        ]
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(30)
        first_environment = {
            **environment,
            "PROBE_PORT": str(listener.getsockname()[1]),
            "PYFLTR_CACHE_DIR": str(tmp_path / "first-slot"),
        }
        with subprocess.Popen(
            argv,
            cwd=tmp_path,
            env=first_environment,
            stdout=streams[0][0],
            stderr=streams[0][1],
            text=True,
            encoding="utf-8",
        ) as first:
            try:
                peer, _address = listener.accept()
                with peer:
                    peer.settimeout(30)
                    data = b""
                    while not data.endswith(b"\n"):
                        part = peer.recv(4096)
                        assert part, "最初のcontrollerが所有領域の通知前に終了した"
                        data += part
                    base = pathlib.Path(json.loads(data)["base"])
                    assert base.exists()
                    second = subprocess.run(
                        argv,
                        cwd=tmp_path,
                        env=environment,
                        stdout=streams[1][0],
                        stderr=streams[1][1],
                        text=True,
                        encoding="utf-8",
                        timeout=60,
                        check=False,
                    )
                    assert second.returncode == 0, [path.read_text(encoding="utf-8") for path in saved_paths[1]]
                    assert base.exists()
                    peer.sendall(b"x")
                first.wait(timeout=60)
                assert first.returncode == 0, [path.read_text(encoding="utf-8") for path in saved_paths[0]]
            finally:
                if first.poll() is None:
                    first.terminate()
                    first.wait(timeout=30)
    assert not base.exists()
    for output, error in saved_paths:
        assert "1 passed" in output.read_text(encoding="utf-8")
        assert "保存する標準エラー" in error.read_text(encoding="utf-8")


@pytest.mark.parametrize("parent_first", [False, True])
def test_collection_order_preserves_isolation_and_overrides(tmp_path: pathlib.Path, parent_first: bool) -> None:
    """親を間に配置する収集でも7件が適用され、範囲外・個別差し替え・実物待機への復帰を保つ。"""
    package = tmp_path / "sample"
    for suffix in ("", "_atk", "_atk/managed_temp", "_atk/wi", "_atk/wi/mutations"):
        directory = package / suffix
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").touch()
    # 明示要求型fixtureも起動時プラグインから使い、自動fixtureの適用先だけを試験用パッケージへ向ける。
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent("""\
            import os
            import pathlib
            from agent_toolkit._testing import pytest_plugin
            from agent_toolkit._hooks import notice

            def pytest_configure(config):
                pytest_plugin._PACKAGE_ROOT = pathlib.Path(__file__).parent / "sample"
                os.environ["FORCE_PROMPT_CACHING_5M"] = "outside"
                os.environ["AI_AGENT"] = "outside"
                os.environ["XDG_STATE_HOME"] = str(pathlib.Path(__file__).parent / "outside-state")
                notice._warning_context = {"session_id": "outside", "blocks": []}
        """),
        encoding="utf-8",
    )
    (tmp_path / "probe.py").write_text(
        textwrap.dedent("""\
            import os
            import pathlib
            import shutil
            import tempfile
            from agent_toolkit._common import codex_models
            from agent_toolkit._hooks import notice, transcript_scan
            from agent_toolkit._atk.managed_temp import paths as registry

            original_models = codex_models.list_models
            original_wait = transcript_scan.wait_for_end_turn
            original_terminal = shutil.get_terminal_size
            original_state = registry._state_root_path

            def common():
                assert "FORCE_PROMPT_CACHING_5M" not in os.environ
                assert notice._warning_context == {"session_id": "", "blocks": []}
                assert codex_models.list_models is not original_models
                assert codex_models.list_models()
                assert transcript_scan.wait_for_end_turn is not original_wait
                assert shutil.get_terminal_size().columns == 200

            def outside():
                assert os.environ["FORCE_PROMPT_CACHING_5M"] == "outside"
                assert notice._warning_context["session_id"] == "outside"
                assert codex_models.list_models is original_models
                assert transcript_scan.wait_for_end_turn is original_wait
                assert shutil.get_terminal_size is original_terminal
                unchanged_subdirectories()

            def unchanged_subdirectories():
                assert registry._state_root_path is original_state
                assert os.environ["AI_AGENT"] == "outside"
                assert pathlib.Path(os.environ["XDG_STATE_HOME"]).name == "outside-state"

            def managed(tmp_path):
                common()
                assert registry._state_root_path() == tmp_path / "external-state"
                assert registry._temp_root_path() == tmp_path / "managed-temp"
                assert os.environ["AI_AGENT"] == "outside"

            def mutations(tmp_path):
                common()
                assert "AI_AGENT" not in os.environ
                assert pathlib.Path(tempfile.gettempdir()) == tmp_path / "temp"
                assert pathlib.Path(os.environ["XDG_STATE_HOME"]) == tmp_path / "state"
                assert registry._state_root_path is original_state
        """),
        encoding="utf-8",
    )
    # probeを収集時に読み込んで差し替え前の関数を保持する。
    common = "import probe\n\ndef test_common():\n    probe.common()\n    probe.unchanged_subdirectories()\n"
    managed = "import probe\n\ndef test_managed(tmp_path):\n    probe.managed(tmp_path)\n"
    mutations = "import probe\n\ndef test_mutations(tmp_path):\n    probe.mutations(tmp_path)\n"
    files = {
        "sample/first_test.py": common,
        "sample/_atk/managed_temp/first_test.py": managed,
        "sample/_atk/wi/mutations/first_test.py": mutations,
        "outside_test.py": "import probe\n\ndef test_outside():\n    probe.outside()\n",
        "sample/last_test.py": common,
        "sample/_atk/managed_temp/last_test.py": managed,
        "sample/_atk/wi/mutations/last_test.py": mutations,
        "sample/override_test.py": textwrap.dedent("""\
            import os
            import shutil
            import probe
            from agent_toolkit._hooks import transcript_scan

            def test_overrides(monkeypatch, real_end_turn_wait):
                monkeypatch.setattr(shutil, "get_terminal_size", lambda **kwargs: os.terminal_size((73, 24)))
                assert shutil.get_terminal_size().columns == 73
                assert transcript_scan.wait_for_end_turn is probe.original_wait
        """),
    }
    for relative, content in files.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")
    order = list(files)
    if parent_first:
        order.remove("outside_test.py")
        order.insert(0, "outside_test.py")
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-p",
            "agent_toolkit._testing.pytest_plugin",
            *order,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passed" in result.stdout


@pytest.mark.parametrize(
    "before,after,failed",
    [
        ((None, None, False), (None, None, False), False),
        ((None, None, False), (7, 1.0, True), False),
        ((7, 1.0, True), (7, 1.0, True), False),
        ((7, 1.0, True), (7, 1.0, False), True),
        ((7, 1.0, True), (7, 2.0, True), True),
        ((None, None, False), (7, None, False), True),
        ((7, None, False), (7, None, False), False),
    ],
)
def test_tracker_guard_classifies_generation_boundary(monkeypatch: pytest.MonkeyPatch, before, after, failed) -> None:
    """実プロセスを停止せず、世代交代・死亡・既存の死亡の帰属を判定する。"""
    snapshots = iter([before, after])
    monkeypatch.setattr(pytest_plugin, "_tracker_state", lambda: next(snapshots))
    reports = []
    item = SimpleNamespace(
        nodeid="probe",
        location=("probe.py", 1, "probe"),
        ihook=SimpleNamespace(pytest_runtest_logreport=lambda **kwargs: reports.append(kwargs["report"])),
    )
    protocol = pytest_plugin.pytest_runtest_protocol(cast(pytest.Item, item), None)
    next(protocol)
    with pytest.raises(StopIteration):
        protocol.send(None)
    assert bool(reports) is failed
    if failed:
        assert reports[0].nodeid == "probe" and reports[0].when == "teardown"
